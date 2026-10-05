//! One-shot host readiness checks (`zaribox doctor`).

use std::path::{Path, PathBuf};
use std::time::Duration;

use nix::unistd::Uid;
use serde::Serialize;

use crate::paths;
use crate::process::{self, RunOptions};
use crate::state;

/// Outcome of a single probe.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Pass,
    Fail,
    Warn,
    Info,
}

impl Status {
    fn tag(self) -> &'static str {
        match self {
            Self::Pass => "[ok]",
            Self::Fail => "[fail]",
            Self::Warn => "[warn]",
            Self::Info => "[info]",
        }
    }

    fn color(self) -> &'static str {
        match self {
            Self::Pass => crate::logging::ANSI_GRN,
            Self::Fail => crate::logging::ANSI_RED,
            Self::Warn => crate::logging::ANSI_YLW,
            Self::Info => crate::logging::ANSI_BLU,
        }
    }
}

fn check_title(id: &str) -> &str {
    match id {
        "podman_path" => "Podman on PATH",
        "podman_info" => "Podman usable",
        "rootless" => "Rootless mode",
        "cgroup" => "Cgroup support",
        "state_dir" => "State directory",
        "mcp_root" => "MCP project root",
        other => other,
    }
}

fn paint(color: &str, text: &str, colorize: bool) -> String {
    if colorize {
        format!(
            "{}{}{}{}",
            color,
            crate::logging::ANSI_BOLD,
            text,
            crate::logging::ANSI_RST
        )
    } else {
        text.to_string()
    }
}

/// One readiness check and optional remediation hint.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Check {
    pub id: &'static str,
    pub status: Status,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hint: Option<String>,
}

impl Check {
    fn pass(id: &'static str, message: impl Into<String>) -> Self {
        Self {
            id,
            status: Status::Pass,
            message: message.into(),
            hint: None,
        }
    }

    fn fail(id: &'static str, message: impl Into<String>, hint: impl Into<String>) -> Self {
        Self {
            id,
            status: Status::Fail,
            message: message.into(),
            hint: Some(hint.into()),
        }
    }

    fn warn(id: &'static str, message: impl Into<String>, hint: impl Into<String>) -> Self {
        Self {
            id,
            status: Status::Warn,
            message: message.into(),
            hint: Some(hint.into()),
        }
    }

    fn info(id: &'static str, message: impl Into<String>) -> Self {
        Self {
            id,
            status: Status::Info,
            message: message.into(),
            hint: None,
        }
    }
}

/// Aggregate doctor report (JSON `data` payload).
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Report {
    /// True when no check has [`Status::Fail`].
    pub ok: bool,
    pub checks: Vec<Check>,
}

impl Report {
    fn from_checks(checks: Vec<Check>) -> Self {
        let ok = checks.iter().all(|c| c.status != Status::Fail);
        Self { ok, checks }
    }
}

/// Hooks used by [`run`] so unit tests can avoid calling a real Podman.
pub struct Hooks {
    pub podman_on_path: Box<dyn Fn() -> bool>,
    pub podman_info: Box<dyn Fn() -> Result<PodmanInfo, String>>,
    pub uid: Box<dyn Fn() -> u32>,
    pub state_root: Box<dyn Fn() -> PathBuf>,
    pub mcp_root: Box<dyn Fn() -> Option<String>>,
    pub cgroup_fs_present: Box<dyn Fn() -> bool>,
}

/// Subset of `podman info` used by doctor.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PodmanInfo {
    pub rootless: Option<bool>,
    pub cgroup_manager: Option<String>,
    pub version: Option<String>,
}

impl Default for Hooks {
    fn default() -> Self {
        Self {
            podman_on_path: Box::new(|| process::command_exists("podman")),
            podman_info: Box::new(probe_podman_info),
            uid: Box::new(|| Uid::current().as_raw()),
            state_root: Box::new(state::state_root),
            mcp_root: Box::new(|| paths::env_nonempty("ZARIBOX_MCP_ROOT")),
            cgroup_fs_present: Box::new(|| Path::new("/sys/fs/cgroup").is_dir()),
        }
    }
}

/// Run every readiness probe.
pub fn run() -> Report {
    run_with(&Hooks::default())
}

