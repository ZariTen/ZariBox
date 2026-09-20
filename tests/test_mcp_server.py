from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from zaribox import __version__
from zaribox.config import load_config
from zaribox.mcp_server import MCPTools, create_server, main
from zaribox.service import ExecResult, InspectionResult, OperationResult, PlanResult


class StubService:
    def __init__(self, manifests: dict[str, Path]) -> None:
        self.manifests = manifests
        self.execute_call: dict[str, object] | None = None
        self.destroyed = False

    def validate(self, path: Path) -> Any:
        return load_config(path)

    def config_for_target(self, target: str | Path) -> Any:
        path = Path(target)
        if path.is_file():
            return load_config(path)
        return load_config(self.manifests[str(target)])

    def plan(self, path: Path) -> PlanResult:
        config = load_config(path)
        return PlanResult("project", config.name, str(path), (), False, "digest")

    def ensure(self, path: Path, *, force: bool = False) -> OperationResult:
        config = load_config(path)
        return OperationResult("operation", True, config.name, ("create",))

    def inspect(self, path: Path) -> InspectionResult:
        config = load_config(path)
        return InspectionResult(
            "project",
            config.name,
            str(path),
            True,
            True,
            (),
            (),
            (),
            (),
            config.image,
            None,
            "agent",
            None,
        )

    def execute(self, path: Path, argv: list[str], **kwargs: object) -> ExecResult:
        self.execute_call = {"path": path, "argv": argv, **kwargs}
        return ExecResult("agent", tuple(argv), 0, "ok", "", False, False)

    def destroy(self, path: Path, *, force: bool) -> OperationResult:
        self.destroyed = force
        return OperationResult("operation", True, load_config(path).name, ("destroy",))

    def list_boxes(self) -> list[dict[str, object]]:
        inside = next(iter(self.manifests.values()))
        return [
            {
                "name": "agent",
                "config_path": str(inside),
                "security_profile": "agent",
            },
            {
                "name": "desktop",
                "config_path": str(inside),
                "security_profile": "default",
            },
            {
                "name": "outside",
                "config_path": str(inside.parent.parent / "outside.yaml"),
                "security_profile": "agent",
            },
        ]


def _manifest(root: Path, *, agent: bool = True) -> Path:
    path = root / ("agent.yaml" if agent else "desktop.yaml")
    if agent:
        path.write_text(
            "ApiVersion: zaribox.dev/v1\n"
            "Kind: AgentBox\n"
            "Metadata:\n  Name: agent\n"
            "Runtime:\n  Image: alpine:3.20\n",
            encoding="utf-8",
        )
    else:
        path.write_text("Name: desktop\nImage: alpine:3.20\n", encoding="utf-8")
    return path


def _tools(root: Path, manifest: Path) -> tuple[MCPTools, StubService]:
    service = StubService({"agent": manifest})
    tools = MCPTools(cast(Any, service), root=root)
    return tools, service


def test_validate_accepts_only_agentbox_inside_root(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    tools, _ = _tools(tmp_path, manifest)

    result = tools.validate("agent.yaml")

    assert result["valid"] is True
    assert result["name"] == "agent"
    assert result["image"] == "docker.io/library/alpine:3.20"


def test_manifest_outside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = _manifest(tmp_path)
    tools, _ = _tools(root, outside)

    with pytest.raises(PermissionError, match="outside the MCP project root"):
        tools.validate(str(outside))


def test_legacy_desktop_manifest_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, agent=False)
    tools, _ = _tools(tmp_path, manifest)

    with pytest.raises(PermissionError, match="only operate on.*AgentBox"):
        tools.validate("desktop.yaml")


