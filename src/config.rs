//! Manifest schema and validation.
//!
//! Two manifest flavours share one file format:
//!
//! * desktop boxes use flat top-level fields (`Name`, `Image`, `Packages`, ...),
//!   optionally combined with the structured sections;
//! * versioned AgentBoxes (`ApiVersion: zaribox.dev/v1`, `Kind: AgentBox`) must
//!   use the structured sections only.
//!
//! The YAML is deserialised with serde into `RawManifest`, then validated and
//! flattened into [`Manifest`].

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};

use crate::paths;

pub type StringMap = BTreeMap<String, String>;

pub const API_VERSION: &str = "zaribox.dev/v1";

// ---------------------------------------------------------------------------
// Public model

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
pub enum Kind {
    AgentBox,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Network {
    None,
    Private,
    Slirp4netns,
    Pasta,
    Host,
}

impl Network {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Private => "private",
            Self::Slirp4netns => "slirp4netns",
            Self::Pasta => "pasta",
            Self::Host => "host",
        }
    }
}

/// Profile names accepted in `Security.Profile`.
#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ProfileName {
    Agent,
    Restricted,
    Desktop,
    Default,
}

/// Effective security profile.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Profile {
    /// Desktop integration: host network/IPC, graphics, sudo.
    #[default]
    Default,
    /// Hardened, isolated AgentBox.
    Agent,
}

impl Profile {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Default => "default",
            Self::Agent => "agent",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields, rename_all = "PascalCase")]
pub struct Mount {
    #[serde(alias = "HostPath")]
    pub source: String,
    #[serde(alias = "ContainerPath")]
    pub target: String,
    #[serde(default)]
    pub read_only: bool,
    #[serde(default)]
    pub options: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields, rename_all = "PascalCase")]
pub struct Resources {
    #[serde(rename = "CPUs")]
    pub cpus: Option<f64>,
    pub memory: Option<String>,
    pub pids_limit: Option<u64>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
#[serde(deny_unknown_fields, rename_all = "PascalCase")]
pub struct Security {
    pub network: Option<Network>,
    pub profile: Option<ProfileName>,
    #[serde(default)]
    pub privileged: bool,
    #[serde(default)]
    pub read_only_root_filesystem: bool,
}

/// A validated manifest.
#[derive(Clone, Debug, PartialEq)]
pub struct Manifest {
    /// Manifest location as it was found (not canonicalised).
    pub path: PathBuf,
    pub kind: Option<Kind>,
    pub name: String,
    /// Fully qualified image reference.
    pub image: String,
    pub home_dir: Option<PathBuf>,
    pub home_mount: bool,
    pub extra_flags: Vec<String>,
    pub packages: Vec<String>,
    pub run: Vec<String>,
    pub env: StringMap,
    pub workdir: Option<String>,
    pub mounts: Vec<Mount>,
    pub ttl: Option<Duration>,
    pub labels: StringMap,
    /// Free-form metadata; not interpreted by ZariBox.
    pub annotations: StringMap,
    pub resources: Resources,
    pub security: Security,
}

impl Manifest {
    pub fn is_agent_box(&self) -> bool {
        self.kind == Some(Kind::AgentBox)
    }

    pub fn profile(&self) -> Profile {
        match (self.kind, self.security.profile) {
            (Some(Kind::AgentBox), _) | (_, Some(ProfileName::Agent | ProfileName::Restricted)) => {
                Profile::Agent
            }
            _ => Profile::Default,
        }
    }

    /// Requested network, defaulting by profile.
    pub fn network(&self) -> Network {
        self.security.network.unwrap_or(match self.profile() {
            Profile::Agent => Network::None,
            Profile::Default => Network::Host,
        })
    }
}

// ---------------------------------------------------------------------------
// Raw YAML schema

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "PascalCase")]
struct RawManifest {
    api_version: Option<String>,
    kind: Option<Kind>,
    #[serde(default)]
    metadata: RawMetadata,
    #[serde(default)]
    workspace: RawWorkspace,
    #[serde(default)]
    runtime: RawRuntime,
    #[serde(default)]
    resources: Resources,
    #[serde(default)]
    security: Security,
    // Flat desktop-box fields.
    name: Option<String>,
    image: Option<String>,
    home_dir: Option<String>,
    home_mount: Option<bool>,
    extra_flags: Option<String>,
    packages: Option<Vec<String>>,
    run: Option<Vec<String>>,
}

