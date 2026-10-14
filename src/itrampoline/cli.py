import argparse
import logging
import sys
from pathlib import Path

from .errors import TrampolineError
from .install import InstallSession
from .stock import DEFAULT_SLOT, SLOTS, CoreSlot


logger = logging.getLogger(__name__)


def ask_slot() -> CoreSlot:
    """Ask the user which stock platform core the trampoline may replace."""
    sys.stderr.write(
        "Choose the stock core to replace "
        "(niche platforms first; this is not a measured ranking):\n"
    )

    for number, slot in enumerate(SLOTS, 1):
        default = " [default]" if slot == DEFAULT_SLOT else ""
        sys.stderr.write(f"{number:2}. type {slot.type_id:2}: {slot.platform} — {slot.filename}{default}\n")

    sys.stderr.write("Number [1]: ")
    sys.stderr.flush()
    answer = input().strip()

    if not answer:
        return DEFAULT_SLOT

    if not answer.isascii() or not answer.isdecimal() or not 1 <= int(answer) <= len(SLOTS):
        raise TrampolineError("Invalid core number")

    return SLOTS[int(answer) - 1]


def main(argv: list[str] | None = None) -> int:
    """Install the selected trampoline on the mounted SD and return an exit code."""
    parser = argparse.ArgumentParser(
        description="Install a stock-menu trampoline on an existing mounted SD", allow_abbrev=False,
    )
    parser.add_argument("sd_path", type=Path, help="path to the mounted SD root (stock game/games.db required)")
    parser.add_argument(
        "--no-ask", action="store_true", help="use defaults without questions; refuse conflicting files",
    )
    arguments = parser.parse_args(argv)

    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr)

    try:
        slot = DEFAULT_SLOT if arguments.no_ask else ask_slot()

        with InstallSession(arguments.sd_path, slot) as session:
            conflicts = session.get_conflicts()
            allow_replace = False

            if conflicts and not arguments.no_ask:
                sys.stderr.write("Replacement changes the stock launch path for all games of this type.\n")
                sys.stderr.write("Conflicting files: " + ", ".join(conflicts) + "\nReplace them? [y/N]: ")
                sys.stderr.flush()
                allow_replace = input().strip().lower() == "y"

            session.apply(backup_root=Path.cwd() / "itrampoline-backups", allow_replace=allow_replace)

        logger.info("Ready: select 'iboxed trampoline' in the stock GUI")
        return 0

    except (TrampolineError, OSError) as error:
        logger.error("%s", error)
        return 1

    except (KeyboardInterrupt, EOFError):
        logger.warning("Cancelled; completed file replacements are not automatically rolled back")
        return 130
