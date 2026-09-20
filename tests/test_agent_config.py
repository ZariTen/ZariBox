from pathlib import Path

import pytest

from zaribox.config import load_config
from zaribox.models import Mount


def _manifest(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "agent.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_loads_versioned_agent_manifest_into_typed_models(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    path = _manifest(
        tmp_path,
        """\
ApiVersion: zaribox.dev/v1
Kind: AgentBox
Metadata:
  Name: coding-agent
  TTL: 2h30m
  Labels:
    team: platform
Workspace:
  HomeMount: false
  Mounts:
    - Source: ./src
      Target: /workspace
      ReadOnly: true
      Options: [nodev]
Runtime:
  Image: python:3.12
  Packages: [git]
  Run: [python --version]
  Env:
    MODE: test
  Workdir: /workspace
Resources:
  CPUs: 1.5
  Memory: 2GiB
  PidsLimit: 256
Security:
  Network: none
  Profile: restricted
  Privileged: false
  ReadOnlyRootFilesystem: false
""",
    )

    config = load_config(path)

    assert config.api_version == "zaribox.dev/v1"
    assert config.kind == "AgentBox"
    assert config.name == "coding-agent"
    assert config.metadata is not None
    assert config.metadata.ttl == "2h30m"
    assert config.metadata.labels == {"team": "platform"}
    assert config.runtime.image == config.image == "docker.io/library/python:3.12"
    assert config.env == {"MODE": "test"}
    assert config.workdir == "/workspace"
    assert config.mounts == [Mount("./src", "/workspace", True, ("nodev",))]
    assert config.resources.cpus == 1.5
    assert config.resources.memory == "2GiB"
    assert config.resources.pids_limit == 256
    assert config.network == "none"
    assert config.profile == "restricted"
    assert config.security.read_only_root_filesystem is False


def test_unversioned_legacy_manifest_remains_supported(tmp_path: Path) -> None:
    config = load_config(
        _manifest(
            tmp_path,
            "Name: old-box\nImage: alpine\nHomeMount: 'true'\nPackages: [git]\n",
        )
    )

    assert config.api_version is None
    assert config.kind is None
    assert config.name == "old-box"
    assert config.home_mount is True
    assert config.workspace.home_mount is True
    assert config.packages == config.runtime.packages == ["git"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("Image: alpine\nTypo: true\n", "Unknown field"),
        (
            "Image: alpine\nRuntime:\n  Env: []\n",
            "Runtime.Env must be a mapping",
        ),
        (
            "Image: alpine\nWorkspace:\n  Mounts: /tmp:/workspace\n",
            "Workspace.Mounts must be a list",
        ),
        ("Image: alpine\nPackages: git\n", "must be a list of strings"),
        ("Image: alpine\nHomeMount: 1\n", "HomeMount must be a boolean"),
        ("Name: ../bad\nImage: alpine\n", "Container name"),
    ],
)
def test_rejects_unknown_fields_and_malformed_types(
    tmp_path: Path, body: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(_manifest(tmp_path, body))


@pytest.mark.parametrize(
    "header",
    [
        "ApiVersion: zaribox.dev/v2\nKind: AgentBox\n",
        "ApiVersion: zaribox.dev/v1\nKind: MCPServer\n",
        "ApiVersion: zaribox.dev/v1\n",
        "Kind: AgentBox\n",
    ],
)
def test_rejects_incomplete_or_unsupported_manifest_identity(
    tmp_path: Path, header: str
) -> None:
    with pytest.raises(ValueError):
        load_config(_manifest(tmp_path, header + "Image: alpine\n"))


@pytest.mark.parametrize("package", ["--option", "git curl", "$(id)", "/tmp/pkg"])
def test_rejects_unsafe_package_specifications(
    tmp_path: Path, package: str
) -> None:
    path = _manifest(
        tmp_path,
        "ApiVersion: zaribox.dev/v1\n"
        "Kind: AgentBox\n"
        "Runtime:\n"
        "  Image: alpine\n"
        f"  Packages: [{package!r}]\n",
    )

    with pytest.raises(ValueError, match="safe package specification"):
        load_config(path)


def test_accepts_package_version_specifications(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path,
        "ApiVersion: zaribox.dev/v1\n"
        "Kind: AgentBox\n"
        "Runtime:\n"
        "  Image: debian\n"
        "  Packages: ['libfoo:amd64=1.2~rc1-1']\n",
    )

    assert load_config(path).packages == ["libfoo:amd64=1.2~rc1-1"]


def test_rejects_unknown_nested_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"Unknown field.*Resources"):
        load_config(
            _manifest(
                tmp_path,
                "Image: alpine\nResources:\n  Memory: 1GiB\n  GPU: 1\n",
            )
        )


def test_rejects_invalid_mount_and_ttl(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute container path"):
        load_config(
            _manifest(
                tmp_path,
                "Image: alpine\nWorkspace:\n  Mounts:\n    - Source: .\n      Target: workspace\n",
            )
        )

    with pytest.raises(ValueError, match="Metadata.TTL"):
        load_config(_manifest(tmp_path, "Image: alpine\nMetadata:\n  TTL: tomorrow\n"))
