//! Small filesystem and environment helpers.

use std::path::{Component, Path, PathBuf};

/// The user's home directory (`$HOME`, falling back to the passwd entry).
pub fn home_dir() -> PathBuf {
    std::env::home_dir().unwrap_or_else(|| PathBuf::from("/"))
}

/// An environment variable that is set and non-empty.
pub fn env_nonempty(key: &str) -> Option<String> {
    std::env::var(key).ok().filter(|value| !value.is_empty())
}

/// Expand a leading `~` and `$VAR`/`${VAR}` references; unknown variables are
/// left untouched.
pub fn expand(text: &str) -> PathBuf {
    let expanded = shellexpand::full_with_context_no_errors(
        text,
        || Some(home_dir().to_string_lossy().into_owned()),
        |name| std::env::var(name).ok(),
    );
    PathBuf::from(expanded.as_ref())
}

/// Lexically normalise a path: drop `.` components and fold `..`.
fn normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            other => out.push(other),
        }
    }
    out
}

/// Absolute, symlink-free form of `path`. Unlike [`std::fs::canonicalize`]
/// the path does not need to exist: the longest existing ancestor is
/// canonicalised and the remainder appended.
pub fn canonical(path: impl AsRef<Path>) -> PathBuf {
    let path = path.as_ref();
    if let Ok(resolved) = std::fs::canonicalize(path) {
        return resolved;
    }
    let absolute = normalize(&std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf()));
    let mut existing = absolute.as_path();
    let mut rest = Vec::new();
    while let Some(parent) = existing.parent() {
        rest.push(existing.file_name().unwrap_or_default().to_owned());
        existing = parent;
        if let Ok(resolved) = std::fs::canonicalize(existing) {
            return rest.iter().rev().fold(resolved, |acc, part| acc.join(part));
        }
    }
    absolute
}

/// Whether `path` is a Unix domain socket.
pub fn is_socket(path: impl AsRef<Path>) -> bool {
    use std::os::unix::fs::FileTypeExt;
    std::fs::metadata(path).is_ok_and(|m| m.file_type().is_socket())
}

/// Entries of `dir` whose file name satisfies `matches`, sorted.
pub fn list_dir(dir: &Path, matches: impl Fn(&str) -> bool) -> Vec<PathBuf> {
    let mut entries: Vec<PathBuf> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|entry| matches(&entry.file_name().to_string_lossy()))
        .map(|entry| entry.path())
        .collect();
    entries.sort();
    entries
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_handles_missing_tails() {
        let base = std::fs::canonicalize(std::env::temp_dir()).unwrap();
        assert_eq!(
            canonical(base.join("zaribox-missing/a/../b")),
            base.join("zaribox-missing/b")
        );
    }

    #[test]
    fn expand_home_and_vars() {
        assert_eq!(expand("~/x"), home_dir().join("x"));
        assert_eq!(
            expand("/a/$ZARIBOX_SURELY_UNSET/b"),
            PathBuf::from("/a/$ZARIBOX_SURELY_UNSET/b")
        );
    }
}
