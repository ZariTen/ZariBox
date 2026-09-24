//! Container engine layer: builds and runs every `podman` invocation.
//!
//! Argument construction lives in pure functions so the exact command lines
//! can be unit-tested; [`PodmanBackend`] wires them to real processes. The
//! process runners, environment, and X11 socket directory are injectable so
//! tests can observe generated commands without a container runtime.

use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::fmt::Display;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result, bail, ensure};

use crate::config::{Network, Profile, Resources, StringMap};
use crate::paths;
use crate::process::{self, Output, RunOptions};

pub const X11_SOCKET_DIR: &str = "/tmp/.X11-unix";
const RUNTIME_DIR_ROOT: &str = "/run/user";
const HOST_DEVICES: &[&str] = &["/dev/dri", "/dev/kfd"];
const CREATE_MAX_OUTPUT: usize = 1024 * 1024;
const CONTAINER_XAUTHORITY: &str = "/tmp/.container_xauth";

pub const LABEL_MANAGED: &str = "io.zaribox.managed";
pub const LABEL_HOME: &str = "io.zaribox.home";
pub const LABEL_PROFILE: &str = "io.zaribox.security-profile";
pub const LABEL_PROJECT: &str = "io.zaribox.project-id";
const RESERVED_LABELS: &[&str] = &[LABEL_MANAGED, LABEL_HOME, LABEL_PROFILE];

// ---------------------------------------------------------------------------
// Environment abstraction

pub trait Env {
    fn var(&self, key: &str) -> Option<String>;

    fn var_nonempty(&self, key: &str) -> Option<String> {
        self.var(key).filter(|value| !value.trim().is_empty())
    }
}

/// The real process environment.
pub struct SystemEnv;

impl Env for SystemEnv {
    fn var(&self, key: &str) -> Option<String> {
        std::env::var(key).ok()
    }
}

impl Env for HashMap<String, String> {
    fn var(&self, key: &str) -> Option<String> {
        self.get(key).cloned()
    }
}

// ---------------------------------------------------------------------------
// Argument helpers

fn s(value: impl Into<String>) -> String {
    value.into()
}

pub fn volume_args(source: impl Display, target: impl Display, options: &str) -> [String; 2] {
    [s("--volume"), format!("{source}:{target}:{options}")]
}

pub fn env_args<'a>(env: impl IntoIterator<Item = (&'a String, &'a String)>) -> Vec<String> {
    env.into_iter()
        .flat_map(|(key, value)| [s("--env"), format!("{key}={value}")])
        .collect()
}

pub fn identity_env_args(user: &str, home_dir: &str) -> Vec<String> {
    vec![
        s("--env"),
        format!("USER={user}"),
        s("--env"),
        format!("LOGNAME={user}"),
        s("--env"),
        format!("HOME={home_dir}"),
    ]
}

/// Host user identity mirrored into containers.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HostUser {
    pub uid: u32,
    pub gid: u32,
    pub name: String,
}

impl HostUser {
    pub fn current(env: &dyn Env) -> Self {
        let uid = nix::unistd::getuid().as_raw();
        let gid = nix::unistd::getgid().as_raw();
        let name = env.var_nonempty("USER").unwrap_or_else(|| uid.to_string());
        Self { uid, gid, name }
    }

    pub fn exec_args(&self, home_dir: &str) -> Vec<String> {
        let mut args = vec![s("--user"), format!("{}:{}", self.uid, self.gid)];
        args.extend(identity_env_args(&self.name, home_dir));
        args
    }
}

/// `podman exec` invocation; options must precede the container name.
#[derive(Debug, Default)]
pub struct ExecCommand<'a> {
    pub name: &'a str,
    pub command: &'a [String],
    pub user_args: Vec<String>,
    pub workdir: Option<&'a str>,
    pub env: Option<&'a StringMap>,
    pub graphics_env: Vec<String>,
    pub interactive: bool,
}

impl ExecCommand<'_> {
    pub fn build(&self) -> Result<Vec<String>> {
        let mut args = vec![s("podman"), s("exec")];
        if self.interactive {
            args.push(s("-it"));
        }
        args.extend_from_slice(&self.user_args);
        if let Some(workdir) = self.workdir.filter(|w| !w.is_empty()) {
            ensure!(
                workdir.starts_with('/'),
                "container workdir must be an absolute path"
            );
            args.extend([s("--workdir"), s(workdir)]);
        }
        if let Some(env) = self.env {
            args.extend(env_args(env));
        }
        args.extend_from_slice(&self.graphics_env);
        args.push(s(self.name));
        args.extend_from_slice(self.command);
        Ok(args)
    }
}

