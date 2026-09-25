use super::*;

#[test]
fn detects_by_image_name() {
    use PackageManager::*;
    for (image, expected) in [
        ("docker.io/library/archlinux:latest", Some(Pacman)),
        ("docker.io/library/debian:12", Some(Apt)),
        ("registry.fedoraproject.org/fedora:40", Some(Dnf)),
        ("docker.io/library/alpine@sha256:abc", Some(Apk)),
        ("docker.io/library/python:3.12", None),
    ] {
        assert_eq!(PackageManager::detect(image), expected, "{image}");
    }
}

#[test]
fn scripts() {
    assert_eq!(install_script(Some(PackageManager::Apk)), r#"apk add "$@""#);
    let probe = install_script(None);
    assert!(probe.starts_with(
        r#"if command -v pacman >/dev/null 2>&1; then exec pacman -Syu --noconfirm "$@"; fi; "#
    ));
    assert!(probe.ends_with("exit 127"));
    assert!(list_script(None).contains("then exec apt-mark showmanual; fi"));
    let status = std::process::Command::new("sh")
        .args(["-n", "-c", &remove_script(None)])
        .status()
        .unwrap();
    assert!(status.success());
}
