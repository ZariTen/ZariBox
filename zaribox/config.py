from __future__ import annotations

import os
import re
from pathlib import Path
from typing import cast

import yaml

from .backends import PodmanBackend
from .models import (
    Metadata,
    Mount,
    Resources,
    Runtime,
    Security,
    Workspace,
    ZariConfig,
)

_API_VERSION = "zaribox.dev/v1"
_KIND = "AgentBox"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TTL_RE = re.compile(r"^(?:\d+(?:\.\d+)?[smhdw])+$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Package entries become arguments to a root-run package manager. Keep them to
# package/version syntax so a manifest cannot smuggle package-manager options.
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:@/~=-]*$")
_TOP_LEVEL_FIELDS = {
    "ApiVersion",
    "Kind",
    "Metadata",
    "Workspace",
    "Runtime",
    "Resources",
    "Security",
    # Legacy flat fields remain valid in unversioned and versioned manifests.
    "Name",
    "Image",
    "HomeDir",
    "HomeMount",
    "ExtraFlags",
    "Packages",
    "Run",
}


def load_context(
    yaml_arg: str | Path | None,
) -> tuple[Path, ZariConfig, PodmanBackend]:
    yaml_path = resolve_yaml(yaml_arg)
    config = load_config(yaml_path)
    return yaml_path, config, PodmanBackend()


def _resolve_image(image: str) -> str:
    digest = "@" in image
    registry_part = image.split("@", 1)[0]
    first = registry_part.split("/")[0]
    if "/" not in registry_part:
        image = f"docker.io/library/{image}"
    elif "." not in first and ":" not in first and first != "localhost":
        image = f"docker.io/{image}"
    if not digest and ":" not in image.split("/")[-1]:
        image = f"{image}:latest"
    return image


def resolve_yaml(arg: str | Path | None) -> Path:
    if arg:
        direct = Path(arg)
        if direct.is_file():
            return direct

        candidate_yaml = Path(f"{arg}.yaml")
        if candidate_yaml.is_file():
            return candidate_yaml

        candidate_yml = Path(f"{arg}.yml")
        if candidate_yml.is_file():
            return candidate_yml

    candidates = sorted(Path.cwd().glob("*.yaml")) + sorted(Path.cwd().glob("*.yml"))
    if not candidates:
        raise ValueError(
            "No .yaml file found. Pass one explicitly or run from a directory containing one."
        )
    if len(candidates) > 1:
        choices = ", ".join(path.name for path in candidates)
        raise ValueError(
            "Multiple YAML files found. Pass one explicitly (for example: "
            + f"'zaribox status {candidates[0].name}'). Found: {choices}"
        )
    return candidates[0]