pub fn login_shell_command(preferred_shell: &str) -> String {
    let shell = shlex::try_quote(preferred_shell)
        .map(|q| q.into_owned())
        .unwrap_or_else(|_| s("sh"));
    format!(
        "if command -v {shell} >/dev/null 2>&1; then exec {shell} -l; \
         elif command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh -l; fi"
    )
}

/// Add the optional SELinux relabel flag to a Podman mount.
pub fn mount_options(env: &dyn Env, options: &str) -> String {
    if env.var("ZARIBOX_PODMAN_RELABEL").as_deref() == Some("1") {
        format!("{options},z")
    } else {
        options.to_string()
    }
}

/// Parse `source<TAB>destination` lines from `podman inspect`.
pub fn parse_mounts(output: &str) -> Vec<(PathBuf, PathBuf)> {
    output
        .lines()
        .filter_map(|line| line.split_once('\t'))
        .map(|(source, destination)| (PathBuf::from(source), PathBuf::from(destination)))
        .filter(|(source, destination)| source.is_absolute() && destination.is_absolute())
        .collect()
}

/// Translate a host directory through the most specific bind mount.
pub fn mounted_workdir(host_dir: &Path, mounts: &[(PathBuf, PathBuf)]) -> Option<String> {
    let host_dir = paths::canonical(host_dir);
    mounts
        .iter()
        .filter_map(|(source, destination)| {
            let source = paths::canonical(source);
            let relative = host_dir.strip_prefix(&source).ok()?;
            Some((source.components().count(), destination.join(relative)))
        })
        .max_by_key(|(depth, _)| *depth)
        .map(|(_, dir)| {
            let text = dir.to_string_lossy().into_owned();
            match text.trim_end_matches('/') {
                "" => s("/"),
                trimmed => s(trimmed),
            }
        })
}

pub const MACHINE_ID_SETUP: &str = "\
if [ ! -s /etc/machine-id ]; then
    if command -v systemd-machine-id-setup >/dev/null 2>&1; then
        systemd-machine-id-setup >/dev/null 2>&1 || true
    fi
    if [ ! -s /etc/machine-id ] && [ -r /proc/sys/kernel/random/uuid ]; then
        tr -d '-' < /proc/sys/kernel/random/uuid > /etc/machine-id 2>/dev/null || true
    fi
fi
";

fn quote(text: &str) -> String {
    shlex::try_quote(text)
        .map(|q| q.into_owned())
        .unwrap_or_else(|_| format!("'{}'", text.replace('\0', "")))
}

pub fn user_setup_script(user: &HostUser, home_dir: &str, passwordless_sudo: bool) -> String {
    let HostUser { uid, gid, name } = user;
    let name = quote(name);
    let home = quote(home_dir);
    let mut script = format!(
        "getent group {gid} >/dev/null 2>&1 ||
    groupadd -g {gid} {name} 2>/dev/null ||
    addgroup -g {gid} {name}
getent passwd {uid} >/dev/null 2>&1 ||
    useradd -M -d {home} -u {uid} -g {gid} {name} 2>/dev/null ||
    adduser -H -h {home} -u {uid} -G {name} -D {name}
"
    );
    if passwordless_sudo {
        script.push_str(&format!(
            "mkdir -p /etc/sudoers.d
printf '%s ALL=(ALL:ALL) NOPASSWD:ALL\\n' {name} > /etc/sudoers.d/90-zaribox-user
chmod 0440 /etc/sudoers.d/90-zaribox-user
"
        ));
    }
    script
}

// ---------------------------------------------------------------------------
// Graphics / desktop session integration

/// Host locations consulted for display, audio, and D-Bus forwarding.
pub struct GraphicsHost<'a> {
    pub env: &'a dyn Env,
    pub x11_dir: &'a Path,
    pub uid: u32,
}

