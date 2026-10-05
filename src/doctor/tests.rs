use super::*;

fn hooks_ok(state: PathBuf) -> Hooks {
    Hooks {
        podman_on_path: Box::new(|| true),
        podman_info: Box::new(|| {
            Ok(PodmanInfo {
                rootless: Some(true),
                cgroup_manager: Some("systemd".into()),
                version: Some("5.0.0".into()),
            })
        }),
        uid: Box::new(|| 1000),
        state_root: Box::new({
            let state = state.clone();
            move || state.clone()
        }),
        mcp_root: Box::new(|| None),
        cgroup_fs_present: Box::new(|| true),
    }
}

#[test]
fn all_pass_when_environment_is_healthy() {
    let dir = tempfile::tempdir().unwrap();
    let report = run_with(&hooks_ok(dir.path().join("state")));
    assert!(report.ok, "{report:?}");
    assert!(report.checks.iter().all(|c| c.status != Status::Fail));
    assert!(
        report
            .checks
            .iter()
            .any(|c| c.id == "mcp_root" && c.status == Status::Info)
    );
    let text = render(&report);
    assert!(text.contains("all checks passed"), "{text}");
    assert!(text.contains("Podman on PATH"), "{text}");
    assert!(text.contains("[ok]"), "{text}");
}

#[test]
fn missing_podman_fails() {
    let dir = tempfile::tempdir().unwrap();
    let state = dir.path().join("state");
    let hooks = Hooks {
        podman_on_path: Box::new(|| false),
        podman_info: Box::new(|| unreachable!("podman info should be skipped")),
        uid: Box::new(|| 1000),
        state_root: Box::new(move || state.clone()),
        mcp_root: Box::new(|| None),
        cgroup_fs_present: Box::new(|| true),
    };
    let report = run_with(&hooks);
    assert!(!report.ok);
    let podman = report
        .checks
        .iter()
        .find(|c| c.id == "podman_path")
        .unwrap();
    assert_eq!(podman.status, Status::Fail);
    let info = report
        .checks
        .iter()
        .find(|c| c.id == "podman_info")
        .unwrap();
    assert_eq!(info.status, Status::Fail);
}

#[test]
fn mcp_root_and_state_dir_probes() {
    let dir = tempfile::tempdir().unwrap();
    let state = dir.path().join("state");
    let project = dir.path().join("project");
    std::fs::create_dir_all(&project).unwrap();

    let project_ok = project.clone();
    let hooks = Hooks {
        podman_on_path: Box::new(|| true),
        podman_info: Box::new(|| Ok(PodmanInfo::default())),
        uid: Box::new(|| 1000),
        state_root: Box::new({
            let state = state.clone();
            move || state.clone()
        }),
        mcp_root: Box::new(move || Some(project_ok.to_string_lossy().into_owned())),
        cgroup_fs_present: Box::new(|| true),
    };
    let report = run_with(&hooks);
    assert!(report.ok, "{report:?}");
    assert!(
        report
            .checks
            .iter()
            .any(|c| c.id == "mcp_root" && c.status == Status::Pass)
    );

    let missing = dir.path().join("missing-mcp");
    let hooks = Hooks {
        podman_on_path: Box::new(|| true),
        podman_info: Box::new(|| Ok(PodmanInfo::default())),
        uid: Box::new(|| 1000),
        state_root: Box::new(move || state.clone()),
        mcp_root: Box::new(move || Some(missing.to_string_lossy().into_owned())),
        cgroup_fs_present: Box::new(|| true),
    };
    let report = run_with(&hooks);
    assert!(!report.ok);
    let mcp = report.checks.iter().find(|c| c.id == "mcp_root").unwrap();
    assert_eq!(mcp.status, Status::Fail);
}

#[test]
fn root_uid_warns() {
    let dir = tempfile::tempdir().unwrap();
    let mut hooks = hooks_ok(dir.path().join("state"));
    hooks.uid = Box::new(|| 0);
    let report = run_with(&hooks);
    assert!(report.ok);
    let rootless = report.checks.iter().find(|c| c.id == "rootless").unwrap();
    assert_eq!(rootless.status, Status::Warn);
}

#[test]
fn human_render_is_scannable() {
    crate::logging::set_color_enabled(false);
    let dir = tempfile::tempdir().unwrap();
    let state = dir.path().join("state");
    let hooks = Hooks {
        podman_on_path: Box::new(|| false),
        podman_info: Box::new(|| unreachable!()),
        uid: Box::new(|| 1000),
        state_root: Box::new(move || state.clone()),
        mcp_root: Box::new(|| None),
        cgroup_fs_present: Box::new(|| true),
    };
    let text = render(&run_with(&hooks));
    assert!(text.contains("ZariBox doctor --"), "{text}");
    assert!(text.contains("[fail]"), "{text}");
    assert!(text.contains("Podman on PATH"), "{text}");
    assert!(text.contains("-> "), "{text}");
}