impl RawManifest {
    fn flat_fields(&self) -> Vec<&'static str> {
        [
            ("ExtraFlags", self.extra_flags.is_some()),
            ("HomeDir", self.home_dir.is_some()),
            ("HomeMount", self.home_mount.is_some()),
            ("Image", self.image.is_some()),
            ("Name", self.name.is_some()),
            ("Packages", self.packages.is_some()),
            ("Run", self.run.is_some()),
        ]
        .into_iter()
        .filter_map(|(field, present)| present.then_some(field))
        .collect()
    }
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "PascalCase")]
struct RawMetadata {
    name: Option<String>,
    #[serde(rename = "TTL")]
    ttl: Option<RawTtl>,
    #[serde(default)]
    labels: StringMap,
    #[serde(default)]
    annotations: StringMap,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum RawTtl {
    Seconds(u64),
    Human(String),
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "PascalCase")]
struct RawWorkspace {
    home_dir: Option<String>,
    home_mount: Option<bool>,
    #[serde(default)]
    mounts: Vec<Mount>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "PascalCase")]
struct RawRuntime {
    image: Option<String>,
    packages: Option<Vec<String>>,
    run: Option<Vec<String>>,
    #[serde(default)]
    env: StringMap,
    workdir: Option<String>,
}

// ---------------------------------------------------------------------------
// Validators

fn is_name(text: &str) -> bool {
    let mut chars = text.chars();
    text.len() <= 128
        && chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && chars.all(|c| c.is_ascii_alphanumeric() || "_.-".contains(c))
}

/// Package entries are passed to a root-run package manager: restrict them to
/// package/version syntax so a manifest cannot smuggle options.
fn is_safe_package(text: &str) -> bool {
    let mut chars = text.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && chars.all(|c| c.is_ascii_alphanumeric() || "+._:@/~=-".contains(c))
}