impl GraphicsHost<'_> {
    fn runtime_directory(&self) -> Option<PathBuf> {
        let dir = match self.env.var_nonempty("XDG_RUNTIME_DIR") {
            Some(configured) => paths::expand(configured.trim()),
            None => Path::new(RUNTIME_DIR_ROOT).join(self.uid.to_string()),
        };
        dir.is_dir().then_some(dir)
    }

    fn only_socket(dir: &Path, matches: impl Fn(&str) -> bool) -> Option<PathBuf> {
        let sockets: Vec<PathBuf> = paths::list_dir(dir, matches)
            .into_iter()
            .filter(|p| paths::is_socket(p))
            .collect();
        match <[PathBuf; 1]>::try_from(sockets) {
            Ok([only]) => Some(only),
            Err(_) => None,
        }
    }

    fn display(&self) -> Option<String> {
        if let Some(display) = self.env.var_nonempty("DISPLAY") {
            return Some(display.trim().to_string());
        }
        let socket = Self::only_socket(self.x11_dir, |name| {
            name.strip_prefix('X')
                .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
        })?;
        Some(format!(":{}", &socket.file_name()?.to_string_lossy()[1..]))
    }

    fn wayland_display(&self, runtime_dir: Option<&Path>) -> Option<String> {
        if let Some(configured) = self.env.var_nonempty("WAYLAND_DISPLAY") {
            let configured = configured.trim().to_string();
            let socket = paths::expand(&configured);
            let socket = if socket.is_absolute() {
                socket
            } else {
                runtime_dir?.join(socket)
            };
            return paths::is_socket(socket).then_some(configured);
        }
        let socket = Self::only_socket(runtime_dir?, |name| name.starts_with("wayland-"))?;
        Some(socket.file_name()?.to_string_lossy().into_owned())
    }

    fn config_dir(&self) -> PathBuf {
        match self.env.var_nonempty("XDG_CONFIG_HOME") {
            Some(dir) => PathBuf::from(dir),
            None => self
                .env
                .var_nonempty("HOME")
                .map(PathBuf::from)
                .unwrap_or_else(paths::home_dir)
                .join(".config"),
        }
    }

    pub fn host_xauthority(&self) -> Option<PathBuf> {
        let path = match self.env.var_nonempty("XAUTHORITY") {
            Some(path) => paths::expand(&path),
            None => PathBuf::from(self.env.var_nonempty("HOME")?).join(".Xauthority"),
        };
        path.is_file().then_some(path)
    }

    /// Per-container copy of the X authority cookie, mounted read-only.
    pub fn xauthority_path(&self, name: &str) -> PathBuf {
        self.config_dir().join("zaribox").join(name).join("xauth")
    }

    fn copy_xauthority(source: &Path, target: &Path) -> Result<()> {
        (|| -> std::io::Result<()> {
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(target, std::fs::read(source)?)?;
            std::fs::set_permissions(target, std::fs::Permissions::from_mode(0o600))
        })()
        .with_context(|| format!("cannot copy Xauthority file {}", source.display()))
    }

    pub fn persist_xauthority(&self, name: &str) -> Result<Option<PathBuf>> {
        let Some(source) = self.host_xauthority() else {
            return Ok(None);
        };
        let target = self.xauthority_path(name);
        Self::copy_xauthority(&source, &target)?;
        Ok(Some(target))
    }

    /// Refresh a persisted cookie so a new X session keeps working.
    pub fn refresh_xauthority(&self, name: &str) -> Result<()> {
        let target = self.xauthority_path(name);
        match self.host_xauthority() {
            Some(source)
                if target.is_file() && paths::canonical(&source) != paths::canonical(&target) =>
            {
                Self::copy_xauthority(&source, &target)
            }
            _ => Ok(()),
        }
    }

    /// Display environment forwarded to `podman exec`.
    pub fn exec_env(&self) -> Vec<String> {
        let runtime_dir = self.runtime_directory();
        let display = self.display().unwrap_or_default();
        let wayland = self
            .wayland_display(runtime_dir.as_deref())
            .unwrap_or_default();
        let session = self
            .env
            .var_nonempty("XDG_SESSION_TYPE")
            .map(|t| t.trim().to_string())
            .unwrap_or_else(|| {
                if !wayland.is_empty() {
                    s("wayland")
                } else if !display.is_empty() {
                    s("x11")
                } else {
                    String::new()
                }
            });
        let mut args = env_args(&StringMap::from([
            (s("DISPLAY"), display),
            (s("WAYLAND_DISPLAY"), wayland),
            (s("XDG_SESSION_TYPE"), session),
        ]));
        if let Some(dir) = runtime_dir {
            args.extend([s("--env"), format!("XDG_RUNTIME_DIR={}", dir.display())]);
        }
        args
    }

    /// Graphics, audio, and session flags for `podman create`.
    pub fn create_args(&self, name: &str) -> Result<Vec<String>> {
        let mut args = Vec::new();
        let rw_rslave = mount_options(self.env, "rw,rslave");
        let ro_rslave = mount_options(self.env, "ro,rslave");
        if self.display().is_some() {
            if self.x11_dir.is_dir() {
                let dir = self.x11_dir.display();
                args.extend(volume_args(&dir, &dir, &ro_rslave));
            }
            if let Some(xauth) = self.persist_xauthority(name)? {
                args.extend([s("--env"), format!("XAUTHORITY={CONTAINER_XAUTHORITY}")]);
                args.extend(volume_args(
                    xauth.display(),
                    CONTAINER_XAUTHORITY,
                    &mount_options(self.env, "ro"),
                ));
            }
        }
        if let Some(runtime_dir) = self.runtime_directory() {
            let dir = runtime_dir.display();
            args.extend([s("--env"), format!("XDG_RUNTIME_DIR={dir}")]);
            args.extend(volume_args(&dir, &dir, &rw_rslave));
            let bus = runtime_dir.join("bus");
            if paths::is_socket(&bus) {
                args.extend([
                    s("--env"),
                    format!("DBUS_SESSION_BUS_ADDRESS=unix:path={}", bus.display()),
                ]);
            } else if let Some(address) = self.env.var_nonempty("DBUS_SESSION_BUS_ADDRESS") {
                args.extend([s("--env"), format!("DBUS_SESSION_BUS_ADDRESS={address}")]);
            }
            let pulse = runtime_dir.join("pulse");
            if pulse.is_dir() {
                let pulse_dir = pulse.display();
                args.extend(volume_args(&pulse_dir, &pulse_dir, &rw_rslave));
                if let Some(server) = self.env.var_nonempty("PULSE_SERVER") {
                    args.extend([s("--env"), format!("PULSE_SERVER={server}")]);
                } else if paths::is_socket(pulse.join("native")) {
                    args.extend([s("--env"), format!("PULSE_SERVER=unix:{pulse_dir}/native")]);
                }
            }
        }
        for device in HOST_DEVICES.iter().filter(|d| Path::new(d).exists()) {
            args.extend([s("--device"), s(*device)]);
        }
        if Path::new("/etc/localtime").exists() {
            args.extend(volume_args("/etc/localtime", "/etc/localtime", "ro"));
        }
        Ok(args)
    }
}

