from pathlib import Path

from zaribox import __version__
from zaribox.config import load_config
from zaribox.state import StateStore, container_identity_hash, package_drift


def test_version_is_a_string() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_load_config_normalizes_basic_fields(tmp_path: Path) -> None:
    yaml_path = tmp_path / "devbox.yaml"
    yaml_path.write_text(
        """\
Name: devbox
Image: archlinux
Packages:
  - git
  - " neovim "
  - ""
Run:
  - echo hello
""",
        encoding="utf-8",
    )

    config = load_config(yaml_path)

    assert config.file_path == yaml_path
    assert config.name == "devbox"
    assert config.image == "docker.io/library/archlinux:latest"
    assert config.packages == ["git", "neovim"]
    assert config.run == ["echo hello"]


def test_state_helpers_track_identity_and_drift(tmp_path: Path) -> None:
    store = StateStore(tmp_path)
    yaml_path = tmp_path / "devbox.yaml"
    yaml_path.write_text("Image: archlinux\n", encoding="utf-8")

    config = load_config(yaml_path)

    store.save_container_hash(config.name, container_identity_hash(config))
    store.save_packages(config.name, ["vim", "git", "git"])

    assert store.saved_container_hash(config.name) == container_identity_hash(config)
    assert store.saved_packages(config.name) == ["git", "vim"]
    assert package_drift(["git", "curl"], store.saved_packages(config.name)) == (
        ["curl"],
        ["vim"],
    )