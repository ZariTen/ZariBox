//! Persistent project state: records, locks, operation sessions, and the
//! container identity digest.
//!
//! Layout under [`state_root`]:
//!
//! ```text
//! projects/<project-id>/state.json   ProjectRecord
//! locks/<project-id>.lock            advisory lock file
//! sessions/<operation-id>.json       in-flight operation lease
//! ```

use std::fs::{File, OpenOptions, TryLockError};
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime};

use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::config::{Manifest, Mount, Network, Profile, Resources, StringMap};
use crate::paths;

pub const SCHEMA_VERSION: u32 = 1;
const SESSION_LEASE: Duration = Duration::from_secs(3600);

// ---------------------------------------------------------------------------
// Utilities

pub fn sha256_hex(data: impl AsRef<[u8]>) -> String {
    Sha256::digest(data)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Seconds since the Unix epoch.
pub fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

/// RFC 3339 UTC timestamp with second precision.
pub fn timestamp() -> String {
    humantime::format_rfc3339_seconds(SystemTime::now()).to_string()
}

pub fn new_operation_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

/// Atomically replace `path` with `contents` (temp file + rename). An existing
/// file keeps its permissions; new files are created `0600`.
pub fn atomic_write(path: &Path, contents: &str) -> Result<()> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    std::fs::create_dir_all(parent)
        .with_context(|| format!("cannot create {}", parent.display()))?;
    let mode = std::fs::metadata(path)
        .map(|m| m.permissions().mode() & 0o7777)
        .unwrap_or(0o600);
    let mut file = tempfile::NamedTempFile::new_in(parent)
        .with_context(|| format!("cannot create a temporary file in {}", parent.display()))?;
    file.write_all(contents.as_bytes())?;
    file.as_file().sync_all()?;
    file.as_file()
        .set_permissions(std::fs::Permissions::from_mode(mode))?;
    file.persist(path)
        .with_context(|| format!("cannot write {}", path.display()))?;
    if let Ok(dir) = File::open(parent) {
        let _ = dir.sync_all();
    }
    Ok(())
}

fn remove_if_exists(path: &Path) -> Result<()> {
    match std::fs::remove_file(path) {
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => {
            Err(e).with_context(|| format!("cannot remove {}", path.display()))
        }
        _ => Ok(()),
    }
}

// ---------------------------------------------------------------------------
// Identity digest and package drift

/// Everything that requires recreating the container when it changes.
#[derive(Serialize)]
struct Identity<'a> {
    name: &'a str,
    image: &'a str,
    home_dir: Option<&'a Path>,
    home_mount: bool,
    extra_flags: &'a [String],
    mounts: &'a [Mount],
    env: &'a StringMap,
    workdir: Option<&'a str>,
    run: &'a [String],
    network: Network,
    profile: Profile,
    resources: Resources,
    read_only_root: bool,
}

/// Resource limits actually applied: AgentBoxes get defaults for unset values.
pub fn effective_resources(manifest: &Manifest) -> Resources {
    let mut resources = manifest.resources.clone();
    if manifest.profile() == Profile::Agent {
        resources.cpus.get_or_insert(2.0);
        resources.memory.get_or_insert_with(|| "2g".into());
        resources.pids_limit.get_or_insert(256);
    }
    resources
}

/// SHA-256 over the canonical JSON form of the container identity.
pub fn identity_digest(manifest: &Manifest) -> String {
    let identity = Identity {
        name: &manifest.name,
        image: &manifest.image,
        home_dir: manifest.home_dir.as_deref(),
        home_mount: manifest.home_mount,
        extra_flags: &manifest.extra_flags,
        mounts: &manifest.mounts,
        env: &manifest.env,
        workdir: manifest.workdir.as_deref(),
        run: &manifest.run,
        network: manifest.network(),
        profile: manifest.profile(),
        resources: effective_resources(manifest),
        read_only_root: manifest.security.read_only_root_filesystem,
    };
    sha256_hex(serde_json::to_vec(&identity).expect("identity serialises"))
}

/// Packages to install and remove, each sorted and de-duplicated.
pub fn package_drift(desired: &[String], applied: &[String]) -> (Vec<String>, Vec<String>) {
    let missing = |from: &[String], other: &[String]| {
        let mut out: Vec<String> = from
            .iter()
            .filter(|p| !other.contains(p))
            .cloned()
            .collect();
        out.sort();
        out.dedup();
        out
    };
    (missing(desired, applied), missing(applied, desired))
}

