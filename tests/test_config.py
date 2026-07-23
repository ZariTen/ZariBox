from pathlib import Path

import pytest

from zaribox import __version__
from zaribox.config import _normalize_list, _resolve_image, load_config, resolve_backend
from zaribox.models import ZariConfig
from zaribox.state import StateStore, container_identity_hash, package_drift


def test_version_is_a_non_empty_string() -> None:
    assert isinstance(__version__, str) and __version__


# config._resolve_image


def test_resolve_image_bare_name() -> None:
    assert _resolve_image("archlinux") == "docker.io/library/archlinux:latest"


def test_resolve_image_with_tag() -> None:
    assert _resolve_image("archlinux:rolling") == "archlinux:rolling"


def test_resolve_image_external_registry_unchanged() -> None:
    assert _resolve_image("ghcr.io/user/repo:v1") == "ghcr.io/user/repo:v1"


def test_resolve_image_external_registry_gets_latest() -> None:
    assert _resolve_image("ghcr.io/user/repo") == "ghcr.io/user/repo:latest"


# config._normalize_list


def test_normalize_list_strips_and_filters() -> None:
    assert _normalize_list(["git", " neovim ", "", "  "]) == ["git", "neovim"]


def test_normalize_list_none_returns_empty() -> None:
    assert _normalize_list(None) == []


# config.load_config


def test_load_config_basic(tmp_path: Path) -> None:
    p = tmp_path / "devbox.yaml"
    p.write_text("Name: devbox\nImage: archlinux\nPackages:\n  - git\n  - \" neovim \"\n  - \"\"\n")
    config = load_config(p)
    assert config.name == "devbox"
    assert config.image == "docker.io/library/archlinux:latest"
    assert config.packages == ["git", "neovim"]
    assert config.file_path == p


def test_load_config_name_defaults_to_stem(tmp_path: Path) -> None:
    p = tmp_path / "mybox.yaml"
    p.write_text("Image: archlinux\n")
    assert load_config(p).name == "mybox"


def test_load_config_missing_image_raises(tmp_path: Path) -> None:
    p = tmp_path / "devbox.yaml"
    p.write_text("Name: devbox\n")
    with pytest.raises(ValueError, match="Image field is required"):
        load_config(p)


# config.resolve_backend


def _dummy_config(backend=None) -> ZariConfig:
    return ZariConfig(file_path=Path("x.yaml"), name="box", image="archlinux:latest", backend=backend)


def test_resolve_backend_defaults_to_distrobox(monkeypatch) -> None:
    monkeypatch.delenv("ZARIBOX_BACKEND", raising=False)
    assert resolve_backend(None) == "distrobox"


def test_resolve_backend_env_overrides_config(monkeypatch) -> None:
    monkeypatch.setenv("ZARIBOX_BACKEND", "podman")
    assert resolve_backend(_dummy_config("distrobox")) == "podman"


def test_resolve_backend_invalid_raises(monkeypatch) -> None:
    monkeypatch.setenv("ZARIBOX_BACKEND", "docker")
    with pytest.raises(ValueError, match="Unsupported backend"):
        resolve_backend(None)


# state.container_identity_hash


def test_container_identity_hash_stable() -> None:
    config = _dummy_config()
    assert container_identity_hash(config) == container_identity_hash(config)


def test_container_identity_hash_differs_on_image() -> None:
    c1 = ZariConfig(file_path=Path("x.yaml"), name="box", image="archlinux:latest")
    c2 = ZariConfig(file_path=Path("x.yaml"), name="box", image="ubuntu:latest")
    assert container_identity_hash(c1) != container_identity_hash(c2)


def test_container_identity_hash_normalizes_image() -> None:
    c1 = ZariConfig(file_path=Path("x.yaml"), name="box", image="archlinux:latest")
    c2 = ZariConfig(file_path=Path("x.yaml"), name="box", image="docker.io/library/archlinux:latest")
    assert container_identity_hash(c1) == container_identity_hash(c2)


# state.package_drift


def test_package_drift_no_change() -> None:
    assert package_drift(["git", "curl"], ["curl", "git"]) == ([], [])


def test_package_drift_install_and_remove() -> None:
    to_install, to_remove = package_drift(["git", "curl"], ["git", "vim"])
    assert to_install == ["curl"]
    assert to_remove == ["vim"]


def test_package_drift_duplicates_in_desired() -> None:
    to_install, _ = package_drift(["git", "git", "curl"], ["curl"])
    assert to_install == ["git"]


# state.StateStore


def test_state_store_hash_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    store = StateStore("box")
    assert store.saved_container_hash("box") == ""
    store.save_container_hash("box", "abc123")
    assert store.saved_container_hash("box") == "abc123"


def test_state_store_packages_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    store = StateStore("box")
    assert store.saved_packages("box") == []
    store.save_packages("box", ["vim", "git", "git"])
    assert store.saved_packages("box") == ["git", "vim"]  # deduped and sorted


def test_state_store_clear_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    store = StateStore("box")
    store.save_container_hash("box", "abc")
    store.save_packages("box", ["git"])
    store.clear_cache("box")
    assert store.saved_container_hash("box") == ""
    assert store.saved_packages("box") == []
