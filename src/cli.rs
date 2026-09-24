//! Command-line interface.

use std::io::{BufRead, Write};
use std::process::ExitCode;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use clap::{Args, CommandFactory, Parser, Subcommand, error::ErrorKind};
use serde::Serialize;
use serde_json::{Value, json};

use crate::config::{Profile, StringMap};
use crate::logging::{err, log, set_color_enabled, warn};
use crate::service::{DEFAULT_LOCK_TIMEOUT, EnsureOptions, ExecRequest, Service};

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
    /// Print one machine-readable JSON document
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
    fn emit(&self, data: impl Serialize) -> Result<()> {
        let value = serde_json::to_value(data)?;
        let text = if self.json {
            envelope(self.command, value, None)
        } else {
            serde_json::to_string_pretty(&value)?
        };
        let mut stdout = std::io::stdout().lock();
        // A closed pipe (e.g. `| head`) is not an error.
        let _ = writeln!(stdout, "{text}");
        Ok(())
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
// Dispatch

fn run(service: &Service, command: Command, out: &Output) -> Result<u8> {
    match command {
        Command::Validate { manifest } => out.emit(service.validate(manifest.as_deref())?)?,
        Command::Plan { target } => out.emit(service.plan(target.as_deref())?)?,
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
            out.emit(service.ensure(target.as_deref(), options)?)?;
        }
        Command::Status { target } => out.emit(service.status(&target)?)?,
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
            out.emit(result)?;
            // Exit statuses are 0-255; signals are already mapped to 128+n.
            return Ok(u8::try_from(code).unwrap_or(1));
        }
        Command::Enter { target } => {
            if out.json {
                bail!("interactive enter cannot be used with --json");
            }
            return enter(service, &target);
        }
        Command::Export { target } => out.emit(json!({ "added": service.export(&target)? }))?,
        Command::List => out.emit(service.list()?)?,
        Command::Remove { target, force } => {
            if out.json && !force {
                bail!("remove with --json requires --force");
            }
            if !force && !confirm(&target) {
                log("Aborted.");
                return Ok(0);
            }
            out.emit(service.destroy(&target, DEFAULT_LOCK_TIMEOUT)?)?;
        }
        Command::Cleanup => out.emit(json!({ "removed": service.cleanup() }))?,
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
mod tests {
    use super::*;

    fn parse(args: &[&str]) -> Result<Cli, clap::Error> {
        Cli::try_parse_from(std::iter::once("zaribox").chain(args.iter().copied()))
    }

    #[test]
    fn cli_definition_is_consistent() {
        Cli::command().debug_assert();
    }

    #[test]
    fn exec_arguments() {
        let cli = parse(&[
            "exec",
            "box",
            "--timeout",
            "2.5",
            "--env",
            "A=b=c",
            "--json",
            "--",
            "ls",
            "--json",
        ])
        .unwrap();
        assert!(cli.json);
        let Some(Command::Exec(args)) = cli.command else {
            panic!()
        };
        assert_eq!(args.timeout, Duration::from_millis(2500));
        assert_eq!(args.env, [("A".to_string(), "b=c".to_string())]);
        assert_eq!(args.argv, ["ls", "--json"]);

        assert!(parse(&["exec", "box", "--shell", "ls", "--", "ls"]).is_err());
        assert!(parse(&["exec", "box", "--env", "=x"]).is_err());
        assert!(parse(&["exec", "box", "--timeout", "-1"]).is_err());
        assert!(parse(&["exec", "box", "--max-output-bytes", "-1"]).is_err());
    }

    #[test]
    fn globals_anywhere() {
        let cli = parse(&["--no-color", "create", "box.yaml", "--force", "--json"]).unwrap();
        assert!(cli.json && cli.no_color);
        let Some(Command::Create {
            force,
            lock_timeout,
            ..
        }) = cli.command
        else {
            panic!()
        };
        assert!(force);
        assert_eq!(lock_timeout, Duration::from_secs(30));
        assert!(parse(&[]).unwrap().command.is_none());
    }

    #[test]
    fn envelope_shape() {
        let text = envelope("list", json!([]), None);
        assert_eq!(
            text,
            r#"{"schema_version":1,"ok":true,"command":"list","data":[],"error":null}"#
        );
        let error: Value = serde_json::from_str(&envelope(
            "x",
            Value::Null,
            Some(("operation_failed", "boom".into())),
        ))
        .unwrap();
        assert_eq!(error["ok"], false);
        assert_eq!(error["error"]["message"], "boom");
    }
}
