//! Command-line interface.

use std::io::{BufRead, Write};
use std::process::ExitCode;
use std::time::{Duration, SystemTime};

use anyhow::{Context, Result, bail};
use clap::{Args, CommandFactory, Parser, Subcommand, error::ErrorKind};
use serde::Serialize;
use serde_json::{Value, json};

use crate::config::{Profile, StringMap};
use crate::logging::{err, log, print, set_color_enabled, set_progress_enabled, warn, warn_stderr};
use crate::service::{
    Action, BoxSummary, DEFAULT_LOCK_TIMEOUT, EnsureOptions, ExecRequest, ExecResult, Operation,
    Plan, Service, Status, Validation,
};

const AFTER_HELP: &str = "\
Common workflows:
  zaribox create archbox.yaml     Create or update a box
  zaribox enter archbox           Open an interactive shell
  zaribox status archbox          Check its current state
  zaribox exec archbox -- git status

A TARGET can be a container name or its manifest path.";

#[derive(Debug, Parser)]
#[command(
    name = "zaribox",
    version,
    about = "Create and manage reproducible Podman development containers.",
    after_help = AFTER_HELP,
    propagate_version = true
)]
pub struct Cli {
    /// Print one machine-readable JSON document instead of the human summary
    #[arg(long, global = true)]
    pub json: bool,

    /// Disable colored output
    #[arg(long, global = true)]
    pub no_color: bool,

    #[command(subcommand)]
    pub command: Option<Command>,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Check a manifest without changing anything
    #[command(after_help = "Example:\n  zaribox validate archbox.yaml")]
    Validate {
        /// YAML manifest; auto-detected in the current directory when omitted
        #[arg(value_name = "MANIFEST")]
        manifest: Option<String>,
    },

    /// Preview what create would change
    #[command(after_help = "Example:\n  zaribox plan archbox")]
    Plan {
        /// Container name or manifest; auto-detected when omitted
        target: Option<String>,
    },

    /// Create a box or bring it up to date
    #[command(after_help = "Example:\n  zaribox create archbox.yaml")]
    Create {
        /// Container name or manifest; auto-detected when omitted
        target: Option<String>,
        /// Allow package removal or other destructive changes
        #[arg(long)]
        force: bool,
        /// Rebuild the container even when already in sync
        #[arg(long)]
        recreate: bool,
        /// Maximum time to wait for another operation
        #[arg(long, value_name = "SECONDS", default_value = "30", value_parser = parse_seconds)]
        lock_timeout: Duration,
    },

    /// Show configuration and runtime state
    #[command(after_help = "Example:\n  zaribox status archbox")]
    Status {
        /// Container name or manifest
        target: String,
    },

    /// Run a non-interactive command inside a box
    #[command(after_help = "Example:\n  zaribox exec archbox -- git status")]
    Exec(ExecArgs),

    /// Open an interactive shell inside a box (desktop boxes only)
    #[command(after_help = "Example:\n  zaribox enter archbox")]
    Enter {
        /// Container name or manifest
        target: String,
    },

    /// Save manually installed packages to the manifest
    #[command(after_help = "Example:\n  zaribox export archbox")]
    Export {
        /// Container name or manifest
        target: String,
    },

    /// List managed boxes and their runtime state
    List,

    /// Remove a box while preserving its home directory
    #[command(after_help = "Example:\n  zaribox remove archbox")]
    Remove {
        /// Container name or manifest
        target: String,
        /// Skip the interactive confirmation prompt
        #[arg(long)]
        force: bool,
    },

    /// Remove expired AgentBoxes and stale operation leases
    Cleanup,
}