fn is_env_name(text: &str) -> bool {
    let mut chars = text.chars();
    chars
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn trimmed(value: Option<String>) -> Option<String> {
    value
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
}

fn trimmed_list(values: Vec<String>) -> Vec<String> {
    values
        .into_iter()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .collect()
}

/// Expand short image names to a fully qualified reference.
pub fn resolve_image(image: &str) -> String {
    let (reference, digest) = match image.split_once('@') {
        Some((reference, digest)) => (reference, Some(digest)),
        None => (image, None),
    };
    let first = reference.split('/').next().unwrap_or_default();
    let mut qualified = if !reference.contains('/') {
        format!("docker.io/library/{reference}")
    } else if !first.contains(['.', ':']) && first != "localhost" {
        format!("docker.io/{reference}")
    } else {
        reference.to_string()
    };
    match digest {
        Some(digest) => {
            qualified.push('@');
            qualified.push_str(digest);
        }
        None if !qualified
            .rsplit('/')
            .next()
            .unwrap_or_default()
            .contains(':') =>
        {
            qualified.push_str(":latest");
        }
        None => {}
    }
    qualified
}

// ---------------------------------------------------------------------------
// Loading

/// Locate a manifest from an explicit argument or the current directory.
pub fn find_manifest(arg: Option<&str>) -> Result<PathBuf> {
    if let Some(arg) = arg.filter(|a| !a.is_empty()) {
        return [arg.to_string(), format!("{arg}.yaml"), format!("{arg}.yml")]
            .into_iter()
            .map(PathBuf::from)
            .find(|candidate| candidate.is_file())
            .with_context(|| format!("manifest not found: {arg}"));
    }
    let cwd = std::env::current_dir().context("cannot determine the current directory")?;
    let candidates = paths::list_dir(&cwd, |name| {
        name.ends_with(".yaml") || name.ends_with(".yml")
    });
    match candidates.as_slice() {
        [] => bail!(
            "No .yaml file found. Pass one explicitly or run from a directory containing one."
        ),
        [only] => Ok(only.clone()),
        many => {
            let names: Vec<String> = many
                .iter()
                .map(|p| {
                    p.file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .into_owned()
                })
                .collect();
            bail!(
                "Multiple YAML files found; pass one explicitly. Found: {}",
                names.join(", ")
            )
        }
    }
}

/// Read, parse and validate a manifest.
pub fn load(path: &Path) -> Result<Manifest> {
    let text =
        std::fs::read_to_string(path).with_context(|| format!("cannot read {}", path.display()))?;
    let raw: RawManifest = if text.trim().is_empty() {
        RawManifest::default()
    } else {
        serde_yaml::from_str(&text)
            .with_context(|| format!("invalid manifest {}", path.display()))?
    };
    validate(raw, path).with_context(|| format!("invalid manifest {}", path.display()))
}

fn validate(raw: RawManifest, path: &Path) -> Result<Manifest> {
    ensure!(
        raw.api_version.is_some() == raw.kind.is_some(),
        "ApiVersion and Kind must be specified together"
    );
    if let Some(version) = &raw.api_version {
        ensure!(
            version == API_VERSION,
            "unsupported ApiVersion {version:?} (supported: {API_VERSION})"
        );
        let flat = raw.flat_fields();
        ensure!(
            flat.is_empty(),
            "versioned AgentBox manifests must use structured sections; found flat field(s): {}",
            flat.join(", ")
        );
    }

    let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned());
    let name = trimmed(raw.metadata.name.or(raw.name))
        .or(stem)
        .unwrap_or_default();
    ensure!(
        is_name(&name),
        "container name {name:?} must be 1-128 characters, start with an alphanumeric \
         character, and contain only letters, digits, '.', '_' or '-'"
    );

    let Some(image) = trimmed(raw.runtime.image.or(raw.image)) else {
        bail!("an image is required (Image or Runtime.Image)");
    };

    let home_dir = trimmed(raw.workspace.home_dir.or(raw.home_dir)).map(|dir| paths::expand(&dir));
    let home_mount = raw.workspace.home_mount.or(raw.home_mount).unwrap_or(false);

    let extra_flags = match trimmed(raw.extra_flags) {
        Some(flags) => shlex::split(&flags).context("ExtraFlags contains unbalanced quotes")?,
        None => Vec::new(),
    };

    let packages = trimmed_list(raw.runtime.packages.or(raw.packages).unwrap_or_default());
    if let Some(bad) = packages.iter().find(|p| !is_safe_package(p)) {
        bail!("{bad:?} is not a safe package specification");
    }
    let run = trimmed_list(raw.runtime.run.or(raw.run).unwrap_or_default());

    if let Some(key) = raw.runtime.env.keys().find(|k| !is_env_name(k)) {
        bail!("invalid environment variable name {key:?}");
    }
    let workdir = trimmed(raw.runtime.workdir);
    if workdir.as_deref().is_some_and(|w| !w.starts_with('/')) {
        bail!("Runtime.Workdir must be an absolute container path");
    }

    let agent = raw.kind == Some(Kind::AgentBox)
        || matches!(
            raw.security.profile,
            Some(ProfileName::Agent | ProfileName::Restricted)
        );
    for (index, mount) in raw.workspace.mounts.iter().enumerate() {
        ensure!(
            !mount.source.trim().is_empty(),
            "Workspace.Mounts[{index}].Source must not be empty"
        );
        ensure!(
            mount.target.starts_with('/'),
            "Workspace.Mounts[{index}].Target must be an absolute container path"
        );
        if agent {
            let unsafe_options: Vec<&str> = mount
                .options
                .iter()
                .map(String::as_str)
                .filter(|o| !["nodev", "nosuid", "noexec"].contains(o))
                .collect();
            ensure!(
                unsafe_options.is_empty(),
                "unsafe AgentBox mount option(s): {}",
                unsafe_options.join(", ")
            );
        }
    }

    let ttl = match raw.metadata.ttl {
        None => None,
        Some(RawTtl::Seconds(seconds)) => Some(Duration::from_secs(seconds)),
        Some(RawTtl::Human(text)) => {
            Some(humantime::parse_duration(text.trim()).with_context(|| {
                format!("Metadata.TTL {text:?} is not a duration such as '30m'")
            })?)
        }
    };

    let resources = raw.resources;
    if let Some(cpus) = resources.cpus {
        ensure!(
            cpus.is_finite() && cpus > 0.0,
            "Resources.CPUs must be a positive number"
        );
    }
    ensure!(
        resources.pids_limit != Some(0),
        "Resources.PidsLimit must be a positive integer"
    );
    ensure!(
        resources
            .memory
            .as_deref()
            .is_none_or(|m| !m.trim().is_empty()),
        "Resources.Memory must not be empty"
    );
    ensure!(
        raw.metadata.labels.keys().all(|k| !k.is_empty()),
        "Metadata.Labels keys must not be empty"
    );

    let security = raw.security;
    if raw.kind == Some(Kind::AgentBox) {
        ensure!(
            !matches!(
                security.profile,
                Some(ProfileName::Desktop | ProfileName::Default)
            ),
            "AgentBox manifests cannot select a desktop/default profile"
        );
    }
    if agent {
        ensure!(
            security.network != Some(Network::Host),
            "host networking is not allowed with the agent security profile"
        );
        ensure!(
            !home_mount,
            "HomeMount is not allowed with the agent security profile"
        );
        ensure!(
            extra_flags.is_empty(),
            "ExtraFlags are not allowed with the agent security profile"
        );
        ensure!(
            !security.privileged,
            "privileged containers are not allowed with the agent security profile"
        );
        ensure!(
            !security.read_only_root_filesystem || (packages.is_empty() && run.is_empty()),
            "ReadOnlyRootFilesystem cannot be combined with Packages or Run; use a prebuilt image"
        );
    } else {
        ensure!(
            !security.privileged,
            "Security.Privileged is not supported; use an explicit trusted Podman workflow"
        );
    }

    Ok(Manifest {
        path: path.to_path_buf(),
        kind: raw.kind,
        name,
        image: resolve_image(&image),
        home_dir,
        home_mount,
        extra_flags,
        packages,
        run,
        env: raw.runtime.env,
        workdir,
        mounts: raw.workspace.mounts,
        ttl,
        labels: raw.metadata.labels,
        annotations: raw.metadata.annotations,
        resources,
        security,
    })
}

#[cfg(test)]
mod tests;
