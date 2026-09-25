//! Orchestration shared by the CLI and the MCP server: validation, planning,
//! reconciliation, execution, and removal of managed boxes.

use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result, bail, ensure};
use serde::Serialize;

use crate::config::{self, Manifest, Profile, StringMap};
use crate::engine::{
    Backend, CreatePolicy, CreateRequest, ExecOptions, LABEL_MANAGED, LABEL_PROJECT, MountSpec,
    PodmanBackend,
};
use crate::paths;
use crate::pkgmgr::{self, PackageManager};
use crate::state::{self, ProjectRecord, ProjectStore};

pub const DEFAULT_LOCK_TIMEOUT: Duration = Duration::from_secs(30);
const PROVISION_OUTPUT_LIMIT: usize = 4 * 1024 * 1024;
const DEFAULT_PROVISION_TIMEOUT: Duration = Duration::from_secs(900);
const READ_ONLY_TMPFS: &str = "/tmp:rw,nosuid,nodev,size=256m";

// ---------------------------------------------------------------------------
// Results

#[derive(Debug, Serialize)]
pub struct Validation {
    pub valid: bool,
    pub config_path: PathBuf,
    pub name: String,
    pub image: String,
    pub kind: &'static str,
    pub security_profile: Profile,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "kind", content = "details", rename_all = "snake_case")]
pub enum Action {
    Create { image: String },
    Recreate { from: Option<String>, to: String },
    InstallPackages { packages: Vec<String> },
    RemovePackages { packages: Vec<String> },
    RunPostInstall { count: usize },
}

