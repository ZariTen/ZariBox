import sys

from . import __version__
from .commands.apply import run_sync
from .commands.create import run_create
from .commands.enter import run_enter
from .commands.export import run_pull
from .commands.list import run_list
from .commands.remove import run_destroy
from .commands.status import run_status


def main() -> int:
    args = sys.argv[1:]

    commands = {
        "create": (
            "Create a new container from a config file",
            lambda: run_create(args[1] if len(args) > 1 else None),
        ),
        "status": ("Show sync status with package drift", lambda: run_status(args[1])),
        "list": ("List all ZariBox-managed containers", lambda: run_list()),
        "apply": ("Sync container to match config", lambda: run_sync(args[1])),
        "export": (
            "Sync packages from container into config file",
            lambda: run_pull(args[1]),
        ),
        "enter": ("Enter container (auto-apply if needed)", lambda: run_enter(args[1])),
        "remove": (
            "Remove container (home dir preserved)",
            lambda: run_destroy(args[1]),
        ),
    }

    if not args or args[0] in ("-h", "--help", "help"):
        print(f"ZariBox v{__version__}")
        print("\nUsage: zaribox <command> [arg]\n")
        print("Commands:")
        for name, (description, _) in commands.items():
            print(f"  {name:<10} {description}")
        print()
        return 0

    command = args[0]

    if command in commands:
        try:
            _, handler = commands[command]
            return handler()
        except IndexError:
            print(f"Error: Command '{command}' requires a target argument.")
            return 1

    print(f"Unknown command: {command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