// ---------------------------------------------------------------------------
// Project state

/// `$ZARIBOX_STATE_HOME`, else `$XDG_STATE_HOME/zaribox`, else
/// `~/.local/state/zaribox`.
pub fn state_root() -> PathBuf {
    if let Some(dir) = paths::env_nonempty("ZARIBOX_STATE_HOME") {
        return paths::expand(&dir);
    }
    paths::env_nonempty("XDG_STATE_HOME")
        .map(|dir| paths::expand(&dir))
        .unwrap_or_else(|| paths::home_dir().join(".local/state"))
        .join("zaribox")
}

/// Stable identifier derived from the canonical manifest path.
pub fn project_id(manifest_path: &Path) -> String {
    let canonical = paths::canonical(manifest_path);
    sha256_hex(canonical.as_os_str().as_encoded_bytes())[..24].to_string()
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct ProjectRecord {
    pub schema_version: u32,
    pub project_id: String,
    pub config_path: PathBuf,
    pub container_name: String,
    pub backend: String,
    pub applied_identity_digest: String,
    #[serde(default)]
    pub applied_packages: Vec<String>,
    #[serde(default)]
    pub image: String,
    #[serde(default)]
    pub image_digest: Option<String>,
    #[serde(default)]
    pub security_profile: Profile,
    pub created_at: String,
    pub updated_at: String,
    /// Unix time after which `cleanup` removes the container.
    #[serde(default)]
    pub expires_at: Option<f64>,
}

impl ProjectRecord {
    fn is_supported(&self) -> bool {
        self.schema_version == SCHEMA_VERSION
    }
}

/// Exclusive advisory lock on a project; released on drop.
#[derive(Debug)]
pub struct ProjectLock {
    _file: File,
}

/// Operation lease removed when dropped.
#[derive(Debug)]
pub struct Session {
    path: PathBuf,
}

impl Drop for Session {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

#[derive(Debug, Deserialize, Serialize)]
struct SessionRecord {
    schema_version: u32,
    operation_id: String,
    project_id: String,
    kind: String,
    started_at: f64,
    expires_at: f64,
}

#[derive(Clone, Debug)]
pub struct ProjectStore {
    pub project_id: String,
    root: PathBuf,
    state_path: PathBuf,
    lock_path: PathBuf,
}

impl ProjectStore {
    pub fn new(manifest_path: &Path) -> Self {
        Self::for_id(project_id(manifest_path))
    }

    pub fn for_id(project_id: String) -> Self {
        let root = state_root();
        Self {
            state_path: root.join("projects").join(&project_id).join("state.json"),
            lock_path: root.join("locks").join(format!("{project_id}.lock")),
            project_id,
            root,
        }
    }

    pub fn load(&self) -> Result<Option<ProjectRecord>> {
        if !self.state_path.is_file() {
            return Ok(None);
        }
        let record = read_record(&self.state_path)
            .with_context(|| format!("invalid project state {}", self.state_path.display()))?;
        Ok(Some(record))
    }

    pub fn save(&self, record: &mut ProjectRecord) -> Result<()> {
        record.updated_at = timestamp();
        let json = serde_json::to_string_pretty(record)? + "\n";
        atomic_write(&self.state_path, &json)
    }

    pub fn clear(&self) -> Result<()> {
        remove_if_exists(&self.state_path)?;
        if let Some(dir) = self.state_path.parent() {
            let _ = std::fs::remove_dir(dir);
        }
        Ok(())
    }

    pub fn lock(&self, timeout: Duration) -> Result<ProjectLock> {
        if let Some(dir) = self.lock_path.parent() {
            std::fs::create_dir_all(dir)
                .with_context(|| format!("cannot create {}", dir.display()))?;
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.lock_path)
            .with_context(|| format!("cannot open {}", self.lock_path.display()))?;
        let deadline = Instant::now() + timeout;
        loop {
            match file.try_lock() {
                Ok(()) => return Ok(ProjectLock { _file: file }),
                Err(TryLockError::WouldBlock) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(50));
                }
                Err(TryLockError::WouldBlock) => {
                    bail!("timed out waiting for project lock {}", self.project_id)
                }
                Err(TryLockError::Error(error)) => {
                    return Err(error)
                        .with_context(|| format!("cannot lock {}", self.lock_path.display()));
                }
            }
        }
    }

    /// Record an in-flight operation; the lease is removed when dropped.
    pub fn begin_session(&self, operation_id: &str, kind: &str) -> Result<Session> {
        let started_at = unix_now();
        let record = SessionRecord {
            schema_version: SCHEMA_VERSION,
            operation_id: operation_id.into(),
            project_id: self.project_id.clone(),
            kind: kind.into(),
            started_at,
            expires_at: started_at + SESSION_LEASE.as_secs_f64(),
        };
        let path = sessions_dir(&self.root).join(format!("{operation_id}.json"));
        atomic_write(&path, &(serde_json::to_string_pretty(&record)? + "\n"))?;
        Ok(Session { path })
    }
}

