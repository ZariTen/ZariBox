from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from zaribox.cli import main
from zaribox.project_state import ProjectStateStore, atomic_write
from zaribox.service import ZariBoxService
from zaribox.shell import CommandResult


class FakeBackend:
    name = "podman"

    def __init__(self) -> None:
        self.containers: dict[str, dict[str, str]] = {}
        self.commands: list[list[str]] = []
        self.fail_exec = False

    def runtime_present(self) -> bool:
        return True

    def container_exists(self, name: str) -> bool:
        return name in self.containers

    def create(
        self, name: str, image: str, home: str, *args: object, **kwargs: object
    ) -> None:
        del home, args
        policy = kwargs["policy"]
        labels = dict(policy.labels)  # type: ignore[attr-defined]
        self.containers[name] = {
            "image": image,
            "io.zaribox.managed": "true",
            **labels,
        }

    def exec(self, name: str, command: list[str], **kwargs: object) -> CommandResult:
        del name, kwargs
        self.commands.append(command)
        if self.fail_exec:
            raise RuntimeError("provision failed")
        return CommandResult(command, 0, "ok", "")

    def image_digest(self, name: str) -> str | None:
        return "sha256:test" if name in self.containers else None

    def label(self, name: str, key: str) -> str | None:
        return self.containers.get(name, {}).get(key)

    def start(self, name: str) -> None:
        del name

    def stop(self, name: str) -> None:
        del name

    def rm(self, name: str) -> None:
        self.containers.pop(name, None)

    def rename(self, name: str, new_name: str) -> None:
        self.containers[new_name] = self.containers.pop(name)

    def post_install(self, name: str, home: str) -> None:
        del name, home

    def is_running(self, name: str) -> bool:
        return name in self.containers


def _manifest(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "agent.yaml"
    path.write_text(
        "ApiVersion: zaribox.dev/v1\n"
        "Kind: AgentBox\n"
        "Metadata:\n  Name: test-agent\n"
        "Runtime:\n  Image: alpine:3.20\n"
        f"{extra}",
        encoding="utf-8",
    )
    return path


def test_ensure_commits_project_state_only_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZARIBOX_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    backend = FakeBackend()
    path = _manifest(tmp_path)

    result = ZariBoxService(cast(Any, backend)).ensure(path)

    assert result.changed is True
    state = ProjectStateStore(path).load()
    assert state is not None
    assert state.container_name == "test-agent"
    assert state.security_profile == "agent"
    assert state.image_digest == "sha256:test"


def test_failed_recreation_restores_previous_container_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZARIBOX_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    backend = FakeBackend()
    path = _manifest(tmp_path)
    service = ZariBoxService(cast(Any, backend))
    service.ensure(path)
    old_state = ProjectStateStore(path).path.read_text(encoding="utf-8")

    path.write_text(
        path.read_text(encoding="utf-8") + "  Run: ['false']\n",
        encoding="utf-8",
    )
    backend.fail_exec = True
    with pytest.raises(RuntimeError, match="provision failed"):
        service.ensure(path, force=True)

    assert "test-agent" in backend.containers
    assert not any("backup-" in name for name in backend.containers)
    assert ProjectStateStore(path).path.read_text(encoding="utf-8") == old_state


def test_exec_is_bounded_and_structured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZARIBOX_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    backend = FakeBackend()
    path = _manifest(tmp_path)
    service = ZariBoxService(cast(Any, backend))
    service.ensure(path)

    result = service.execute(path, ["echo", "hello"], timeout=2, max_output_bytes=128)

    assert result.ok is True
    assert result.stdout == "ok"
    assert backend.commands[-1] == ["echo", "hello"]


def test_atomic_write_replaces_complete_document(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    atomic_write(path, '{"old": true}\n')
    atomic_write(path, '{"new": true}\n')
    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}


def test_validate_json_cli_emits_one_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _manifest(tmp_path)
    assert main(["validate", str(path), "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["ok"] is True
    assert result["data"]["security_profile"] == "agent"
    assert captured.err == ""


def test_json_option_after_exec_delimiter_is_preserved() -> None:
    from zaribox.cli import _move_global_options

    assert _move_global_options(["exec", "box", "--json", "--", "echo", "--json"]) == [
        "--json",
        "exec",
        "box",
        "--",
        "echo",
        "--json",
    ]


def test_agent_profile_cannot_downgrade_to_desktop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZARIBOX_STATE_HOME", str(tmp_path / "state"))
    path = _manifest(tmp_path, "Security:\n  Profile: desktop\n  Network: host\n")
    with pytest.raises(ValueError, match="AgentBox"):
        ZariBoxService(cast(Any, FakeBackend())).validate(path)