#[derive(Debug, Args)]
pub struct ExecArgs {
    /// Container name or manifest
    pub target: String,
    /// Terminate the command after this duration
    #[arg(long, value_name = "SECONDS", default_value = "300", value_parser = parse_seconds)]
    pub timeout: Duration,
    /// Truncate captured output beyond this size
    #[arg(long, value_name = "BYTES", default_value_t = 1024 * 1024)]
    pub max_output_bytes: usize,
    /// Working directory inside the container
    #[arg(long, value_name = "PATH")]
    pub workdir: Option<String>,
    /// Set an environment variable; may be repeated
    #[arg(long = "env", value_name = "KEY=VALUE", value_parser = parse_env)]
    pub env: Vec<(String, String)>,
    /// Run as root instead of the container user
    #[arg(long)]
    pub root: bool,
    /// Run a command string through `sh -lc`
    #[arg(long, value_name = "COMMAND", conflicts_with = "argv")]
    pub shell: Option<String>,
    /// Command and arguments (after --)
    #[arg(value_name = "COMMAND", trailing_var_arg = true)]
    pub argv: Vec<String>,
}

fn parse_seconds(raw: &str) -> Result<Duration, String> {
    raw.parse::<f64>()
        .ok()
        .and_then(|secs| Duration::try_from_secs_f64(secs).ok())
        .ok_or_else(|| format!("'{raw}' is not a non-negative number of seconds"))
}

fn parse_env(raw: &str) -> Result<(String, String), String> {
    match raw.split_once('=') {
        Some((key, value)) if !key.is_empty() => Ok((key.into(), value.into())),
        _ => Err(format!("'{raw}' is not KEY=VALUE")),
    }
}

impl Command {
    fn name(&self) -> &'static str {
        match self {
            Self::Validate { .. } => "validate",
            Self::Plan { .. } => "plan",
            Self::Create { .. } => "create",
            Self::Status { .. } => "status",
            Self::Exec(_) => "exec",
            Self::Enter { .. } => "enter",
            Self::Export { .. } => "export",
            Self::List => "list",
            Self::Remove { .. } => "remove",
            Self::Cleanup => "cleanup",
        }
    }
}

// ---------------------------------------------------------------------------
// Output

#[derive(Serialize)]
struct Envelope<'a> {
    schema_version: u32,
    ok: bool,
    command: &'a str,
    data: Value,
    error: Option<ErrorBody>,
}

#[derive(Serialize)]
struct ErrorBody {
    code: &'static str,
    message: String,
}

