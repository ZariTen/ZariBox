import re
from pathlib import Path

from ..backends import PodmanBackend
from ..logging import err, ok, step
from ..models import ZariConfig
from ..pkgmgr import detect_pkgmgr, list_cmd
from ..project_state import atomic_write
from ._common import load_container_context, require_runtime


def _fetch_installed_packages(
    backend: PodmanBackend, name: str, image: str
) -> list[str]:
    mgr = detect_pkgmgr(image)
    cmd = list_cmd(mgr)
    result = backend.exec(name, cmd, as_user=False, capture_output=True)
    return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]


def _merge_into_config(
    yaml_path: Path, config: ZariConfig, packages: list[str]
) -> list[str]:
    existing = set(config.packages)
    incoming = set(packages)
    added = sorted(incoming - existing)
    if not added:
        return []
    merged = sorted(existing | incoming)

    text = yaml_path.read_text(encoding="utf-8")
    block = "Packages:\n" + "".join(f"  - {package}\n" for package in merged)
    if re.search(r"^Packages:", text, re.MULTILINE):
        text = re.sub(
            r"^Packages:.*?(?=^\S|\Z)", block, text, flags=re.MULTILINE | re.DOTALL
        )
    else:
        text = text.rstrip("\n") + "\n" + block
    atomic_write(yaml_path, text)
    return added


def run_pull(container_name: str) -> int:
    context = load_container_context(container_name)
    if context is None:
        return 1
    if not require_runtime(context.backend_name, context.backend):
        return 1

    name = context.config.name
    if not context.backend.container_exists(name):
        err(f"Container '{name}' does not exist. Run 'zaribox create' first.")
        return 1

    try:
        step("Fetching explicitly installed packages...")
        packages = _fetch_installed_packages(
            context.backend, name, context.config.image
        )
    except RuntimeError as exc:
        err(str(exc))
        return 1

    added = _merge_into_config(context.yaml_path, context.config, packages)

    if not added:
        ok("Nothing new — packages file already up to date.")
        return 0

    ok(f"Added {len(added)} package(s) to {context.yaml_path.name}: {' '.join(added)}")
    return 0