def test_execute_is_unprivileged_and_bounded(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    tools, service = _tools(tmp_path, manifest)

    result = tools.execute("agent", ["python", "-V"], timeout=10)

    assert result["exit_code"] == 0
    assert service.execute_call is not None
    assert service.execute_call["as_root"] is False
    assert service.execute_call["timeout"] == 10
    assert service.execute_call["max_output_bytes"] == 1_048_576

    with pytest.raises(ValueError, match="timeout"):
        tools.execute("agent", ["true"], timeout=tools.max_timeout + 1)


def test_remove_requires_explicit_confirmation(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    tools, service = _tools(tmp_path, manifest)

    with pytest.raises(PermissionError, match="confirm=true"):
        tools.remove("agent")
    tools.remove("agent", confirm=True)
    assert service.destroyed is True


def test_server_registers_expected_tools_without_starting_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path)
    tools, _ = _tools(tmp_path, manifest)

    class FakeMCPServer:
        def __init__(self, name: str, **metadata: object) -> None:
            self.name = name
            self.metadata = metadata
            self.tools: list[str] = []
            self.handlers: dict[str, Any] = {}

        def tool(self) -> Any:
            def register(function: Any) -> Any:
                self.tools.append(function.__name__)
                self.handlers[function.__name__] = function
                return function

            return register

    class FakeServerModule:
        MCPServer = FakeMCPServer

    class FakeToolError(Exception):
        pass

    class FakeExceptionsModule:
        ToolError = FakeToolError

    def fake_import(name: str) -> object:
        if name == "mcp.server":
            return FakeServerModule
        if name == "mcp.server.mcpserver.exceptions":
            return FakeExceptionsModule
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("zaribox.mcp_server.import_module", fake_import)
    server = create_server(tools)

    assert server.name == "zaribox"
    assert server.metadata["title"] == "ZariBox"
    assert server.metadata["version"] == __version__
    instructions = str(server.metadata["instructions"])
    assert "zaribox_validate" in instructions
    assert "zaribox_create" in instructions
    assert "allow_destructive=true" in instructions
    assert "confirm=true" in instructions
    assert "interactive shells" in instructions
    assert server.tools == [
        "zaribox_validate",
        "zaribox_plan",
        "zaribox_create",
        "zaribox_status",
        "zaribox_exec",
        "zaribox_remove",
        "zaribox_list",
    ]
    for handler in server.handlers.values():
        assert handler.__doc__ is not None
        assert len(handler.__doc__) > 150
    with pytest.raises(FakeToolError, match="confirm=true"):
        server.handlers["zaribox_remove"]("agent")


def test_create_server_uses_installed_sdk_v2(tmp_path: Path) -> None:
    server_module = pytest.importorskip("mcp.server")
    if not hasattr(server_module, "MCPServer"):
        pytest.skip("MCP Python SDK 2.x is not installed")
    manifest = _manifest(tmp_path)
    tools, _ = _tools(tmp_path, manifest)

    server = create_server(tools)

    assert isinstance(server, server_module.MCPServer)


def test_sdk_v2_client_calls_validate_in_memory(tmp_path: Path) -> None:
    mcp_module = pytest.importorskip("mcp")
    server_module = pytest.importorskip("mcp.server")
    if not hasattr(mcp_module, "Client") or not hasattr(server_module, "MCPServer"):
        pytest.skip("MCP Python SDK 2.x is not installed")
    manifest = _manifest(tmp_path)
    tools, _ = _tools(tmp_path, manifest)

    async def call_tool() -> None:
        async with mcp_module.Client(create_server(tools)) as client:
            assert client.protocol_version == "2026-07-28"
            assert client.server_info is not None
            assert client.server_info.name == "zaribox"
            assert client.server_info.title == "ZariBox"
            assert client.server_info.version == __version__

            listed = await client.list_tools()
            assert {tool.name for tool in listed.tools} == {
                "zaribox_validate",
                "zaribox_plan",
                "zaribox_create",
                "zaribox_status",
                "zaribox_exec",
                "zaribox_remove",
                "zaribox_list",
            }
            assert all(
                tool.input_schema.get("type") == "object" for tool in listed.tools
            )
            exec_tool = next(
                tool for tool in listed.tools if tool.name == "zaribox_exec"
            )
            properties = exec_tool.input_schema.get("properties", {})
            assert "max_output_bytes" not in properties

            result = await client.call_tool(
                "zaribox_validate", {"manifest": "agent.yaml"}
            )
            assert result.is_error is False
            assert result.structured_content["valid"] is True
            assert result.structured_content["name"] == "agent"

    asyncio.run(call_tool())


def test_sdk_v2_returns_actionable_tool_errors(tmp_path: Path) -> None:
    mcp_module = pytest.importorskip("mcp")
    server_module = pytest.importorskip("mcp.server")
    if not hasattr(mcp_module, "Client") or not hasattr(server_module, "MCPServer"):
        pytest.skip("MCP Python SDK 2.x is not installed")
    manifest = _manifest(tmp_path)
    tools, _ = _tools(tmp_path, manifest)

    async def call_tool() -> None:
        async with mcp_module.Client(create_server(tools)) as client:
            result = await client.call_tool(
                "zaribox_remove", {"target": "agent", "confirm": False}
            )
            assert result.is_error is True
            assert "confirm=true" in result.content[0].text

    asyncio.run(call_tool())


def test_main_handles_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    class InterruptedServer:
        def run(self, *, transport: str) -> None:
            assert transport == "stdio"
            raise KeyboardInterrupt

    monkeypatch.setattr("zaribox.mcp_server.create_server", lambda: InterruptedServer())
    assert main() == 130


def test_list_filters_desktop_and_outside_boxes(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    tools, _ = _tools(tmp_path, manifest)

    assert [box["name"] for box in tools.list_boxes()] == ["agent"]