// ---------------------------------------------------------------------------
// Create policy

#[derive(Clone, Debug, PartialEq)]
pub struct MountSpec {
    pub source: PathBuf,
    pub target: String,
    pub options: String,
}

/// Structured `podman create` options.
#[derive(Clone, Debug, PartialEq)]
pub struct CreatePolicy {
    pub profile: Profile,
    pub network: Network,
    pub mounts: Vec<MountSpec>,
    pub env: StringMap,
    pub workdir: Option<String>,
    pub resources: Resources,
    pub read_only_root: bool,
    pub writable_tmpfs: Vec<String>,
    pub labels: StringMap,
    pub extra_flags: Vec<String>,
    pub timeout: Option<Duration>,
}

impl Default for CreatePolicy {
    fn default() -> Self {
        Self {
            profile: Profile::Default,
            network: Network::Host,
            mounts: Vec::new(),
            env: StringMap::new(),
            workdir: None,
            resources: Resources::default(),
            read_only_root: false,
            writable_tmpfs: Vec::new(),
            labels: StringMap::new(),
            extra_flags: Vec::new(),
            timeout: None,
        }
    }
}

impl CreatePolicy {
    pub fn is_agent(&self) -> bool {
        self.profile == Profile::Agent
    }

    /// Defence in depth: the manifest layer already rejects these.
    pub fn validate(&self) -> Result<()> {
        if self.is_agent() {
            ensure!(
                self.extra_flags.is_empty(),
                "extra flags are not allowed with the agent security profile"
            );
            ensure!(
                self.network != Network::Host,
                "host networking is not allowed in agent mode"
            );
        }
        if let Some(label) = self
            .labels
            .keys()
            .find(|k| RESERVED_LABELS.contains(&k.as_str()))
        {
            bail!("reserved container label: {label}");
        }
        Ok(())
    }
}