/// Run probes with custom hooks (tests).
pub fn run_with(hooks: &Hooks) -> Report {
    let mut checks = Vec::with_capacity(6);
    checks.push(check_podman_path(&*hooks.podman_on_path));
    let info = if checks.last().is_some_and(|c| c.status == Status::Pass) {
        Some((hooks.podman_info)())
    } else {
        None
    };
    checks.push(check_podman_usable(info.as_ref()));
    checks.push(check_rootless(&*hooks.uid, info.as_ref()));
    checks.push(check_cgroup(&*hooks.cgroup_fs_present, info.as_ref()));
    checks.push(check_state_dir(&(hooks.state_root)()));
    checks.push(check_mcp_root((hooks.mcp_root)()));
    Report::from_checks(checks)
}

fn check_podman_path(exists: &dyn Fn() -> bool) -> Check {
    if exists() {
        Check::pass("podman_path", "podman is on PATH")
    } else {
        Check::fail(
            "podman_path",
            "podman was not found on PATH",
            "Install Podman and ensure `podman` is available in this shell's PATH",
        )
    }
}

fn check_podman_usable(info: Option<&Result<PodmanInfo, String>>) -> Check {
    match info {
        None => Check::fail(
            "podman_info",
            "skipped podman info because podman is missing",
            "Install Podman, then re-run `zaribox doctor`",
        ),
        Some(Ok(info)) => {
            let version = info.version.as_deref().unwrap_or("unknown");
            Check::pass(
                "podman_info",
                format!("podman info succeeded (version {version})"),
            )
        }
        Some(Err(error)) => Check::fail(
            "podman_info",
            format!("podman info failed: {error}"),
            "Run `podman info` manually and fix any socket, permission, or service errors it reports",
        ),
    }
}

fn check_rootless(uid: &dyn Fn() -> u32, info: Option<&Result<PodmanInfo, String>>) -> Check {
    let uid = uid();
    let podman_rootless = info.and_then(|r| r.as_ref().ok()).and_then(|i| i.rootless);
    if uid == 0 {
        return Check::warn(
            "rootless",
            "running as uid 0 (root); ZariBox prefers rootless Podman",
            "Run as a normal user so AgentBox keep-id / rootless defaults apply",
        );
    }
    match podman_rootless {
        Some(true) => Check::pass("rootless", format!("rootless Podman ready (uid {uid})")),
        Some(false) => Check::warn(
            "rootless",
            format!("Podman reports rootful mode while host uid is {uid}"),
            "Prefer a rootless Podman installation for AgentBox isolation",
        ),
        None => Check::pass("rootless", format!("host uid {uid} (rootless expected)")),
    }
}

fn check_cgroup(cgroup_fs: &dyn Fn() -> bool, info: Option<&Result<PodmanInfo, String>>) -> Check {
    let manager = info
        .and_then(|r| r.as_ref().ok())
        .and_then(|i| i.cgroup_manager.clone());
    if let Some(manager) = manager {
        if manager.is_empty() || manager.eq_ignore_ascii_case("none") {
            return Check::warn(
                "cgroup",
                format!("Podman cgroup manager is {manager:?}"),
                "Enable cgroup v2 (unified hierarchy) so resource limits apply inside boxes",
            );
        }
        return Check::pass("cgroup", format!("cgroup manager: {manager}"));
    }
    if cgroup_fs() {
        Check::pass("cgroup", "/sys/fs/cgroup is present")
    } else {
        Check::warn(
            "cgroup",
            "/sys/fs/cgroup is missing; resource limits may not apply",
            "Use a kernel/distro with cgroup v2 mounted at /sys/fs/cgroup",
        )
    }
}

fn check_state_dir(root: &Path) -> Check {
    match ensure_writable_dir(root) {
        Ok(()) => Check::pass(
            "state_dir",
            format!("state directory is writable ({})", root.display()),
        ),
        Err(error) => Check::fail(
            "state_dir",
            format!(
                "state directory is not writable ({}): {error}",
                root.display()
            ),
            "Fix permissions on the state path, or set ZARIBOX_STATE_HOME to a writable directory",
        ),
    }
}

