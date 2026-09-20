from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from typing import Any, TypeVar

from . import __version__
from .models import ZariConfig
from .service import ZariBoxService

_ResultT = TypeVar("_ResultT")
_MCP_OUTPUT_LIMIT_BYTES = 1_048_576
_EXPECTED_TOOL_ERRORS = (
    ValueError,
    PermissionError,
    FileNotFoundError,
    RuntimeError,
    OSError,
)


class MCPTools:
    """Safe, project-scoped facade used by the MCP transport adapter."""

    def __init__(
        self,
        service: ZariBoxService | None = None,
        *,
        root: Path | None = None,
        max_timeout: float | None = None,
    ) -> None:
        configured_root = os.environ.get("ZARIBOX_MCP_ROOT")
        self.root = (
            root
            or (Path(configured_root).expanduser() if configured_root else Path.cwd())
        ).resolve()
        if not self.root.is_dir():
            raise ValueError(f"MCP project root does not exist: {self.root}")
        self.service = service or ZariBoxService()
        self.max_timeout = (
            max_timeout
            if max_timeout is not None
            else self._positive_env("ZARIBOX_MCP_MAX_TIMEOUT", 900.0)
        )
        if self.max_timeout <= 0:
            raise ValueError("max_timeout must be positive")

    @staticmethod
    def _positive_env(name: str, default: float) -> float:
        raw = os.environ.get(name, str(default))
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if value <= 0:
            raise ValueError(f"{name} must be positive")
        return value

    def _inside_root(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise PermissionError(
                f"Path is outside the MCP project root '{self.root}': {resolved}"
            )
        return resolved

    def _agent_config(self, target: str, *, manifest: bool) -> ZariConfig:
        if manifest:
            path = Path(target)
            if not path.is_absolute():
                path = self.root / path
            config = self.service.validate(self._inside_root(path))
        else:
            direct = Path(target)
            if (
                direct.is_absolute()
                or direct.is_file()
                or direct.suffix in {".yaml", ".yml"}
                or "/" in target
            ):
                if not direct.is_absolute():
                    direct = self.root / direct
                config = self.service.config_for_target(self._inside_root(direct))
            else:
                config = self.service.config_for_target(target)
            self._inside_root(config.file_path)
        if config.kind != "AgentBox":
            raise PermissionError(
                "MCP tools only operate on versioned AgentBox manifests"
            )
        return config

    def validate(self, manifest: str) -> dict[str, object]:
        config = self._agent_config(manifest, manifest=True)
        return {
            "valid": True,
            "config_path": str(config.file_path.resolve()),
            "name": config.name,
            "image": config.image,
            "security_profile": "agent",
        }

    def plan(self, manifest: str) -> dict[str, object]:
        config = self._agent_config(manifest, manifest=True)
        return asdict(self.service.plan(config.file_path))

    def create(
        self, manifest: str, *, allow_destructive: bool = False
    ) -> dict[str, object]:
        config = self._agent_config(manifest, manifest=True)
        result = self.service.ensure(config.file_path, force=allow_destructive)
        return asdict(result)

    def status(self, target: str) -> dict[str, object]:
        config = self._agent_config(target, manifest=False)
        return asdict(self.service.inspect(config.file_path))

    def execute(
        self,
        target: str,
        argv: list[str],
        *,
        timeout: float = 300.0,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, object]:
        if not argv:
            raise ValueError("argv must contain at least one argument")
        if timeout <= 0 or timeout > self.max_timeout:
            raise ValueError(
                f"timeout must be between 0 and {self.max_timeout} seconds"
            )
        config = self._agent_config(target, manifest=False)
        result = self.service.execute(
            config.file_path,
            argv,
            timeout=timeout,
            max_output_bytes=_MCP_OUTPUT_LIMIT_BYTES,
            as_root=False,
            workdir=workdir,
            env=env,
        )
        return asdict(result)

    def remove(self, target: str, *, confirm: bool = False) -> dict[str, object]:
        if not confirm:
            raise PermissionError("remove requires confirm=true")
        config = self._agent_config(target, manifest=False)
        return asdict(self.service.destroy(config.file_path, force=True))

    def list_boxes(self) -> list[dict[str, object]]:
        boxes: list[dict[str, object]] = []
        for box in self.service.list_boxes():
            raw_path = box.get("config_path")
            if not isinstance(raw_path, str):
                continue
            try:
                self._inside_root(Path(raw_path))
            except PermissionError:
                continue
            if box.get("security_profile") == "agent":
                boxes.append(box)
        return boxes


def create_server(tools: MCPTools | None = None) -> Any:
    """Build an MCP SDK v2 server lazily for the optional stdio integration."""
    try:
        server_module = import_module("mcp.server")
        server_class = server_module.MCPServer
    except (AttributeError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "MCP Python SDK 2.x is required. Install ZariBox with the 'mcp' "
            "extra or provide mcp>=2.0 in the runtime environment."
        ) from exc

    try:
        tool_error_class = import_module("mcp.server.mcpserver.exceptions").ToolError
    except (AttributeError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "The installed MCP package does not provide the SDK v2 ToolError API."
        ) from exc

    facade = tools or MCPTools()
    server = server_class(
        "zaribox",
        title="ZariBox",
        description=(
            "Create and manage isolated, project-scoped ZariBox AgentBox containers."
        ),
        instructions=(
            "Use zaribox_validate to check a manifest and zaribox_plan to preview "
            "changes, "
            "then zaribox_create to create or update the AgentBox. Use zaribox_status "
            "to inspect state and zaribox_exec for bounded, non-interactive work. "
            "Destructive reconciliation requires allow_destructive=true. Removal "
            "requires confirm=true and preserves the dedicated home directory. Tools "
            "only accept versioned AgentBox manifests inside ZARIBOX_MCP_ROOT; desktop "
            "boxes, host-home access, interactive shells, and root command execution "
            "are intentionally unavailable. A target is either an AgentBox name or a "
            "manifest path inside the project root."
        ),
        version=__version__,
    )

    def call_tool(
        function: Callable[..., _ResultT], *args: object, **kwargs: object
    ) -> _ResultT:
        try:
            return function(*args, **kwargs)
        except _EXPECTED_TOOL_ERRORS as exc:
            raise tool_error_class(str(exc)) from exc

    @server.tool()
    def zaribox_validate(manifest: str) -> dict[str, object]:
        """Validate an AgentBox manifest without creating or changing anything.

        `manifest` is a YAML path relative to the configured project root. This checks
        its schema, image, mounts, resource limits, and security policy. The result
        includes the resolved manifest path, container name, image, and security
        profile. Use this first when a manifest may be invalid.
        """
        return call_tool(facade.validate, manifest)

    @server.tool()
    def zaribox_plan(manifest: str) -> dict[str, object]:
        """Preview every action needed to create or update an AgentBox.

        `manifest` is a YAML path inside the project root. This makes no changes. The
        result reports create, recreation, package, and post-install actions plus
        `requires_force`, which indicates whether `zaribox_create` must be called with
        `allow_destructive=true`.
        """
        return call_tool(facade.plan, manifest)

    @server.tool()
    def zaribox_create(
        manifest: str, allow_destructive: bool = False
    ) -> dict[str, object]:
        """Create a missing AgentBox or bring an existing one up to date.

        `manifest` is a YAML path inside the project root. Packages and configuration
        are reconciled with the manifest while the dedicated home directory persists.
        Keep `allow_destructive` false unless a prior plan reports `requires_force` and
        the package removal or container recreation is intentional. Returns the actions
        performed and any warnings.
        """
        return call_tool(
            facade.create, manifest, allow_destructive=allow_destructive
        )

    @server.tool()
    def zaribox_status(target: str) -> dict[str, object]:
        """Report configuration and runtime state for a managed AgentBox.

        `target` is an AgentBox name or manifest path. The result includes existence,
        image, security profile, expiry, configuration sync, desired and applied
        packages, and pending package installation or removal. This makes no changes.
        """
        return call_tool(facade.status, target)

    @server.tool()
    def zaribox_exec(
        target: str,
        argv: list[str],
        timeout: float = 300.0,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """Run a bounded, non-interactive command as the AgentBox user.

        `target` is an AgentBox name or manifest path. Pass the executable and each
        argument separately in `argv`; shell syntax is not interpreted. `timeout`
        defaults to 300 seconds and cannot exceed the server maximum. `workdir` is an
        optional container path and `env` adds command environment values. Output is
        capped at 1 MiB. Returns exit code, stdout, stderr, timeout, and truncation
        state. Root execution and interactive sessions are intentionally unavailable.
        """
        return call_tool(
            facade.execute,
            target,
            argv,
            timeout=timeout,
            workdir=workdir,
            env=env,
        )

    @server.tool()
    def zaribox_remove(target: str, confirm: bool = False) -> dict[str, object]:
        """Remove a managed AgentBox while preserving its dedicated home directory.

        `target` is an AgentBox name or manifest path. Set `confirm=true` explicitly;
        otherwise the operation is rejected. The container and ZariBox state are
        removed, but persistent home data remains available for a later create.
        """
        return call_tool(facade.remove, target, confirm=confirm)

    @server.tool()
    def zaribox_list() -> list[dict[str, object]]:
        """List managed AgentBoxes belonging to the configured project root.

        Returns only agent-profile boxes whose manifests are inside the allowed root.
        Each record describes its name, manifest, image, runtime state, security
        profile, image digest, and expiry when available. This makes no changes.
        """
        return call_tool(facade.list_boxes)

    return server


def main() -> int:
    try:
        server = create_server()
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"zaribox-mcp: {exc}", file=sys.stderr)
        return 1
    try:
        server.run(transport="stdio")
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