fn envelope(command: &str, data: Value, error: Option<(&'static str, String)>) -> String {
    serde_json::to_string(&Envelope {
        schema_version: 1,
        ok: error.is_none(),
        command,
        data,
        error: error.map(|(code, message)| ErrorBody { code, message }),
    })
    .expect("envelope serialises")
}

struct Output {
    json: bool,
    command: &'static str,
}

impl Output {
    /// Machine-readable envelope. Human mode renders each result itself.
    fn emit(&self, data: impl Serialize) -> Result<()> {
        let value = serde_json::to_value(data)?;
        let text = envelope(self.command, value, None);
        let mut stdout = std::io::stdout().lock();
        // A closed pipe (e.g. `| head`) is not an error.
        let _ = writeln!(stdout, "{text}");
        Ok(())
    }

    fn show(&self, data: &impl Serialize, human: impl FnOnce()) -> Result<()> {
        if self.json {
            self.emit(data)
        } else {
            human();
            Ok(())
        }
    }
}

fn confirm(target: &str) -> bool {
    println!("This will destroy container '{target}' (its home directory is preserved).");
    print!("  Confirm? [y/N] ");
    let _ = std::io::stdout().flush();
    let mut answer = String::new();
    std::io::stdin().lock().read_line(&mut answer).is_ok()
        && answer.trim().eq_ignore_ascii_case("y")
}

// ---------------------------------------------------------------------------
// Human summaries
//
// `--json` keeps the structs the MCP tools also return. The default path is
// for a person watching a terminal.

fn fields(title: &str, rows: &[(&str, &str)]) -> String {
    let width = rows.iter().map(|(key, _)| key.len()).max().unwrap_or(0);
    let mut lines = Vec::with_capacity(rows.len() + 1);
    if !title.is_empty() {
        lines.push(title.to_string());
    }
    for (key, value) in rows {
        lines.push(format!("  {key:<width$}  {value}"));
    }
    lines.join("\n")
}

fn join_or_none(items: &[String]) -> String {
    if items.is_empty() {
        "none".into()
    } else {
        items.join(", ")
    }
}

fn short_digest(digest: &str) -> String {
    let (prefix, hex) = digest.split_once(':').unwrap_or(("", digest));
    let shown = if hex.len() > 12 {
        format!("{}...", &hex[..12])
    } else {
        hex.to_string()
    };
    if prefix.is_empty() {
        shown
    } else {
        format!("{prefix}:{shown}")
    }
}

fn format_expiry(secs: f64) -> String {
    format_expiry_at(secs, crate::state::unix_now())
}

fn format_expiry_at(secs: f64, now: f64) -> String {
    let text = Duration::try_from_secs_f64(secs.max(0.0))
        .map(|duration| {
            humantime::format_rfc3339_seconds(SystemTime::UNIX_EPOCH + duration).to_string()
        })
        .unwrap_or_else(|_| secs.to_string());
    if secs <= now {
        format!("{text} (expired)")
    } else {
        text
    }
}

fn render_validation(result: &Validation) -> String {
    let config = result.config_path.display().to_string();
    fields(
        &format!("{}  valid", result.name),
        &[
            ("kind", result.kind),
            ("image", result.image.as_str()),
            ("profile", result.security_profile.as_str()),
            ("config", config.as_str()),
        ],
    )
}

fn render_plan(plan: &Plan) -> String {
    let mut lines = vec![format!(
        "{}  {}",
        plan.container,
        plan.config_path.display()
    )];
    for planned in &plan.actions {
        let mark = if planned.destructive {
            "  (destructive)"
        } else {
            ""
        };
        let (verb, detail) = match &planned.action {
            Action::Create { image } => ("create", image.clone()),
            Action::Recreate { .. } => ("recreate", "container identity changed".into()),
            Action::InstallPackages { packages } => ("install", packages.join(", ")),
            Action::RemovePackages { packages } => ("remove", packages.join(", ")),
            Action::RunPostInstall { count } => (
                "post-install",
                format!("{count} command{}", if *count == 1 { "" } else { "s" }),
            ),
        };
        lines.push(format!("  {verb:<12}  {detail}{mark}"));
    }
    if plan.requires_force {
        lines.push(String::new());
        lines.push("Destructive actions need --force.".into());
    }
    lines.join("\n")
}

fn render_status(status: &Status) -> String {
    let digest = status
        .image_digest
        .as_deref()
        .map(short_digest)
        .unwrap_or_else(|| "none".into());
    let mut rows = vec![
        (
            "exists",
            if status.exists { "yes" } else { "no" }.to_string(),
        ),
        (
            "in sync",
            if status.config_in_sync { "yes" } else { "no" }.to_string(),
        ),
        ("image", status.image.clone()),
        ("digest", digest),
        ("profile", status.security_profile.as_str().to_string()),
        ("packages", join_or_none(&status.desired_packages)),
        ("install", join_or_none(&status.install)),
        ("remove", join_or_none(&status.remove)),
    ];
    if let Some(at) = status.expires_at {
        rows.push(("expires", format_expiry(at)));
    }
    rows.push(("path", status.config_path.display().to_string()));
    let borrowed: Vec<(&str, &str)> = rows
        .iter()
        .map(|(key, value)| (*key, value.as_str()))
        .collect();
    fields(&status.container, &borrowed)
}

fn runtime_status(summary: &BoxSummary) -> &'static str {
    if !summary.exists {
        "missing"
    } else if summary.running {
        "running"
    } else {
        "stopped"
    }
}

