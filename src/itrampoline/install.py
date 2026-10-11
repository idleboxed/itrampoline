import hashlib
import json
import logging
import os
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Self

from .build import build_runtime
from .database import patch_database
from .errors import TrampolineError
from .filesystem import FileSnapshot, SDTree, open_absolute_directory, split_relative
from .stock import DATABASE_PATH, HELPER_PATH, TOKEN_CONTENT, TOKEN_PATH, CoreSlot


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlannedFile:
    path: str
    content: bytes
    mode: int
    before: FileSnapshot | None

    def should_write(self) -> bool:
        return self.before is None or self.content != self.before.content or (
            self.path == HELPER_PATH and not self.before.mode & 0o111
        )


@dataclass(frozen=True)
class InstallPlan:
    slot: CoreSlot
    files: tuple[PlannedFile, ...]

    def get_conflicts(self) -> tuple[str, ...]:
        return tuple(
            entry.path for entry in self.files
            if entry.path != DATABASE_PATH and entry.before is not None and entry.should_write()
        )


class InstallSession:
    """Hold the selected root and build resources until approval and publication finish."""

    def __init__(self, sd_path: Path, slot: CoreSlot) -> None:
        self._tree = SDTree(sd_path)
        self._slot = slot
        self._plan: InstallPlan | None = None
        self._resources: ExitStack | None = None

    def __enter__(self) -> Self:

        if self._resources is not None:
            raise RuntimeError("InstallSession is already open")

        with ExitStack() as resources:
            tree = resources.enter_context(self._tree)
            source = tree.capture(DATABASE_PATH)

            if source is None:
                raise TrampolineError("Missing game/games.db; an existing stock catalogue is required")

            # Reject the catalogue before Cargo or any publication can start.
            tree.validate_database_idle(DATABASE_PATH)
            patch_database(source.content, self._slot)
            directory = resources.enter_context(TemporaryDirectory(prefix="itrampoline-"))
            core, helper = build_runtime(Path(directory))
            self._plan = prepare_install(tree, self._slot, core=core, helper=helper)
            self._resources = resources.pop_all()

        return self

    def __exit__(self, *exception: object) -> None:
        resources = self._resources
        self._resources = None
        self._plan = None

        if resources is not None:
            resources.__exit__(*exception)

    def _get_plan(self) -> InstallPlan:

        if self._resources is None or self._plan is None:
            raise RuntimeError("InstallSession is not open")

        return self._plan

    def get_conflicts(self) -> tuple[str, ...]:
        return self._get_plan().get_conflicts()

    def apply(self, *, backup_root: Path, allow_replace: bool) -> Path | None:
        return apply_install(self._tree, self._get_plan(), backup_root=backup_root, allow_replace=allow_replace)


def read_target(token: bytes) -> str:

    if not token.endswith(b"\n") or len(token) > 1024:
        raise TrampolineError("Invalid trampoline token: expected a relative executable path and one newline")

    try:
        path = token[:-1].decode("utf-8")

    except UnicodeDecodeError as error:
        raise TrampolineError("Invalid UTF-8 trampoline target") from error

    split_relative(path)

    if "\n" in path or "\r" in path:
        raise TrampolineError("Invalid newline in trampoline target")

    return path


def prepare_install(tree: SDTree, slot: CoreSlot, *, core: bytes, helper: bytes) -> InstallPlan:
    database = tree.capture(DATABASE_PATH)

    if database is None:
        raise TrampolineError("Missing game/games.db; an existing stock catalogue is required")

    tree.validate_database_idle(DATABASE_PATH)
    patched = patch_database(database.content, slot)
    token = tree.capture(TOKEN_PATH)
    token_content = TOKEN_CONTENT if token is None else token.content
    target = read_target(token_content)
    target_file = tree.capture(target)

    if target_file is None:
        logger.warning("Application is not installed yet: %s (it is not built by itrampoline)", target)

    elif not target_file.mode & 0o111:
        raise TrampolineError(f"Application has no executable bit: {target}")

    paths = (
        (f"retro_lib/{slot.filename}", core, 0o755),
        (HELPER_PATH, helper, 0o755),
        (TOKEN_PATH, token_content, 0o644),
    )
    planned = [PlannedFile(path, content, mode, tree.capture(path)) for path, content, mode in paths]
    planned.append(PlannedFile(DATABASE_PATH, patched, database.mode, database))
    return InstallPlan(slot, tuple(planned))


def create_backup(tree: SDTree, plan: InstallPlan, destination: Path) -> Path:
    destination = Path(os.path.abspath(destination))

    if destination.is_relative_to(tree.root):
        raise TrampolineError("Backups must be stored on the PC, outside the selected SD")

    parent = open_absolute_directory(destination.parent)
    session = None

    try:

        try:
            os.mkdir(destination.name, 0o700, dir_fd=parent)

        except FileExistsError:
            pass

        backup_root = os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)

        try:
            name = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            os.mkdir(name, 0o700, dir_fd=backup_root)
            session = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=backup_root)
            manifest = []

            for index, entry in enumerate(plan.files):

                if not entry.should_write():
                    continue

                record = {"path": entry.path, "backup": None}

                if entry.before is not None:
                    backup_name = f"{index}-{Path(entry.path).name}"
                    descriptor = os.open(backup_name, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=session)

                    with os.fdopen(descriptor, "w+b") as output:
                        output.write(entry.before.content)
                        output.flush()
                        output.seek(0)
                        digest = hashlib.file_digest(output, "sha256").hexdigest()

                        if digest != entry.before.digest:
                            raise TrampolineError(f"PC backup verification failed: {entry.path}")

                        os.fsync(output.fileno())

                    record.update(backup=backup_name, mode=entry.before.mode, sha256=entry.before.digest)

                manifest.append(record)

            descriptor = os.open("restore.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=session)

            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({"sd": f"{tree.root}", "files": manifest}, output, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())

            os.fsync(session)
            os.fsync(backup_root)
            os.fsync(parent)
            return destination / name

        finally:
            os.close(backup_root)

    finally:

        if session is not None:
            os.close(session)

        os.close(parent)


def apply_install(tree: SDTree, plan: InstallPlan, *, backup_root: Path, allow_replace: bool) -> Path | None:

    if plan.get_conflicts() and not allow_replace:
        raise TrampolineError("Existing files differ; replacement needs interactive approval")

    tree.validate_database_idle(DATABASE_PATH)

    for entry in plan.files:
        tree.validate_snapshot(entry.path, entry.before)

    changed = [entry for entry in plan.files if entry.should_write()]

    if not changed:
        logger.info("Trampoline files and catalogue are already current")
        return None

    space = os.fstatvfs(tree.descriptor)
    needed = sum(len(entry.content) for entry in changed)

    if space.f_bavail * space.f_frsize < needed + 1024 * 1024:
        raise TrampolineError("Not enough free space for staged files")

    backup = create_backup(tree, plan, backup_root)
    logger.info("Recovery files saved on the PC: %s", backup)

    try:

        for entry in changed:

            if entry.path == DATABASE_PATH:
                tree.validate_database_idle(DATABASE_PATH)

            tree.publish(entry.path, entry.content, entry.mode, entry.before)
            logger.info("Published %s", entry.path)

    except BaseException:
        logger.error("Installation may be partial. Recovery files remain at %s; no automatic rollback", backup)
        raise

    return backup