fn read_record(path: &Path) -> Result<ProjectRecord> {
    let text = std::fs::read_to_string(path)?;
    let record: ProjectRecord = serde_json::from_str(&text)?;
    if !record.is_supported() {
        bail!("unsupported schema version {}", record.schema_version);
    }
    Ok(record)
}

/// Every readable project record, sorted by state path.
pub fn all_records() -> Vec<ProjectRecord> {
    paths::list_dir(&state_root().join("projects"), |_| true)
        .into_iter()
        .filter_map(|dir| read_record(&dir.join("state.json")).ok())
        .collect()
}

fn sessions_dir(root: &Path) -> PathBuf {
    root.join("sessions")
}

/// Remove expired operation leases, returning their operation ids.
pub fn cleanup_expired_sessions(now: f64) -> Vec<String> {
    paths::list_dir(&sessions_dir(&state_root()), |name| name.ends_with(".json"))
        .into_iter()
        .filter_map(|path| {
            let text = std::fs::read_to_string(&path).ok()?;
            let session: SessionRecord = serde_json::from_str(&text).ok()?;
            (session.expires_at <= now && std::fs::remove_file(&path).is_ok())
                .then_some(session.operation_id)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn manifest(image: &str) -> Manifest {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("box.yaml");
        std::fs::write(&path, format!("Image: {image}\n")).unwrap();
        crate::config::load(&path).unwrap()
    }

    #[test]
    fn identity_digest_tracks_recreate_inputs() {
        let base = manifest("archlinux");
        assert_eq!(identity_digest(&base), identity_digest(&base.clone()));
        assert_eq!(
            identity_digest(&base),
            identity_digest(&manifest("docker.io/library/archlinux:latest"))
        );
        assert_ne!(identity_digest(&base), identity_digest(&manifest("ubuntu")));
        let mut mounted = base.clone();
        mounted.home_mount = true;
        assert_ne!(identity_digest(&base), identity_digest(&mounted));
        let mut agent = base.clone();
        agent.kind = Some(crate::config::Kind::AgentBox);
        assert_ne!(identity_digest(&base), identity_digest(&agent));
        // Packages are reconciled in place and do not force recreation.
        let mut packages = base.clone();
        packages.packages = vec!["git".into()];
        assert_eq!(identity_digest(&base), identity_digest(&packages));
    }

    #[test]
    fn drift() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        assert_eq!(
            package_drift(&s(&["git", "curl"]), &s(&["curl", "git"])),
            (vec![], vec![])
        );
        assert_eq!(
            package_drift(&s(&["git", "curl", "git"]), &s(&["git", "vim"])),
            (s(&["curl"]), s(&["vim"]))
        );
    }

    #[test]
    fn atomic_write_keeps_permissions() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        atomic_write(&path, "one").unwrap();
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        atomic_write(&path, "two").unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "two");
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o644
        );
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
    }

    #[test]
    fn records_round_trip() {
        let record = ProjectRecord {
            schema_version: SCHEMA_VERSION,
            project_id: "abc".into(),
            config_path: "/x/box.yaml".into(),
            container_name: "box".into(),
            backend: "podman".into(),
            applied_identity_digest: "d".into(),
            applied_packages: vec!["git".into()],
            image: "img".into(),
            image_digest: None,
            security_profile: Profile::Agent,
            created_at: timestamp(),
            updated_at: timestamp(),
            expires_at: Some(1.5),
        };
        let json = serde_json::to_string(&record).unwrap();
        assert!(json.contains("\"security_profile\":\"agent\""));
        assert_eq!(
            serde_json::from_str::<ProjectRecord>(&json).unwrap(),
            record
        );
        assert_eq!(new_operation_id().len(), 32);
    }
}