impl Action {
    pub fn destructive(&self) -> bool {
        matches!(self, Self::Recreate { .. } | Self::RemovePackages { .. })
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct PlannedAction {
    #[serde(flatten)]
    pub action: Action,
    pub destructive: bool,
}

#[derive(Debug, Serialize)]
pub struct Plan {
    pub project_id: String,
    pub container: String,
    pub config_path: PathBuf,
    pub actions: Vec<PlannedAction>,
    pub requires_force: bool,
    pub identity_digest: String,
}

impl Plan {
    fn has(&self, predicate: impl Fn(&Action) -> bool) -> bool {
        self.actions.iter().any(|a| predicate(&a.action))
    }
}

#[derive(Debug, Serialize)]
pub struct Status {
    pub project_id: String,
    pub container: String,
    pub config_path: PathBuf,
    pub exists: bool,
    pub config_in_sync: bool,
    pub desired_packages: Vec<String>,
    pub applied_packages: Vec<String>,
    pub install: Vec<String>,
    pub remove: Vec<String>,
    pub image: String,
    pub image_digest: Option<String>,
    pub security_profile: Profile,
    pub expires_at: Option<f64>,
}

#[derive(Debug, Serialize)]
pub struct Operation {
    pub operation_id: String,
    pub changed: bool,
    pub container: String,
    pub actions: Vec<&'static str>,
    pub warnings: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct ExecResult {
    pub container: String,
    pub argv: Vec<String>,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
    pub truncated: bool,
}

#[derive(Debug, Serialize)]
pub struct BoxSummary {
    pub project_id: String,
    pub name: String,
    pub config_path: PathBuf,
    pub exists: bool,
    pub running: bool,
    pub image: String,
    pub image_digest: Option<String>,
    pub security_profile: Profile,
    pub expires_at: Option<f64>,
}

#[derive(Clone, Copy, Debug)]
pub struct EnsureOptions {
    /// Allow destructive actions (recreation, package removal).
    pub force: bool,
    /// Rebuild even when the container is in sync (implies `force`).
    pub recreate: bool,
    pub lock_timeout: Duration,
}

impl Default for EnsureOptions {
    fn default() -> Self {
        Self {
            force: false,
            recreate: false,
            lock_timeout: DEFAULT_LOCK_TIMEOUT,
        }
    }
}

#[derive(Clone, Debug)]
pub struct ExecRequest {
    pub argv: Vec<String>,
    pub timeout: Duration,
    pub max_output: usize,
    pub as_root: bool,
    pub workdir: Option<String>,
    pub env: StringMap,
}

impl ExecRequest {
    pub fn new(argv: Vec<String>) -> Self {
        Self {
            argv,
            timeout: Duration::from_secs(300),
            max_output: 1024 * 1024,
            as_root: false,
            workdir: None,
            env: StringMap::new(),
        }
    }
}

// ---------------------------------------------------------------------------
// Helpers

fn default_home(name: &str) -> PathBuf {
    paths::env_nonempty("XDG_DATA_HOME")
        .map(|dir| paths::expand(&dir))
        .unwrap_or_else(|| paths::home_dir().join(".local/share"))
        .join("zaribox/home")
        .join(name)
}

fn home_for(manifest: &Manifest) -> PathBuf {
    manifest
        .home_dir
        .clone()
        .unwrap_or_else(|| default_home(&manifest.name))
}

fn provision_timeout() -> Result<Duration> {
    let Some(raw) = paths::env_nonempty("ZARIBOX_PROVISION_TIMEOUT") else {
        return Ok(DEFAULT_PROVISION_TIMEOUT);
    };
    raw.trim()
        .parse::<f64>()
        .ok()
        .filter(|secs| *secs > 0.0)
        .and_then(|secs| Duration::try_from_secs_f64(secs).ok())
        .context("ZARIBOX_PROVISION_TIMEOUT must be a positive number of seconds")
}

/// Mount roots an AgentBox may reach: `$ZARIBOX_ALLOWED_MOUNT_ROOTS` or the
/// manifest directory.
fn allowed_roots(manifest: &Manifest) -> Vec<PathBuf> {
    let configured: Vec<PathBuf> = std::env::var_os("ZARIBOX_ALLOWED_MOUNT_ROOTS")
        .map(|raw| {
            std::env::split_paths(&raw)
                .filter(|p| !p.as_os_str().is_empty())
                .map(|p| paths::canonical(paths::expand(&p.to_string_lossy())))
                .collect()
        })
        .unwrap_or_default();
    if configured.is_empty() {
        vec![project_dir(manifest)]
    } else {
        configured
    }
}

fn project_dir(manifest: &Manifest) -> PathBuf {
    let path = paths::canonical(&manifest.path);
    path.parent().map(Path::to_path_buf).unwrap_or(path)
}

fn check_agent_home(manifest: &Manifest) -> Result<()> {
    let Some(home) = manifest.home_dir.as_deref() else {
        return Ok(());
    };
    ensure!(
        !home.is_symlink(),
        "agent HomeDir must not be a symbolic link"
    );
    let resolved = paths::canonical(home);
    ensure!(
        resolved != paths::canonical(paths::home_dir()),
        "agent HomeDir cannot be the host home directory"
    );
    ensure!(
        allowed_roots(manifest)
            .iter()
            .any(|root| resolved.starts_with(root)),
        "agent HomeDir is outside the allowed roots: {}",
        resolved.display()
    );
    Ok(())
}

fn mount_specs(manifest: &Manifest) -> Result<Vec<MountSpec>> {
    let agent = manifest.profile() == Profile::Agent;
    let base = project_dir(manifest);
    let roots = allowed_roots(manifest);
    manifest
        .mounts
        .iter()
        .map(|mount| {
            let source = paths::expand(&mount.source);
            let source = paths::canonical(base.join(source));
            ensure!(
                source.exists(),
                "mount source does not exist: {}",
                source.display()
            );
            ensure!(
                !agent || roots.iter().any(|root| source.starts_with(root)),
                "agent mount is outside the allowed roots: {}",
                source.display()
            );
            let mut options = vec![if mount.read_only { "ro" } else { "rw" }];
            for option in &mount.options {
                if !options.contains(&option.as_str()) {
                    options.push(option);
                }
            }
            Ok(MountSpec {
                source,
                target: mount.target.clone(),
                options: options.join(","),
            })
        })
        .collect()
}

/// Host-side checks that depend on the filesystem.
fn check_host(manifest: &Manifest) -> Result<()> {
    if manifest.profile() == Profile::Agent {
        check_agent_home(manifest)?;
    }
    mount_specs(manifest).map(drop)
}

fn create_policy(manifest: &Manifest, project_id: &str) -> Result<CreatePolicy> {
    let read_only = manifest.security.read_only_root_filesystem;
    let mut labels = StringMap::from([(LABEL_PROJECT.to_string(), project_id.to_string())]);
    labels.extend(
        manifest
            .labels
            .iter()
            .map(|(key, value)| (format!("io.zaribox.user-label.{key}"), value.clone())),
    );
    Ok(CreatePolicy {
        profile: manifest.profile(),
        network: manifest.network(),
        mounts: mount_specs(manifest)?,
        env: manifest.env.clone(),
        workdir: manifest.workdir.clone(),
        resources: state::effective_resources(manifest),
        read_only_root: read_only,
        writable_tmpfs: if read_only {
            vec![READ_ONLY_TMPFS.to_string()]
        } else {
            Vec::new()
        },
        labels,
        extra_flags: manifest.extra_flags.clone(),
        timeout: Some(provision_timeout()?),
    })
}

/// Rewrite the top-level `Packages:` block of a flat manifest, returning the
/// packages that were added.
fn merge_packages(manifest: &Manifest, installed: &[String]) -> Result<Vec<String>> {
    let mut added: Vec<String> = installed
        .iter()
        .filter(|p| !manifest.packages.contains(p))
        .cloned()
        .collect();
    added.sort();
    added.dedup();
    if added.is_empty() {
        return Ok(added);
    }
    ensure!(
        !manifest.is_agent_box(),
        "export only edits desktop manifests; add packages to Runtime.Packages manually"
    );
    let mut merged: Vec<&String> = manifest.packages.iter().chain(&added).collect();
    merged.sort();
    merged.dedup();
    let block: String = std::iter::once("Packages:\n".to_string())
        .chain(merged.iter().map(|p| format!("  - {p}\n")))
        .collect();

    let text = std::fs::read_to_string(&manifest.path)
        .with_context(|| format!("cannot read {}", manifest.path.display()))?;
    let lines: Vec<&str> = text.split_inclusive('\n').collect();
    let updated = match lines.iter().position(|l| l.starts_with("Packages:")) {
        Some(start) => {
            // The block ends at the next non-indented, non-blank line.
            let end = lines[start + 1..]
                .iter()
                .position(|l| !l.trim().is_empty() && !l.starts_with([' ', '\t', '-']))
                .map_or(lines.len(), |offset| start + 1 + offset);
            [lines[..start].concat(), block, lines[end..].concat()].concat()
        }
        None => format!("{}\n{block}", text.trim_end_matches('\n')),
    };
    state::atomic_write(&manifest.path, &updated)?;
    Ok(added)
}

// ---------------------------------------------------------------------------
// Service

pub struct Service {
    backend: Box<dyn Backend>,
}

impl Default for Service {
    fn default() -> Self {
        Self::new(Box::new(PodmanBackend::new()))
    }
}

impl Service {
    pub fn new(backend: Box<dyn Backend>) -> Self {
        Self { backend }
    }

    pub fn backend(&self) -> &dyn Backend {
        self.backend.as_ref()
    }

    /// Resolve a manifest path, container name, or (with `None`) the manifest
    /// in the current directory, then load and validate it.
    pub fn load(&self, target: Option<&str>) -> Result<Manifest> {
        let path = match target {
            Some(target) if !target.is_empty() => {
                let direct = paths::expand(target);
                if direct.is_file() {
                    direct
                } else if target.contains('/')
                    || target.ends_with(".yaml")
                    || target.ends_with(".yml")
                {
                    // Clearly a path: don't fall back to container-name lookup.
                    config::find_manifest(Some(target))?
                } else {
                    config::find_manifest(Some(target))
                        .or_else(|_| manifest_for_container(target))?
                }
            }
            _ => config::find_manifest(None)?,
        };
        let manifest = config::load(&path)?;
        check_host(&manifest)?;
        Ok(manifest)
    }

    pub fn validate(&self, target: Option<&str>) -> Result<Validation> {
        let manifest = self.load(target)?;
        Ok(Validation {
            valid: true,
            config_path: paths::canonical(&manifest.path),
            name: manifest.name.clone(),
            image: manifest.image.clone(),
            kind: if manifest.is_agent_box() {
                "AgentBox"
            } else {
                "DesktopBox"
            },
            security_profile: manifest.profile(),
        })
    }

    pub fn plan(&self, target: Option<&str>) -> Result<Plan> {
        self.plan_manifest(&self.load(target)?)
    }

    fn plan_manifest(&self, manifest: &Manifest) -> Result<Plan> {
        let store = ProjectStore::new(&manifest.path);
        let record = store.load()?;
        let identity = state::identity_digest(manifest);
        let mut actions = Vec::new();
        let applied: &[String] = if !self.backend.container_exists(&manifest.name)? {
            actions.push(Action::Create {
                image: manifest.image.clone(),
            });
            &[]
        } else {
            match &record {
                Some(record) if record.applied_identity_digest == identity => {
                    &record.applied_packages
                }
                _ => {
                    actions.push(Action::Recreate {
                        from: record.as_ref().map(|r| r.applied_identity_digest.clone()),
                        to: identity.clone(),
                    });
                    &[]
                }
            }
        };
        let (install, remove) = state::package_drift(&manifest.packages, applied);
        if !install.is_empty() {
            actions.push(Action::InstallPackages { packages: install });
        }
        if !remove.is_empty() {
            actions.push(Action::RemovePackages { packages: remove });
        }
        let builds = actions
            .iter()
            .any(|a| matches!(a, Action::Create { .. } | Action::Recreate { .. }));
        if builds && !manifest.run.is_empty() {
            actions.push(Action::RunPostInstall {
                count: manifest.run.len(),
            });
        }
        let actions: Vec<PlannedAction> = actions
            .into_iter()
            .map(|action| PlannedAction {
                destructive: action.destructive(),
                action,
            })
            .collect();
        Ok(Plan {
            project_id: store.project_id,
            container: manifest.name.clone(),
            config_path: paths::canonical(&manifest.path),
            requires_force: actions.iter().any(|a| a.destructive),
            actions,
            identity_digest: identity,
        })
    }

    pub fn status(&self, target: &str) -> Result<Status> {
        let manifest = self.load(Some(target))?;
        let store = ProjectStore::new(&manifest.path);
        let record = store.load()?;
        let applied = record
            .as_ref()
            .map(|r| r.applied_packages.clone())
            .unwrap_or_default();
        let (install, remove) = state::package_drift(&manifest.packages, &applied);
        let exists = self.backend.container_exists(&manifest.name)?;
        let identity = state::identity_digest(&manifest);
        Ok(Status {
            project_id: store.project_id,
            container: manifest.name.clone(),
            config_path: paths::canonical(&manifest.path),
            exists,
            config_in_sync: record
                .as_ref()
                .is_some_and(|r| r.applied_identity_digest == identity),
            desired_packages: manifest.packages.clone(),
            applied_packages: applied,
            install,
            remove,
            image: manifest.image.clone(),
            image_digest: if exists {
                self.backend.image_digest(&manifest.name)?
            } else {
                None
            },
            security_profile: manifest.profile(),
            expires_at: record.and_then(|r| r.expires_at),
        })
    }

    fn check_owner(&self, name: &str, project_id: &str, unmanaged: &str) -> Result<Option<String>> {
        if self.backend.label(name, LABEL_MANAGED)?.as_deref() != Some("true") {
            bail!("{unmanaged}");
        }
        let owner = self.backend.label(name, LABEL_PROJECT)?;
        if owner.as_deref().is_some_and(|owner| owner != project_id) {
            bail!("container '{name}' belongs to another ZariBox project");
        }
        Ok(owner)
    }

    fn assert_owned(&self, manifest: &Manifest, project_id: &str) -> Result<()> {
        let name = &manifest.name;
        let owner = self.check_owner(
            name,
            project_id,
            &format!("container '{name}' is not managed by ZariBox"),
        )?;
        ensure!(
            owner.is_some() || manifest.profile() != Profile::Agent,
            "agent container '{name}' has no project ownership label; recreate it"
        );
        Ok(())
    }

    /// Install/remove packages to match the manifest; reports whether any changed.
    fn sync_packages(&self, manifest: &Manifest, applied: &[String]) -> Result<bool> {
        let (install, remove) = state::package_drift(&manifest.packages, applied);
        let changed = !(install.is_empty() && remove.is_empty());
        let manager = PackageManager::detect(&manifest.image);
        let options = ExecOptions {
            check: true,
            timeout: Some(provision_timeout()?),
            max_output: Some(PROVISION_OUTPUT_LIMIT),
            ..ExecOptions::default()
        };
        for (script, packages) in [
            (pkgmgr::install_script(manager), install),
            (pkgmgr::remove_script(manager), remove),
        ] {
            if packages.is_empty() {
                continue;
            }
            let command: Vec<String> = ["sh", "-c", &script, "_"]
                .into_iter()
                .map(String::from)
                .chain(packages)
                .collect();
            self.backend.exec(&manifest.name, &command, &options)?;
        }
        Ok(changed)
    }

    /// Create or reconcile the box described by `target`.
    pub fn ensure(&self, target: Option<&str>, options: EnsureOptions) -> Result<Operation> {
        let manifest = self.load(target)?;
        let backend = self.backend.as_ref();
        ensure!(
            backend.runtime_present(),
            "{} is not installed or not in PATH",
            backend.name()
        );
        let store = ProjectStore::new(&manifest.path);
        let operation_id = state::new_operation_id();
        let _session = store.begin_session(&operation_id, "ensure")?;
        let _lock = store.lock(options.lock_timeout)?;

        let plan = self.plan_manifest(&manifest)?;
        let exists = !plan.has(|a| matches!(a, Action::Create { .. }));
        let recreate =
            plan.has(|a| matches!(a, Action::Recreate { .. })) || (options.recreate && exists);
        let force = options.force || options.recreate;
        ensure!(
            force || !(recreate || plan.requires_force),
            "plan contains destructive actions; rerun with --force"
        );
        let needs_create = recreate || plan.has(|a| matches!(a, Action::Create { .. }));

        let mut rollback = Rollback::default();
        let result = self.reconcile(
            &manifest,
            &store,
            &operation_id,
            needs_create,
            &mut rollback,
        );
        match result {
            Ok(operation) => Ok(operation),
            Err(error) => {
                rollback.undo(backend, &manifest.name);
                Err(error)
            }
        }
    }

    fn reconcile(
        &self,
        manifest: &Manifest,
        store: &ProjectStore,
        operation_id: &str,
        needs_create: bool,
        rollback: &mut Rollback,
    ) -> Result<Operation> {
        let backend = self.backend.as_ref();
        let name = manifest.name.as_str();
        let record = store.load()?;
        let mut applied = record
            .as_ref()
            .map(|r| r.applied_packages.clone())
            .unwrap_or_default();
        let mut actions = Vec::new();
        let mut warnings = Vec::new();
        let home = home_for(manifest);

        if needs_create {
            if backend.container_exists(name)? {
                self.check_owner(
                    name,
                    &store.project_id,
                    &format!("refusing to replace unmanaged container '{name}'"),
                )?;
                let backup = format!("{name}.backup-{}", &operation_id[..8]);
                let was_running = backend.is_running(name)?;
                let _ = backend.stop(name);
                backend.rename(name, &backup)?;
                rollback.backup = Some((backup, was_running));
            }
            if !home.exists() {
                std::fs::create_dir_all(&home)
                    .with_context(|| format!("cannot create {}", home.display()))?;
                std::fs::set_permissions(&home, std::fs::Permissions::from_mode(0o700))?;
            }
            let home_str = home.to_string_lossy();
            let policy = create_policy(manifest, &store.project_id)?;
            rollback.created = true;
            backend.create(&CreateRequest {
                name,
                image: &manifest.image,
                home_dir: &home_str,
                home_mount: manifest.home_mount,
                policy: &policy,
            })?;
            actions.push(if rollback.backup.is_some() {
                "recreate"
            } else {
                "create"
            });
            applied.clear();
        }

        if self.sync_packages(manifest, &applied)? {
            actions.push("sync_packages");
        }

        if needs_create {
            let options = ExecOptions {
                as_user: true,
                check: true,
                timeout: Some(provision_timeout()?),
                max_output: Some(PROVISION_OUTPUT_LIMIT),
                ..ExecOptions::default()
            };
            for command in &manifest.run {
                let argv = ["sh".to_string(), "-lc".to_string(), command.clone()];
                backend.exec(name, &argv, &options)?;
            }
            if !manifest.run.is_empty() {
                actions.push("run_post_install");
            }
            backend.post_install(name, &home.to_string_lossy())?;
        }

        let now = state::timestamp();
        let mut new_record = ProjectRecord {
            schema_version: state::SCHEMA_VERSION,
            project_id: store.project_id.clone(),
            config_path: paths::canonical(&manifest.path),
            container_name: name.to_string(),
            backend: backend.name().to_string(),
            applied_identity_digest: state::identity_digest(manifest),
            applied_packages: manifest.packages.clone(),
            image: manifest.image.clone(),
            image_digest: backend.image_digest(name)?,
            security_profile: manifest.profile(),
            created_at: record.map(|r| r.created_at).unwrap_or_else(|| now.clone()),
            updated_at: now,
            expires_at: manifest
                .ttl
                .map(|ttl| state::unix_now() + ttl.as_secs_f64()),
        };
        store.save(&mut new_record)?;

        if let Some((backup, _)) = rollback.backup.take()
            && let Err(error) = backend.remove(&backup)
        {
            warnings.push(format!("backup cleanup failed: {error:#}"));
        }
        rollback.created = false;
        Ok(Operation {
            operation_id: operation_id.to_string(),
            changed: !actions.is_empty(),
            container: name.to_string(),
            actions,
            warnings,
        })
    }

    /// Run a bounded, non-interactive command in a managed box.
    pub fn exec(&self, target: &str, request: ExecRequest) -> Result<ExecResult> {
        ensure!(
            !request.argv.is_empty(),
            "at least one command argument is required"
        );
        let manifest = self.load(Some(target))?;
        let name = &manifest.name;
        ensure!(
            self.backend.container_exists(name)?,
            "container '{name}' does not exist; run create first"
        );
        let store = ProjectStore::new(&manifest.path);
        self.assert_owned(&manifest, &store.project_id)?;
        let _lock = store.lock(DEFAULT_LOCK_TIMEOUT)?;
        let output = self.backend.exec(
            name,
            &request.argv,
            &ExecOptions {
                as_user: !request.as_root,
                check: false,
                timeout: Some(request.timeout),
                max_output: Some(request.max_output),
                agent: Some(manifest.profile() == Profile::Agent),
                workdir: request.workdir.or_else(|| manifest.workdir.clone()),
                env: Some(request.env),
            },
        )?;
        Ok(ExecResult {
            container: name.clone(),
            argv: request.argv,
            exit_code: output.exit_code,
            stdout: output.stdout,
            stderr: output.stderr,
            timed_out: output.timed_out,
            truncated: output.truncated,
        })
    }

    /// Stop and remove a managed box and its state; the home is preserved.
    pub fn destroy(&self, target: &str, lock_timeout: Duration) -> Result<Operation> {
        let manifest = self.load(Some(target))?;
        let name = &manifest.name;
        let store = ProjectStore::new(&manifest.path);
        let _lock = store.lock(lock_timeout)?;
        let mut actions = Vec::new();
        if self.backend.container_exists(name)? {
            self.check_owner(
                name,
                &store.project_id,
                &format!("refusing to destroy unmanaged container '{name}'"),
            )?;
            let _ = self.backend.stop(name);
            self.backend.remove(name)?;
            actions.push("destroy");
        }
        store.clear()?;
        Ok(Operation {
            operation_id: state::new_operation_id(),
            changed: !actions.is_empty(),
            container: name.clone(),
            actions,
            warnings: Vec::new(),
        })
    }

    /// Add explicitly installed packages to the manifest.
    pub fn export(&self, target: &str) -> Result<Vec<String>> {
        let manifest = self.load(Some(target))?;
        let name = &manifest.name;
        ensure!(
            self.backend.container_exists(name)?,
            "container '{name}' does not exist"
        );
        let store = ProjectStore::new(&manifest.path);
        self.assert_owned(&manifest, &store.project_id)?;
        let _lock = store.lock(DEFAULT_LOCK_TIMEOUT)?;
        let script = pkgmgr::list_script(PackageManager::detect(&manifest.image));
        let command = ["sh".to_string(), "-c".to_string(), script];
        let output = self.backend.exec(name, &command, &ExecOptions::default())?;
        let installed: Vec<String> = output
            .stdout
            .lines()
            .filter_map(|line| line.split_whitespace().next())
            .map(String::from)
            .collect();
        let added = merge_packages(&manifest, &installed)?;
        if !added.is_empty()
            && let Some(mut record) = store.load()?
        {
            let mut packages: Vec<String> =
                manifest.packages.iter().chain(&added).cloned().collect();
            packages.sort();
            packages.dedup();
            record.applied_packages = packages;
            store.save(&mut record)?;
        }
        Ok(added)
    }

    pub fn list(&self) -> Result<Vec<BoxSummary>> {
        state::all_records()
            .into_iter()
            .map(|record| {
                let exists = self.backend.container_exists(&record.container_name)?;
                Ok(BoxSummary {
                    running: exists && self.backend.is_running(&record.container_name)?,
                    exists,
                    project_id: record.project_id,
                    name: record.container_name,
                    config_path: record.config_path,
                    image: record.image,
                    image_digest: record.image_digest,
                    security_profile: record.security_profile,
                    expires_at: record.expires_at,
                })
            })
            .collect()
    }

    /// Remove expired boxes and abandoned operation leases.
    pub fn cleanup(&self) -> Vec<String> {
        let now = state::unix_now();
        let mut removed = state::cleanup_expired_sessions(now);
        let expired = state::all_records()
            .into_iter()
            .filter(|r| r.expires_at.is_some_and(|at| at <= now));
        for record in expired {
            let lock_timeout = Duration::from_millis(100);
            let outcome = if record.config_path.is_file() {
                self.destroy(&record.config_path.to_string_lossy(), lock_timeout)
                    .map(|op| op.changed)
            } else {
                self.remove_orphan(&record, lock_timeout)
            };
            if outcome.unwrap_or(false) {
                removed.push(record.container_name);
            }
        }
        removed
    }

    /// Remove an expired box whose manifest no longer exists.
    fn remove_orphan(&self, record: &ProjectRecord, lock_timeout: Duration) -> Result<bool> {
        let name = &record.container_name;
        let store = ProjectStore::for_id(record.project_id.clone());
        let _lock = store.lock(lock_timeout)?;
        let mut removed = false;
        if self.backend.container_exists(name)? {
            let managed = self.backend.label(name, LABEL_MANAGED)?.as_deref() == Some("true");
            let owned = self.backend.label(name, LABEL_PROJECT)?.as_deref()
                == Some(record.project_id.as_str());
            if !(managed && owned) {
                return Ok(false);
            }
            let _ = self.backend.stop(name);
            self.backend.remove(name)?;
            removed = true;
        }
        store.clear()?;
        Ok(removed)
    }
}

/// Manifest recorded for a managed container name.
fn manifest_for_container(name: &str) -> Result<PathBuf> {
    state::all_records()
        .into_iter()
        .find(|r| r.container_name == name && r.config_path.is_file())
        .map(|r| r.config_path)
        .with_context(|| format!("no manifest or managed container named '{name}'"))
}

/// Undo state for a failed `ensure`.
#[derive(Default)]
struct Rollback {
    created: bool,
    /// Renamed original container and whether it was running.
    backup: Option<(String, bool)>,
}

impl Rollback {
    fn undo(self, backend: &dyn Backend, name: &str) {
        if self.created && backend.container_exists(name).unwrap_or(false) {
            let _ = backend.remove(name);
        }
        if let Some((backup, was_running)) = self.backup
            && backend.container_exists(&backup).unwrap_or(false)
            && backend.rename(&backup, name).is_ok()
            && was_running
        {
            let _ = backend.start(name);
        }
    }
}

#[cfg(test)]
pub(crate) mod tests;