/// Host facts that feed into `podman create`.
pub struct CreateHost<'a> {
    pub user: &'a HostUser,
    pub host_home: &'a str,
    pub rootless: bool,
    pub env: &'a dyn Env,
}

/// Everything `podman create` needs besides the host.
pub struct CreateRequest<'a> {
    pub name: &'a str,
    pub image: &'a str,
    pub home_dir: &'a str,
    pub home_mount: bool,
    pub policy: &'a CreatePolicy,
}

/// Assemble the full `podman create` command line.
pub fn build_create_command(
    request: &CreateRequest<'_>,
    host: &CreateHost<'_>,
    graphics: impl FnOnce() -> Result<Vec<String>>,
) -> Result<Vec<String>> {
    let CreateRequest {
        name,
        image,
        home_dir,
        home_mount,
        policy,
    } = *request;
    policy.validate()?;
    let agent = policy.is_agent();
    let mut args = vec![
        s("podman"),
        s("create"),
        s("--name"),
        s(name),
        s("--hostname"),
        s(name),
        s("--label"),
        format!("{LABEL_MANAGED}=true"),
        s("--label"),
        format!("{LABEL_HOME}={home_dir}"),
        s("--label"),
        format!("{LABEL_PROFILE}={}", policy.profile.as_str()),
        s("--network"),
        s(policy.network.as_str()),
        s("--ipc"),
        s(if agent { "private" } else { "host" }),
    ];
    args.extend(identity_env_args(&host.user.name, home_dir));
    args.extend([
        s("--workdir"),
        policy.workdir.clone().unwrap_or_else(|| s(home_dir)),
    ]);
    args.extend(volume_args(
        home_dir,
        home_dir,
        &mount_options(host.env, "rslave"),
    ));
    if agent {
        args.extend([
            s("--cap-drop"),
            s("all"),
            s("--security-opt"),
            s("no-new-privileges"),
        ]);
    } else {
        args.extend([s("--security-opt"), s("label=disable")]);
    }
    if policy.read_only_root {
        args.push(s("--read-only"));
    }
    for tmpfs in &policy.writable_tmpfs {
        args.extend([s("--tmpfs"), tmpfs.clone()]);
    }
    let Resources {
        cpus,
        memory,
        pids_limit,
    } = &policy.resources;
    if let Some(cpus) = cpus {
        args.extend([s("--cpus"), cpus.to_string()]);
    }
    if let Some(memory) = memory {
        args.extend([s("--memory"), memory.clone()]);
    }
    if let Some(pids) = pids_limit {
        args.extend([s("--pids-limit"), pids.to_string()]);
    }
    for mount in &policy.mounts {
        args.extend(volume_args(
            mount.source.display(),
            &mount.target,
            &mount_options(host.env, &mount.options),
        ));
    }
    args.extend(env_args(&policy.env));
    for (key, value) in &policy.labels {
        args.extend([s("--label"), format!("{key}={value}")]);
    }
    if home_mount && host.host_home != home_dir {
        let home = host.host_home;
        args.extend(volume_args(home, home, &mount_options(host.env, "rw")));
    }
    if host.rootless {
        args.extend([s("--userns"), s("keep-id")]);
    }
    if let Some(term) = host.env.var_nonempty("TERM") {
        args.extend([s("--env"), format!("TERM={term}")]);
    }
    if !agent {
        args.extend(graphics()?);
    }
    args.extend(policy.extra_flags.iter().cloned());
    args.extend([s(image), s("sleep"), s("infinity")]);
    Ok(args)
}

// ---------------------------------------------------------------------------
// Backend

#[derive(Clone, Debug, Default)]
pub struct ExecOptions {
    /// Run as the mirrored host user instead of root.
    pub as_user: bool,
    /// Fail on a non-zero exit status.
    pub check: bool,
    pub timeout: Option<Duration>,
    pub max_output: Option<usize>,
    /// Known profile; looked up from container labels when `None`.
    pub agent: Option<bool>,
    pub workdir: Option<String>,
    pub env: Option<StringMap>,
}

