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