fn check_mcp_root(raw: Option<String>) -> Check {
    match raw {
        None => Check::info(
            "mcp_root",
            "ZARIBOX_MCP_ROOT is unset (ok for desktop CLI use; required for zaribox-mcp)",
        ),
        Some(value) => {
            let path = paths::canonical(paths::expand(&value));
            if path.is_dir() {
                Check::pass(
                    "mcp_root",
                    format!("ZARIBOX_MCP_ROOT is a directory ({})", path.display()),
                )
            } else if path.exists() {
                Check::fail(
                    "mcp_root",
                    format!(
                        "ZARIBOX_MCP_ROOT exists but is not a directory ({})",
                        path.display()
                    ),
                    "Point ZARIBOX_MCP_ROOT at the project directory you want zaribox-mcp to manage",
                )
            } else {
                Check::fail(
                    "mcp_root",
                    format!("ZARIBOX_MCP_ROOT does not exist ({})", path.display()),
                    "Create the project directory or export ZARIBOX_MCP_ROOT to an existing path",
                )
            }
        }
    }
}

fn ensure_writable_dir(root: &Path) -> Result<(), String> {
    std::fs::create_dir_all(root).map_err(|e| e.to_string())?;
    let probe = root.join(".zaribox-doctor-write-test");
    std::fs::write(&probe, b"ok").map_err(|e| e.to_string())?;
    std::fs::remove_file(&probe).map_err(|e| e.to_string())?;
    Ok(())
}

fn probe_podman_info() -> Result<PodmanInfo, String> {
    let output = process::run(
        &[
            "podman".into(),
            "info".into(),
            "--format".into(),
            "{{.Version.Version}}|{{.Host.Security.Rootless}}|{{.Host.CgroupManager}}".into(),
        ],
        RunOptions {
            capture: true,
            timeout: Some(Duration::from_secs(15)),
            max_output: Some(64 * 1024),
        },
    )
    .map_err(|e| format!("{e:#}"))?;
    if !output.success() {
        let detail = output.stderr.trim();
        if detail.is_empty() {
            return Err(format!("exit {}", output.exit_code));
        }
        return Err(detail.to_string());
    }
    let line = output.stdout.lines().next().unwrap_or("").trim();
    let mut parts = line.splitn(3, '|');
    let version = parts.next().map(str::trim).filter(|s| !s.is_empty());
    let rootless = parts.next().and_then(|raw| match raw.trim() {
        "true" | "True" | "1" => Some(true),
        "false" | "False" | "0" => Some(false),
        _ => None,
    });
    let cgroup_manager = parts
        .next()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    Ok(PodmanInfo {
        rootless,
        cgroup_manager,
        version: version.map(str::to_string),
    })
}

/// Human-readable multi-line summary (ANSI colors when stdout is a TTY).
pub fn render(report: &Report) -> String {
    let colorize = crate::logging::stdout_color();
    let failed = report
        .checks
        .iter()
        .filter(|c| c.status == Status::Fail)
        .count();
    let warned = report
        .checks
        .iter()
        .filter(|c| c.status == Status::Warn)
        .count();

    let headline = if report.ok && warned == 0 {
        paint(
            crate::logging::ANSI_GRN,
            "ZariBox doctor -- all checks passed",
            colorize,
        )
    } else if report.ok {
        paint(
            crate::logging::ANSI_YLW,
            &format!("ZariBox doctor -- passed with {warned} warning(s)"),
            colorize,
        )
    } else {
        paint(
            crate::logging::ANSI_RED,
            &format!("ZariBox doctor -- {failed} failed, {warned} warning(s)"),
            colorize,
        )
    };

    let title_width = report
        .checks
        .iter()
        .map(|c| check_title(c.id).chars().count())
        .max()
        .unwrap_or(0);

    let mut lines = Vec::with_capacity(report.checks.len() * 2 + 2);
    lines.push(headline);
    lines.push(String::new());
    for check in &report.checks {
        let tag_plain = check.status.tag();
        let tag = paint(check.status.color(), tag_plain, colorize);
        let title = check_title(check.id);
        let pad = " ".repeat(title_width.saturating_sub(title.chars().count()));
        // Keep the status column fixed-width on the plain tag so ANSI codes do not
        // disturb alignment.
        let tag_pad = " ".repeat(6usize.saturating_sub(tag_plain.chars().count()));
        lines.push(format!(
            "  {tag}{tag_pad}  {title}{pad}  {message}",
            message = check.message
        ));
        if let Some(hint) = &check.hint {
            lines.push(format!("          -> {hint}"));
        }
    }
    lines.join("\n")
}

#[cfg(test)]
mod tests;