/// Operations the service layer needs from a container runtime.
pub trait Backend {
    fn name(&self) -> &'static str {
        "podman"
    }
    fn runtime_present(&self) -> bool;
    fn container_exists(&self, name: &str) -> Result<bool>;
    fn create(&self, request: &CreateRequest<'_>) -> Result<()>;
    fn exec(&self, name: &str, command: &[String], options: &ExecOptions) -> Result<Output>;
    fn enter(&self, name: &str) -> Result<i32>;
    fn post_install(&self, name: &str, home_dir: &str) -> Result<()>;
    fn start(&self, name: &str) -> Result<()>;
    fn stop(&self, name: &str) -> Result<()>;
    fn remove(&self, name: &str) -> Result<()>;
    fn rename(&self, name: &str, new_name: &str) -> Result<()>;
    fn label(&self, name: &str, key: &str) -> Result<Option<String>>;
    fn image_digest(&self, name: &str) -> Result<Option<String>>;
    fn is_running(&self, name: &str) -> Result<bool>;
}

pub type Runner = dyn Fn(&[String], RunOptions) -> Result<Output>;
pub type InteractiveRunner = dyn Fn(&[String]) -> Result<i32>;

fn check(output: &Output, context: &str) -> Result<()> {
    if output.success() {
        return Ok(());
    }
    match output.stderr.trim() {
        "" => bail!("{context} failed (exit status {})", output.exit_code),
        stderr => bail!("{context} failed: {stderr}"),
    }
}

fn run_interactive(args: &[String]) -> Result<i32> {
    use std::os::unix::process::ExitStatusExt;
    let (program, rest) = args.split_first().context("empty command")?;
    let status = std::process::Command::new(program)
        .args(rest)
        .status()
        .with_context(|| format!("failed to run {program}"))?;
    Ok(status
        .code()
        .or_else(|| status.signal().map(|signal| 128 + signal))
        .unwrap_or(1))
}

pub struct PodmanBackend {
    runner: Box<Runner>,
    interactive: Box<InteractiveRunner>,
    env: Box<dyn Env>,
    x11_dir: PathBuf,
    runtime_override: Option<bool>,
    runtime_seen: Cell<Option<bool>>,
    home_cache: RefCell<HashMap<String, String>>,
    agent_cache: RefCell<HashMap<String, bool>>,
}

impl Default for PodmanBackend {
    fn default() -> Self {
        Self::new()
    }
}

impl PodmanBackend {
    pub fn new() -> Self {
        Self {
            runner: Box::new(process::run),
            interactive: Box::new(run_interactive),
            env: Box::new(SystemEnv),
            x11_dir: PathBuf::from(X11_SOCKET_DIR),
            runtime_override: None,
            runtime_seen: Cell::new(None),
            home_cache: RefCell::default(),
            agent_cache: RefCell::default(),
        }
    }

    /// Backend with injected process runners and environment (for tests).
    pub fn with_hooks(
        runner: Box<Runner>,
        interactive: Box<InteractiveRunner>,
        env: Box<dyn Env>,
        x11_dir: PathBuf,
    ) -> Self {
        Self {
            runner,
            interactive,
            env,
            x11_dir,
            runtime_override: Some(true),
            ..Self::new()
        }
    }

    fn user(&self) -> HostUser {
        HostUser::current(self.env.as_ref())
    }

