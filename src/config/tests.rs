use super::*;

fn load_str(file: &str, body: &str) -> Result<Manifest> {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join(file);
    std::fs::write(&path, body).unwrap();
    load(&path)
}

fn error(body: &str) -> String {
    format!("{:#}", load_str("box.yaml", body).unwrap_err())
}

#[test]
fn resolve_image_variants() {
    for (input, expected) in [
        ("archlinux", "docker.io/library/archlinux:latest"),
        ("archlinux:rolling", "docker.io/library/archlinux:rolling"),
        ("ghcr.io/user/repo:v1", "ghcr.io/user/repo:v1"),
        ("ghcr.io/user/repo", "ghcr.io/user/repo:latest"),
        ("user/repo", "docker.io/user/repo:latest"),
        ("localhost/img", "localhost/img:latest"),
        ("localhost:5000/img", "localhost:5000/img:latest"),
        ("alpine@sha256:abc", "docker.io/library/alpine@sha256:abc"),
    ] {
        assert_eq!(resolve_image(input), expected, "{input}");
    }
}

#[test]
fn desktop_manifest() {
    let manifest = load_str(
        "devbox.yaml",
        "Image: archlinux\nHomeMount: true\nPackages: [git, ' neovim ', '']\n\
         ExtraFlags: --device '/dev/fuse' --cap-add SYS_ADMIN\n",
    )
    .unwrap();
    assert_eq!(manifest.name, "devbox");
    assert_eq!(manifest.image, "docker.io/library/archlinux:latest");
    assert_eq!(manifest.packages, ["git", "neovim"]);
    assert_eq!(
        manifest.extra_flags,
        ["--device", "/dev/fuse", "--cap-add", "SYS_ADMIN"]
    );
    assert_eq!(manifest.profile(), Profile::Default);
    assert_eq!(manifest.network(), Network::Host);
}

#[test]
fn agent_manifest() {
    let manifest = load_str(
        "agent.yaml",
        "ApiVersion: zaribox.dev/v1\nKind: AgentBox\nMetadata:\n  Name: coding-agent\n  TTL: 2h 30m\n\
         Workspace:\n  Mounts:\n    - Source: ./src\n      Target: /workspace\n      ReadOnly: true\n      Options: [nodev]\n\
         Runtime:\n  Image: python:3.12\n  Env:\n    MODE: test\n  Workdir: /workspace\n\
         Resources:\n  CPUs: 1.5\n  PidsLimit: 256\nSecurity:\n  Profile: restricted\n",
    )
    .unwrap();
    assert!(manifest.is_agent_box());
    assert_eq!(manifest.profile(), Profile::Agent);
    assert_eq!(manifest.network(), Network::None);
    assert_eq!(manifest.ttl, Some(Duration::from_secs(9000)));
    assert_eq!(manifest.mounts[0].target, "/workspace");
    assert_eq!(manifest.resources.cpus, Some(1.5));
}

#[test]
fn rejects_invalid_manifests() {
    let agent = "ApiVersion: zaribox.dev/v1\nKind: AgentBox\nRuntime:\n  Image: alpine\n";
    for (body, needle) in [
        (
            "Image: alpine\nBackend: podman\n",
            "unknown field `Backend`",
        ),
        ("Image: alpine\nPackages: git\n", "Packages"),
        ("Image: alpine\nHomeMount: 1\n", "HomeMount"),
        ("Name: ../bad\nImage: alpine\n", "container name"),
        ("Name: devbox\n", "an image is required"),
        (
            "Image: alpine\nResources:\n  GPU: 1\n",
            "unknown field `GPU`",
        ),
        (
            "Image: alpine\nResources:\n  CPUs: 0\n",
            "CPUs must be a positive",
        ),
        ("Image: alpine\nResources:\n  PidsLimit: 1.5\n", "PidsLimit"),
        (
            "Image: alpine\nMetadata:\n  TTL: tomorrow\n",
            "Metadata.TTL",
        ),
        (
            "Image: alpine\nSecurity:\n  Network: bogus\n",
            "unknown variant `bogus`",
        ),
        (
            "Image: alpine\nExtraFlags: \"--x 'y\"\n",
            "unbalanced quotes",
        ),
        (
            "Image: alpine\nSecurity:\n  Privileged: true\n",
            "not supported",
        ),
        (
            "Image: alpine\nWorkspace:\n  Mounts:\n    - Source: .\n      Target: rel\n",
            "absolute container path",
        ),
        (
            "Image: alpine\nWorkspace:\n  Mounts:\n    - Source: .\n      HostPath: .\n      Target: /x\n",
            "duplicate field",
        ),
        ("- a\n", "invalid manifest"),
        ("ApiVersion: zaribox.dev/v1\nImage: alpine\n", "together"),
        (
            "ApiVersion: zaribox.dev/v2\nKind: AgentBox\n",
            "unsupported ApiVersion",
        ),
        (
            "ApiVersion: zaribox.dev/v1\nKind: Other\n",
            "unknown variant `Other`",
        ),
        (
            "ApiVersion: zaribox.dev/v1\nKind: AgentBox\nImage: alpine\n",
            "flat field(s): Image",
        ),
    ] {
        let message = error(body);
        assert!(message.contains(needle), "{body:?}: {message}");
    }
    for (extra, needle) in [
        ("Security:\n  Network: host\n", "host networking"),
        ("Security:\n  Profile: desktop\n", "desktop/default"),
        (
            "Runtime:\n  Image: alpine\n  Packages: ['--force']\n",
            "safe package",
        ),
        (
            "Workspace:\n  Mounts:\n    - Source: .\n      Target: /w\n      Options: [suid]\n",
            "unsafe AgentBox mount option(s): suid",
        ),
    ] {
        let body = if extra.starts_with("Runtime") {
            format!("ApiVersion: zaribox.dev/v1\nKind: AgentBox\n{extra}")
        } else {
            format!("{agent}{extra}")
        };
        let message = error(&body);
        assert!(message.contains(needle), "{body:?}: {message}");
    }
}

#[test]
fn ttl_formats() {
    for (ttl, secs) in [
        ("90s", 90),
        ("1h30m", 5400),
        ("2d", 172_800),
        ("1w", 604_800),
        ("3600", 3600),
    ] {
        let manifest = load_str(
            "box.yaml",
            &format!("Image: alpine\nMetadata:\n  TTL: {ttl}\n"),
        )
        .unwrap();
        assert_eq!(manifest.ttl, Some(Duration::from_secs(secs)), "{ttl}");
    }
}

#[test]
fn bundled_examples_are_valid() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("examples");
    let examples = paths::list_dir(&dir, |name| name.ends_with(".yaml"));
    assert!(!examples.is_empty());
    for example in examples {
        load(&example).unwrap_or_else(|e| panic!("{}: {e:#}", example.display()));
    }
}

#[test]
fn package_specification_grammar() {
    assert!(is_safe_package("libfoo:amd64=1.2~rc1-1"));
    for bad in ["--option", "git curl", "$(id)", "/tmp/pkg"] {
        assert!(!is_safe_package(bad), "{bad}");
    }
}
