from __future__ import annotations

from ..backends import make_backend
from ..config import resolve_backend
from ..logging import CYN, DIM, GRN, RED, RST, err
from ..state import StateStore
from ._common import require_runtime


def run_list() -> int:
    try:
        backend_name = resolve_backend(None)
        backend = make_backend(backend_name)
    except ValueError as exc:
        err(str(exc))
        return 1

    if not require_runtime(backend_name, backend):
        return 1

    state = StateStore()
    print()
    print(f"{CYN}ZariBox containers{RST}  {DIM}(from {state.cache_dir}){RST}")
    print()

    found_any = False
    for container_dir in state.cache_dir.iterdir():
        for hash_file in sorted(container_dir.glob("*.hash")):
            found_any = True
            name = hash_file.name.removesuffix(".hash")
            if backend.container_exists(name):
                print(f"  {GRN}+{RST}  {name}")
            else:
                print(f"  {RED}-{RST}  {name}  {DIM}(not running){RST}")

    if not found_any:
        print(f"  {DIM}(none){RST}")

    print()
    return 0
