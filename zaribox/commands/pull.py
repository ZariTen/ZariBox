import re

from ..backends import Backend
from ..config import load_context
from ..logging import err, ok, step
from ..pkgmgr import detect_pkgmgr, list_cmd
from ..state import StateStore


def _fetch_installed_packages(backend: Backend, name: str, image: str) -> list[str]:
    mgr = detect_pkgmgr(image)
    cmd = list_cmd(mgr)
    step(f"Fetching explicitly installed packages via {mgr}...")
    result = backend.exec(name, cmd, as_user=False, capture_output=True)
    return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]


def _merge_into_config(yaml_path, config, packages: list[str]) -> list[str]:
    existing = set(config.packages)
    incoming = set(packages)
    added = sorted(incoming - existing)
    if not added:
        return []
    merged = sorted(incoming)

    text = yaml_path.read_text()
    block = "Packages:\n" + "".join(f"  - {p}\n" for p in merged)
    if re.search(r"^Packages:", text, re.MULTILINE):
        text = re.sub(
            r"^Packages:.*?(?=^\S|\Z)", block, text, flags=re.MULTILINE | re.DOTALL
        )
    else:
        text = text.rstrip("\n") + "\n" + block
    yaml_path.write_text(text)
    return added


def run_pull(container_name: str) -> int:
    try:
        state = StateStore(container_name)
        resolved = state.yaml_path_for(container_name)
        yaml_path, config, backend_name, backend = load_context(resolved)
    except (ValueError, RuntimeError) as exc:
        err(str(exc))
        return 1

    if not backend.runtime_present():
        err(f"{backend_name} is not installed or not in PATH.")
        return 1

    name = config.name
    if not backend.container_exists(name):
        err(f"Container '{name}' does not exist. Run 'zaribox create' first.")
        return 1

    try:
        packages = _fetch_installed_packages(backend, name, config.image)
    except RuntimeError as exc:
        err(str(exc))
        return 1

    added = _merge_into_config(yaml_path, config, packages)

    if not added:
        ok("Nothing new — packages file already up to date.")
        return 0

    ok(f"Added {len(added)} package(s) to {yaml_path.name}: {' '.join(added)}")
    return 0
