use super::*;
use tempfile::tempdir;

#[test]
fn scaffolds_desktop_and_rejects_overwrite() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("devbox.yaml");
    let result = scaffold(InitOptions {
        kind: BoxKind::Desktop,
        lang: AgentLang::Python,
        name: Some("archbox".into()),
        force: false,
        path: Some(path.clone()),
    })
    .unwrap();
    assert_eq!(result.name, "archbox");
    assert_eq!(result.template, "desktop-arch");
    assert!(!result.overwritten);
    assert!(path.is_file());

    let manifest = config::load(&path).unwrap();
    assert_eq!(manifest.name, "archbox");
    assert!(!manifest.is_agent_box());

    let err = scaffold(InitOptions {
        kind: BoxKind::Desktop,
        lang: AgentLang::Python,
        name: None,
        force: false,
        path: Some(path.clone()),
    })
    .unwrap_err()
    .to_string();
    assert!(err.contains("--force"), "{err}");

    let again = scaffold(InitOptions {
        kind: BoxKind::Desktop,
        lang: AgentLang::Python,
        name: Some("archbox".into()),
        force: true,
        path: Some(path),
    })
    .unwrap();
    assert!(again.overwritten);
}

#[test]
fn scaffolds_agent_languages() {
    let dir = tempdir().unwrap();
    for (lang, image_needle, template) in [
        (AgentLang::Python, "python:3.12", "agent-python"),
        (AgentLang::Node, "node:22", "agent-node"),
        (AgentLang::Rust, "rust:1-bookworm", "agent-rust"),
    ] {
        let path = dir.path().join(format!("{}.yaml", lang.as_str()));
        let result = scaffold(InitOptions {
            kind: BoxKind::Agent,
            lang,
            name: Some(format!("{}-agent", lang.as_str())),
            force: false,
            path: Some(path.clone()),
        })
        .unwrap();
        assert_eq!(result.template, template);
        let manifest = config::load(&path).unwrap();
        assert!(manifest.is_agent_box());
        assert!(
            manifest.image.contains(image_needle),
            "{} vs {}",
            manifest.image,
            image_needle
        );
        assert_eq!(manifest.profile(), crate::config::Profile::Agent);
    }
}

#[test]
fn default_paths_and_name_validation() {
    let dir = tempdir().unwrap();
    let cwd = std::env::current_dir().unwrap();
    std::env::set_current_dir(dir.path()).unwrap();
    let result = scaffold(InitOptions {
        kind: BoxKind::Agent,
        lang: AgentLang::Python,
        name: None,
        force: false,
        path: None,
    })
    .unwrap();
    assert_eq!(result.path, PathBuf::from("agentbox.yaml"));
    assert_eq!(result.name, "coding-agent");
    std::env::set_current_dir(cwd).unwrap();

    let err = scaffold(InitOptions {
        kind: BoxKind::Desktop,
        lang: AgentLang::Python,
        name: Some("../evil".into()),
        force: true,
        path: Some(dir.path().join("bad.yaml")),
    })
    .unwrap_err()
    .to_string();
    assert!(err.contains("invalid"), "{err}");
}