fn render_list(boxes: &[BoxSummary]) -> String {
    if boxes.is_empty() {
        return "No managed boxes.".into();
    }
    boxes
        .iter()
        .map(|summary| {
            let mut rows = vec![
                ("image", summary.image.clone()),
                ("profile", summary.security_profile.as_str().to_string()),
                ("config", summary.config_path.display().to_string()),
            ];
            if let Some(at) = summary.expires_at {
                rows.push(("expires", format_expiry(at)));
            }
            let borrowed: Vec<(&str, &str)> = rows
                .iter()
                .map(|(key, value)| (*key, value.as_str()))
                .collect();
            fields(
                &format!("{}  {}", summary.name, runtime_status(summary)),
                &borrowed,
            )
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn action_phrase(action: &str) -> String {
    match action {
        "create" => "created the container".into(),
        "recreate" => "recreated the container".into(),
        "sync_packages" => "synced packages".into(),
        "run_post_install" => "ran post-install commands".into(),
        "destroy" => "removed the container (home preserved)".into(),
        other => other.replace('_', " "),
    }
}

fn operation_summary(command: &str, operation: &Operation) -> String {
    if operation.changed {
        let summary = operation
            .actions
            .iter()
            .copied()
            .map(action_phrase)
            .collect::<Vec<_>>()
            .join(", ");
        format!("'{}': {summary}", operation.container)
    } else if command == "remove" {
        format!("'{}' was already gone", operation.container)
    } else {
        format!("'{}' is already up to date", operation.container)
    }
}

fn print_operation(command: &str, operation: &Operation) {
    crate::logging::ok(&operation_summary(command, operation));
    for warning in &operation.warnings {
        warn(warning);
    }
}

fn render_export(added: &[String]) -> String {
    if added.is_empty() {
        return "No new packages to add.".into();
    }
    let label = if added.len() == 1 {
        "package"
    } else {
        "packages"
    };
    let mut lines = vec![format!("Added {} {label} to the manifest:", added.len())];
    lines.extend(added.iter().map(|pkg| format!("  {pkg}")));
    lines.join("\n")
}

fn render_cleanup(removed: &[String]) -> String {
    if removed.is_empty() {
        return "Nothing to clean up.".into();
    }
    let mut lines = vec![format!("Removed {}:", removed.len())];
    lines.extend(removed.iter().map(|name| format!("  {name}")));
    lines.join("\n")
}

fn exec_notes(result: &ExecResult) -> Vec<String> {
    let mut notes = Vec::new();
    if result.timed_out {
        notes.push("command timed out".into());
    }
    if result.truncated {
        notes.push("output truncated".into());
    }
    if result.exit_code != 0
        && result.stdout.is_empty()
        && result.stderr.is_empty()
        && !result.timed_out
    {
        notes.push(format!("command exited {}", result.exit_code));
    }
    notes
}

fn write_stream(mut stream: impl Write, text: &str) {
    let _ = stream.write_all(text.as_bytes());
    let _ = stream.flush();
}

fn print_exec(result: &ExecResult) {
    write_stream(std::io::stdout().lock(), &result.stdout);
    write_stream(std::io::stderr().lock(), &result.stderr);
    for note in exec_notes(result) {
        warn_stderr(&note);
    }
}

// ---------------------------------------------------------------------------
// Dispatch

fn run(service: &Service, command: Command, out: &Output) -> Result<u8> {
    match command {
        Command::Validate { manifest } => {
            let result = service.validate(manifest.as_deref())?;
            out.show(&result, || print(&render_validation(&result)))?;
        }
        Command::Plan { target } => {
            let plan = service.plan(target.as_deref())?;
            out.show(&plan, || {
                if plan.actions.is_empty() {
                    crate::logging::ok(&format!("'{}' is already up to date", plan.container));
                } else {
                    print(&render_plan(&plan));
                }
            })?;
        }
        Command::Create {
            target,
            force,
            recreate,
            lock_timeout,
        } => {
            let options = EnsureOptions {
                force,
                recreate,
                lock_timeout,
            };
            let operation = service.ensure(target.as_deref(), options)?;
            out.show(&operation, || print_operation("create", &operation))?;
        }
        Command::Status { target } => {
            let status = service.status(&target)?;
            out.show(&status, || print(&render_status(&status)))?;
        }
        Command::Exec(args) => {
            let argv = match args.shell {
                Some(script) => vec!["sh".into(), "-lc".into(), script],
                None => args.argv,
            };
            let request = ExecRequest {
                argv,
                timeout: args.timeout,
                max_output: args.max_output_bytes,
                as_root: args.root,
                workdir: args.workdir,
                env: args.env.into_iter().collect::<StringMap>(),
            };
            let result = service.exec(&args.target, request)?;
            let code = result.exit_code;
            if out.json {
                out.emit(&result)?;
            } else {
                print_exec(&result);
            }
            // Exit statuses are 0-255; signals are already mapped to 128+n.
            return Ok(u8::try_from(code).unwrap_or(1));
        }
        Command::Enter { target } => {
            if out.json {
                bail!("interactive enter cannot be used with --json");
            }
            return enter(service, &target);
        }
        Command::Export { target } => {
            let added = service.export(&target)?;
            out.show(&json!({ "added": &added }), || {
                print(&render_export(&added))
            })?;
        }
        Command::List => {
            let boxes = service.list()?;
            out.show(&boxes, || print(&render_list(&boxes)))?;
        }
        Command::Remove { target, force } => {
            if out.json && !force {
                bail!("remove with --json requires --force");
            }
            if !force && !confirm(&target) {
                log("Aborted.");
                return Ok(0);
            }
            let operation = service.destroy(&target, DEFAULT_LOCK_TIMEOUT)?;
            out.show(&operation, || print_operation("remove", &operation))?;
        }
        Command::Cleanup => {
            let removed = service.cleanup();
            out.show(&json!({ "removed": &removed }), || {
                print(&render_cleanup(&removed));
            })?;
        }
    }
    Ok(0)
}

fn enter(service: &Service, target: &str) -> Result<u8> {
    let manifest = service.load(Some(target))?;
    if manifest.profile() == Profile::Agent {
        bail!("interactive enter is disabled for agent-profile containers; use 'zaribox exec'");
    }
    let backend = service.backend();
    if !backend.runtime_present() {
        bail!("{} is not installed or not in PATH", backend.name());
    }
    let name = &manifest.name;
    if !backend.container_exists(name)? {
        warn(&format!(
            "Container '{name}' does not exist; run 'zaribox create' first."
        ));
        return Ok(1);
    }
    log(&format!("Entering '{name}'..."));
    let code = backend.enter(name).context("enter failed")?;
    Ok(u8::try_from(code).unwrap_or(1))
}

pub fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    let json_requested = args
        .iter()
        .skip(1)
        .take_while(|a| *a != "--")
        .any(|a| a == "--json");
    let cli = match Cli::try_parse_from(&args) {
        Ok(cli) => cli,
        Err(error)
            if json_requested
                && !matches!(
                    error.kind(),
                    ErrorKind::DisplayHelp | ErrorKind::DisplayVersion
                ) =>
        {
            let rendered = error.render().to_string();
            let message = rendered
                .lines()
                .next()
                .unwrap_or_default()
                .trim_start_matches("error: ")
                .to_string();
            println!(
                "{}",
                envelope("parse", Value::Null, Some(("invalid_arguments", message)))
            );
            return ExitCode::from(2);
        }
        Err(error) => error.exit(),
    };

    set_color_enabled(!(cli.no_color || cli.json || std::env::var_os("NO_COLOR").is_some()));
    set_progress_enabled(!cli.json);
    let Some(command) = cli.command else {
        let _ = Cli::command().print_help();
        return ExitCode::SUCCESS;
    };
    let out = Output {
        json: cli.json,
        command: command.name(),
    };
    let service = Service::default();
    match run(&service, command, &out) {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            let message = format!("{error:#}");
            if out.json {
                println!(
                    "{}",
                    envelope(
                        out.command,
                        Value::Null,
                        Some(("operation_failed", message))
                    )
                );
            } else {
                err(&message);
            }
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests;