def _load_yaml(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as stream:
        loaded_obj = cast(object, yaml.safe_load(stream))

    if loaded_obj is None:
        return {}
    if not isinstance(loaded_obj, dict):
        raise ValueError(  # noqa: TRY004 - manifest errors use ValueError publicly
            f"Top-level YAML document must be a mapping: {path}"
        )

    loaded = cast(dict[object, object], loaded_obj)
    if any(not isinstance(key, str) for key in loaded):
        raise ValueError(f"Top-level field names must be strings: {path}")
    return cast(dict[str, object], loaded)


def _normalize_list(value: object) -> list[str]:
    """Normalize a legacy string list; strict parsing is done by _string_list."""
    if value is None:
        return []
    if isinstance(value, list):
        items = cast(list[object], value)
        return [str(item).strip() for item in items if str(item).strip()]
    return []


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(  # noqa: TRY004 - manifest errors use ValueError publicly
            f"{field_name} must be a mapping"
        )
    result = cast(dict[object, object], value)
    if any(not isinstance(key, str) for key in result):
        raise ValueError(f"{field_name} field names must be strings")
    return cast(dict[str, object], result)


def _section(raw: dict[str, object], name: str) -> dict[str, object]:
    if name not in raw:
        return {}
    return _mapping(raw[name], name)


def _reject_unknown(mapping: dict[str, object], allowed: set[str], path: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        fields = ", ".join(unknown)
        raise ValueError(f"Unknown field(s) in {path}: {fields}")


def _string(value: object, field_name: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004 - manifest errors use ValueError publicly
            f"{field_name} must be a string"
        )
    result = value.strip()
    if not result:
        if required:
            raise ValueError(f"{field_name} must be a non-empty string")
        return None
    return result


def _boolean(value: object, field_name: str, *, legacy_strings: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if (
        legacy_strings
        and isinstance(value, str)
        and value.strip().lower() in {"true", "false"}
    ):
        return value.strip().lower() == "true"
    raise ValueError(f"{field_name} must be a boolean")


def _string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(  # noqa: TRY004 - manifest errors use ValueError publicly
            f"{field_name} must be a list of strings"
        )
    result: list[str] = []
    for index, item in enumerate(cast(list[object], value)):
        if not isinstance(item, str):
            raise ValueError(  # noqa: TRY004 - manifest errors use ValueError publicly
                f"{field_name}[{index}] must be a string"
            )
        item = item.strip()
        if item:
            result.append(item)
    return result


def _package_list(value: object, field_name: str) -> list[str]:
    packages = _string_list(value, field_name)
    for index, package in enumerate(packages):
        if not _PACKAGE_RE.fullmatch(package):
            raise ValueError(
                f"{field_name}[{index}] is not a safe package specification: "
                f"{package!r}"
            )
    return packages


def _string_map(value: object, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    mapping = _mapping(value, field_name)
    result: dict[str, str] = {}
    for key, item in mapping.items():
        if not key or not isinstance(item, str):
            raise ValueError(f"{field_name} must map non-empty string keys to strings")
        if field_name == "Runtime.Env" and not _ENV_NAME_RE.fullmatch(key):
            raise ValueError(f"Invalid environment variable name: {key}")
        result[key] = item
    return result


def _parse_mounts(value: object) -> list[Mount]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(  # noqa: TRY004 - manifest errors use ValueError publicly
            "Workspace.Mounts must be a list of mappings"
        )

    mounts: list[Mount] = []
    for index, item in enumerate(cast(list[object], value)):
        path = f"Workspace.Mounts[{index}]"
        mount = _mapping(item, path)
        _reject_unknown(
            mount,
            {"Source", "Target", "HostPath", "ContainerPath", "ReadOnly", "Options"},
            path,
        )
        if "Source" in mount and "HostPath" in mount:
            raise ValueError(f"{path} cannot contain both Source and HostPath")
        if "Target" in mount and "ContainerPath" in mount:
            raise ValueError(f"{path} cannot contain both Target and ContainerPath")
        source = _string(
            mount.get("Source", mount.get("HostPath")), f"{path}.Source", required=True
        )
        target = _string(
            mount.get("Target", mount.get("ContainerPath")),
            f"{path}.Target",
            required=True,
        )
        assert source is not None and target is not None
        if not target.startswith("/"):
            raise ValueError(f"{path}.Target must be an absolute container path")
        read_only = (
            _boolean(mount["ReadOnly"], f"{path}.ReadOnly")
            if "ReadOnly" in mount
            else False
        )
        options = tuple(_string_list(mount.get("Options"), f"{path}.Options"))
        mounts.append(Mount(source, target, read_only, options))
    return mounts


def _parse_ttl(value: object) -> str | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - manifest errors use ValueError publicly
            "Metadata.TTL must be a non-negative integer or duration string"
        )
    if isinstance(value, int):
        if value < 0:
            raise ValueError("Metadata.TTL must not be negative")
        return value
    if isinstance(value, str) and _TTL_RE.fullmatch(value.strip()):
        return value.strip()
    raise ValueError(
        "Metadata.TTL must be a non-negative integer (seconds) or duration such as '30m'"
    )


def _validate_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            "Container name must be 1-128 characters, start with an alphanumeric "
            "character, and contain only letters, digits, '.', '_' or '-'"
        )
    return name


def load_config(path: Path) -> ZariConfig:
    raw = _load_yaml(path)
    _reject_unknown(raw, _TOP_LEVEL_FIELDS, "manifest")

    has_version = "ApiVersion" in raw
    has_kind = "Kind" in raw
    if has_version != has_kind:
        raise ValueError("ApiVersion and Kind must be specified together")

    api_version: str | None = None
    kind: str | None = None
    if has_version:
        legacy_fields = {
            "Name",
            "Image",
            "HomeDir",
            "HomeMount",
            "ExtraFlags",
            "Packages",
            "Run",
        }
        present_legacy = sorted(legacy_fields & set(raw))
        if present_legacy:
            raise ValueError(
                "Versioned AgentBox manifests must use structured sections; legacy field(s): "
                + ", ".join(present_legacy)
            )
        api_version = _string(raw["ApiVersion"], "ApiVersion", required=True)
        kind = _string(raw["Kind"], "Kind", required=True)
        if api_version != _API_VERSION:
            raise ValueError(
                f"Unsupported ApiVersion: {api_version!r}. Supported: {_API_VERSION}"
            )
        if kind != _KIND:
            raise ValueError(f"Unsupported Kind: {kind!r}. Supported: {_KIND}")

    metadata_raw = _section(raw, "Metadata")
    workspace_raw = _section(raw, "Workspace")
    runtime_raw = _section(raw, "Runtime")
    resources_raw = _section(raw, "Resources")
    security_raw = _section(raw, "Security")
    _reject_unknown(metadata_raw, {"Name", "TTL", "Labels", "Annotations"}, "Metadata")
    _reject_unknown(workspace_raw, {"HomeDir", "HomeMount", "Mounts"}, "Workspace")
    _reject_unknown(
        runtime_raw,
        {"Image", "Packages", "Run", "Env", "Workdir"},
        "Runtime",
    )
    _reject_unknown(resources_raw, {"CPUs", "Memory", "PidsLimit"}, "Resources")
    _reject_unknown(
        security_raw,
        {"Network", "Profile", "Privileged", "ReadOnlyRootFilesystem"},
        "Security",
    )

    name_value = metadata_raw.get("Name", raw.get("Name", path.stem))
    name = _string(name_value, "Metadata.Name", required=True)
    assert name is not None
    name = _validate_name(name)

    image_value = runtime_raw.get("Image", raw.get("Image"))
    if image_value is None:
        raise ValueError(f"Image field is required in {path}")
    image = _string(image_value, "Runtime.Image", required=True)
    assert image is not None
    image = _resolve_image(image)


    home_dir_value = workspace_raw.get("HomeDir", raw.get("HomeDir"))
    home_dir = _string(home_dir_value, "Workspace.HomeDir")
    if home_dir is not None:
        home_dir = os.path.expandvars(home_dir)

    if "HomeMount" in workspace_raw:
        home_mount = _boolean(workspace_raw["HomeMount"], "Workspace.HomeMount")
    elif "HomeMount" in raw:
        home_mount = _boolean(raw["HomeMount"], "HomeMount", legacy_strings=True)
    else:
        home_mount = False

    extra_flags = _string(raw.get("ExtraFlags"), "ExtraFlags") or ""
    packages = _package_list(
        runtime_raw.get("Packages", raw.get("Packages")), "Runtime.Packages"
    )
    run = _string_list(runtime_raw.get("Run", raw.get("Run")), "Runtime.Run")
    env = _string_map(runtime_raw.get("Env"), "Runtime.Env")
    workdir = _string(runtime_raw.get("Workdir"), "Runtime.Workdir")
    if workdir is not None and not workdir.startswith("/"):
        raise ValueError("Runtime.Workdir must be an absolute container path")

    workspace = Workspace(
        home_dir=home_dir,
        home_mount=home_mount,
        mounts=_parse_mounts(workspace_raw.get("Mounts")),
    )
    runtime = Runtime(
        image=image,
        packages=packages,
        run=run,
        env=env,
        workdir=workdir,
    )

    cpus: float | None = None
    if "CPUs" in resources_raw:
        cpus_value = resources_raw["CPUs"]
        if isinstance(cpus_value, bool) or not isinstance(cpus_value, (int, float)):
            raise ValueError("Resources.CPUs must be a positive number")
        cpus = float(cpus_value)
        if cpus <= 0:
            raise ValueError("Resources.CPUs must be a positive number")

    memory = _string(resources_raw.get("Memory"), "Resources.Memory")
    pids_limit: int | None = None
    if "PidsLimit" in resources_raw:
        pids_value = resources_raw["PidsLimit"]
        if (
            isinstance(pids_value, bool)
            or not isinstance(pids_value, int)
            or pids_value <= 0
        ):
            raise ValueError("Resources.PidsLimit must be a positive integer")
        pids_limit = pids_value
    resources = Resources(cpus=cpus, memory=memory, pids_limit=pids_limit)

    network = _string(security_raw.get("Network"), "Security.Network")
    if network is not None and network not in {
        "none",
        "private",
        "slirp4netns",
        "pasta",
        "host",
    }:
        raise ValueError(
            "Security.Network must be one of: none, private, slirp4netns, pasta, host"
        )
    profile = _string(security_raw.get("Profile"), "Security.Profile")
    if profile is not None and profile.lower() not in {
        "agent",
        "restricted",
        "desktop",
        "default",
    }:
        raise ValueError(
            "Security.Profile must be one of: agent, restricted, desktop, default"
        )
    privileged = (
        _boolean(security_raw["Privileged"], "Security.Privileged")
        if "Privileged" in security_raw
        else False
    )
    read_only_root = (
        _boolean(
            security_raw["ReadOnlyRootFilesystem"],
            "Security.ReadOnlyRootFilesystem",
        )
        if "ReadOnlyRootFilesystem" in security_raw
        else False
    )
    security = Security(
        network=network,
        profile=profile,
        privileged=privileged,
        read_only_root_filesystem=read_only_root,
    )

    metadata = Metadata(
        name=name,
        ttl=_parse_ttl(metadata_raw.get("TTL")),
        labels=_string_map(metadata_raw.get("Labels"), "Metadata.Labels"),
        annotations=_string_map(
            metadata_raw.get("Annotations"), "Metadata.Annotations"
        ),
    )

    return ZariConfig(
        file_path=path,
        name=name,
        image=image,
        home_dir=home_dir,
        home_mount=home_mount,
        extra_flags=extra_flags,
        packages=packages,
        run=run,
        api_version=api_version,
        kind=kind,
        metadata=metadata,
        workspace=workspace,
        runtime=runtime,
        resources=resources,
        security=security,
    )
