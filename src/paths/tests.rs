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