    fn graphics(&self) -> GraphicsHost<'_> {
        GraphicsHost {
            env: self.env.as_ref(),
            x11_dir: &self.x11_dir,
            uid: self.user().uid,
        }
    }

    fn host_home(&self) -> Option<String> {
        self.env
            .var_nonempty("HOME")
            .map(|home| home.trim_end_matches('/').to_string())
    }

    fn run(&self, args: &[String]) -> Result<Output> {
        (self.runner)(args, RunOptions::default())
    }

    fn podman(&self, args: &[&str]) -> Result<Output> {
        let command: Vec<String> = std::iter::once("podman")
            .chain(args.iter().copied())
            .map(s)
            .collect();
        let output = self.run(&command)?;
        check(&output, &format!("podman {}", args[0]))?;
        Ok(output)
    }

    fn inspect(&self, name: &str, format: &str) -> Result<Option<String>> {
        let output = self.run(&[s("podman"), s("inspect"), s("--format"), s(format), s(name)])?;
        Ok(output.success().then(|| output.stdout.trim().to_string()))
    }

    fn container_home(&self, name: &str) -> Result<String> {
        if let Some(home) = self.home_cache.borrow().get(name) {
            return Ok(home.clone());
        }
        let home = self.label(name, LABEL_HOME)?.unwrap_or_default();
        self.home_cache.borrow_mut().insert(s(name), home.clone());
        Ok(home)
    }

    fn container_mounts(&self, name: &str) -> Result<Vec<(PathBuf, PathBuf)>> {
        let output = self.inspect(
            name,
            "{{range .Mounts}}{{.Source}}\t{{.Destination}}\n{{end}}",
        )?;
        Ok(output.map(|o| parse_mounts(&o)).unwrap_or_default())
    }

    fn is_agent(&self, name: &str, known: Option<bool>) -> Result<bool> {
        if let Some(agent) = known.or_else(|| self.agent_cache.borrow().get(name).copied()) {
            return Ok(agent);
        }
        let agent = self.label(name, LABEL_PROFILE)?.as_deref() == Some(Profile::Agent.as_str());
        self.agent_cache.borrow_mut().insert(s(name), agent);
        Ok(agent)
    }

    /// Start the container (refreshing X credentials) and ensure a machine id.
    pub fn start_with(&self, name: &str, agent: Option<bool>) -> Result<()> {
        if !self.is_agent(name, agent)? {
            self.graphics().refresh_xauthority(name)?;
        }
        self.podman(&["start", name])?;
        let output = self.root_shell(name, MACHINE_ID_SETUP)?;
        check(&output, &format!("initializing the machine id in '{name}'"))
    }

    fn root_shell(&self, name: &str, script: &str) -> Result<Output> {
        self.run(&[
            s("podman"),
            s("exec"),
            s("--user"),
            s("0"),
            s(name),
            s("sh"),
            s("-c"),
            s(script),
        ])
    }

    fn user_exists(&self, name: &str, uid: u32) -> Result<bool> {
        Ok(self
            .root_shell(name, &format!("getent passwd {uid}"))?
            .success())
    }

    fn ensure_user(&self, name: &str, home_dir: &str, passwordless_sudo: bool) -> Result<()> {
        self.start_with(name, None)?;
        let script = user_setup_script(&self.user(), home_dir, passwordless_sudo);
        let output = self.root_shell(name, &script)?;
        check(&output, &format!("initializing the user inside '{name}'"))
    }
}

impl Backend for PodmanBackend {
    fn runtime_present(&self) -> bool {
        if let Some(present) = self.runtime_override {
            return present;
        }
        *self
            .runtime_seen
            .get()
            .get_or_insert_with(|| process::command_exists("podman"))
    }

    fn container_exists(&self, name: &str) -> Result<bool> {
        if !self.runtime_present() {
            return Ok(false);
        }
        Ok(self
            .run(&[s("podman"), s("container"), s("exists"), s(name)])?
            .success())
    }

    fn create(&self, request: &CreateRequest<'_>) -> Result<()> {
        let home_dir = request.home_dir.trim_end_matches('/');
        std::fs::create_dir_all(home_dir)
            .with_context(|| format!("cannot create home directory {home_dir}"))?;
        let user = self.user();
        let host_home = self
            .host_home()
            .unwrap_or_else(|| format!("/home/{}", user.name));
        let host = CreateHost {
            user: &user,
            host_home: &host_home,
            rootless: user.uid != 0,
            env: self.env.as_ref(),
        };
        let request = CreateRequest {
            home_dir,
            ..*request
        };
        let args = build_create_command(&request, &host, || {
            self.graphics().create_args(request.name)
        })?;
        let timeout = request.policy.timeout;
        let options = RunOptions {
            capture: true,
            timeout,
            max_output: timeout.map(|_| CREATE_MAX_OUTPUT),
        };
        check(&(self.runner)(&args, options)?, "podman create")?;

        let agent = request.policy.is_agent();
        self.agent_cache.borrow_mut().insert(s(request.name), agent);
        self.start_with(request.name, Some(agent))?;
        if !(agent && request.policy.read_only_root) {
            self.ensure_user(request.name, home_dir, !agent)?;
        }
        Ok(())
    }

