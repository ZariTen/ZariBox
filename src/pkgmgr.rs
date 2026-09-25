//! Distribution package managers used to reconcile `Packages`.

/// Package manager inside a container image.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PackageManager {
    Pacman,
    Apt,
    Dnf,
    Zypper,
    Apk,
    Xbps,
}

impl PackageManager {
    /// Probe order when the image name does not identify the distribution.
    pub const ALL: [Self; 6] = [
        Self::Pacman,
        Self::Apt,
        Self::Dnf,
        Self::Zypper,
        Self::Apk,
        Self::Xbps,
    ];

    /// Infer the manager from the image name; `None` means probe at runtime.
    pub fn detect(image: &str) -> Option<Self> {
        let lower = image.to_lowercase();
        let name = lower
            .rsplit('/')
            .next()
            .and_then(|last| last.split([':', '@']).next())
            .unwrap_or_default();
        let prefixes: [(&[&str], Self); 6] = [
            (&["arch", "manjaro", "endeavour"], Self::Pacman),
            (&["ubuntu", "debian", "pop", "mint"], Self::Apt),
            (&["fedora", "centos", "rhel"], Self::Dnf),
            (&["opensuse", "suse"], Self::Zypper),
            (&["alpine"], Self::Apk),
            (&["void"], Self::Xbps),
        ];
        prefixes
            .into_iter()
            .find(|(names, _)| names.iter().any(|prefix| name.starts_with(prefix)))
            .map(|(_, manager)| manager)
    }

    fn binary(self) -> &'static str {
        match self {
            Self::Pacman => "pacman",
            Self::Apt => "apt-get",
            Self::Dnf => "dnf",
            Self::Zypper => "zypper",
            Self::Apk => "apk",
            Self::Xbps => "xbps-install",
        }
    }

    /// Install script; packages are passed as positional `"$@"` arguments.
    fn install(self) -> &'static str {
        match self {
            Self::Pacman => r#"pacman -Syu --noconfirm "$@""#,
            Self::Apt => r#"apt-get install -y "$@""#,
            Self::Dnf => r#"dnf install -y "$@""#,
            Self::Zypper => r#"zypper install -y "$@""#,
            Self::Apk => r#"apk add "$@""#,
            Self::Xbps => r#"xbps-install -y "$@""#,
        }
    }

    fn remove(self) -> &'static str {
        match self {
            Self::Pacman => r#"pacman -Rns --noconfirm "$@""#,
            Self::Apt => r#"apt-get remove -y "$@""#,
            Self::Dnf => r#"dnf remove -y "$@""#,
            Self::Zypper => r#"zypper remove -y "$@""#,
            Self::Apk => r#"apk del "$@""#,
            Self::Xbps => r#"xbps-remove -y "$@""#,
        }
    }

    /// Command listing explicitly installed packages (first column is the name).
    fn list(self) -> &'static str {
        match self {
            Self::Pacman => "pacman -Qen",
            Self::Apt => "apt-mark showmanual",
            Self::Dnf => "dnf repoquery --userinstalled",
            Self::Zypper => "zypper packages --userinstalled",
            Self::Apk => "apk info -q",
            Self::Xbps => "xbps-query -m",
        }
    }
}

fn script(manager: Option<PackageManager>, pick: fn(PackageManager) -> &'static str) -> String {
    match manager {
        Some(manager) => pick(manager).to_string(),
        None => {
            let mut script: String = PackageManager::ALL
                .iter()
                .map(|m| {
                    format!(
                        "if command -v {} >/dev/null 2>&1; then exec {}; fi; ",
                        m.binary(),
                        pick(*m)
                    )
                })
                .collect();
            script.push_str("echo 'No supported package manager found' >&2; exit 127");
            script
        }
    }
}

/// `sh -c` script installing the packages given as positional arguments.
pub fn install_script(manager: Option<PackageManager>) -> String {
    script(manager, PackageManager::install)
}

/// `sh -c` script removing the packages given as positional arguments.
pub fn remove_script(manager: Option<PackageManager>) -> String {
    script(manager, PackageManager::remove)
}

/// `sh -c` script listing explicitly installed packages.
pub fn list_script(manager: Option<PackageManager>) -> String {
    script(manager, PackageManager::list)
}

#[cfg(test)]
mod tests;
