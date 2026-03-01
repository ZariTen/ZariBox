from __future__ import annotations

from ..backends import make_backend
from ..config import resolve_backend
from ..logging import CYN, DIM, GRN, RED, RST, err
from ..state import StateStore


def run_list() -> int:
    try:
        backend_name = resolve_backend(None)
        backend = make_backend(backend_name)
    except ValueError as exc:
        err(str(exc))
        return 1

    if not backend.runtime_present():
        err(f"{backend_name} backend is not installed or not in PATH.")
        return 1

    state = StateStore()
    print()
    print(f"{CYN}ZariBox containers{RST}  {DIM}(from {state.cache_dir}){RST}")
    print()

    found_any = False
    for hash_file in sorted(state.cache_dir.glob("*.container.hash")):
        found_any = True
        name = hash_file.name.removesuffix(".container.hash")
        if backend.container_exists(name):
            print(f"  {GRN}+{RST}  {name}")
        else:
            print(f"  {RED}-{RST}  {name}  {DIM}(not running){RST}")

    if not found_any:
        print(f"  {DIM}(none){RST}")

    print()
    return 0