    fn exec(&self, name: &str, command: &[String], options: &ExecOptions) -> Result<Output> {
        let agent = self.is_agent(name, options.agent)?;
        self.start_with(name, Some(agent))?;
        let user_args = if options.as_user {
            self.user().exec_args(&self.container_home(name)?)
        } else {
            vec![s("--user"), s("0")]
        };
        let args = ExecCommand {
            name,
            command,
            user_args,
            workdir: options.workdir.as_deref(),
            env: options.env.as_ref(),
            graphics_env: if agent {
                Vec::new()
            } else {
                self.graphics().exec_env()
            },
            interactive: false,
        }
        .build()?;
        let output = (self.runner)(
            &args,
            RunOptions {
                capture: true,
                timeout: options.timeout,
                max_output: options.max_output,
            },
        )?;
        if options.check {
            check(&output, "podman exec")?;
        }
        Ok(output)
    }

    fn enter(&self, name: &str) -> Result<i32> {
        let shell = self
            .env
            .var_nonempty("SHELL")
            .unwrap_or_else(|| s("/bin/sh"));
        let preferred = Path::new(&shell)
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| s("sh"));
        let user = self.user();
        let mut home_dir = self.container_home(name)?;
        if home_dir.is_empty() {
            home_dir = self.host_home().unwrap_or_else(|| s("/"));
        }
        self.start_with(name, None)?;
        if !self.user_exists(name, user.uid)? {
            let agent = self.is_agent(name, None)?;
            self.ensure_user(name, &home_dir, !agent)?;
        }
        let cwd = std::env::current_dir().context("cannot determine the current directory")?;
        let workdir = mounted_workdir(&cwd, &self.container_mounts(name)?);
        let command = [s("sh"), s("-lc"), login_shell_command(&preferred)];
        let args = ExecCommand {
            name,
            command: &command,
            user_args: user.exec_args(&home_dir),
            workdir: workdir.as_deref(),
            env: None,
            graphics_env: self.graphics().exec_env(),
            interactive: true,
        }
        .build()?;
        (self.interactive)(&args)
    }

    fn post_install(&self, name: &str, home_dir: &str) -> Result<()> {
        let user = self.user();
        let target = home_dir.trim_end_matches('/');
        if target.is_empty() || Some(target) == self.host_home().as_deref() || user.uid != 0 {
            return Ok(());
        }
        let script = format!("chown {}:{} {}", user.uid, user.gid, quote(target));
        self.root_shell(name, &script)?;
        Ok(())
    }

    fn start(&self, name: &str) -> Result<()> {
        self.start_with(name, None)
    }

    fn stop(&self, name: &str) -> Result<()> {
        self.podman(&["stop", name]).map(drop)
    }

    fn remove(&self, name: &str) -> Result<()> {
        self.podman(&["rm", "-f", name]).map(drop)
    }

    fn rename(&self, name: &str, new_name: &str) -> Result<()> {
        self.podman(&["rename", name, new_name])?;
        let home = self.home_cache.borrow_mut().remove(name);
        if let Some(home) = home {
            self.home_cache.borrow_mut().insert(s(new_name), home);
        }
        let agent = self.agent_cache.borrow_mut().remove(name);
        if let Some(agent) = agent {
            self.agent_cache.borrow_mut().insert(s(new_name), agent);
        }
        Ok(())
    }

    fn label(&self, name: &str, key: &str) -> Result<Option<String>> {
        Ok(self
            .inspect(name, &format!("{{{{ index .Config.Labels \"{key}\" }}}}"))?
            .filter(|value| !value.is_empty() && value != "<no value>"))
    }

    fn image_digest(&self, name: &str) -> Result<Option<String>> {
        Ok(self
            .inspect(name, "{{.ImageDigest}}")?
            .filter(|v| !v.is_empty()))
    }

    fn is_running(&self, name: &str) -> Result<bool> {
        Ok(self
            .inspect(name, "{{.State.Running}}")?
            .is_some_and(|state| state.eq_ignore_ascii_case("true")))
    }
}

#[cfg(test)]
mod tests;
