import fcntl
import hashlib
import os
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Self

from .errors import TrampolineError


DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
MAX_FILE_BYTES = 256 * 1024 * 1024


def split_relative(path: str) -> tuple[str, ...]:
    """Split a relative path and reject empty, parent or NUL-containing components."""
    parts = tuple(path.split("/"))

    if not parts or any(not part or part in (".", "..") or "\x00" in part for part in parts):
        raise TrampolineError("Expected a safe relative path")

    return parts


def open_absolute_directory(path: Path) -> int:
    """Open and return a directory FD without following links; the caller closes it."""
    descriptor = os.open("/", DIRECTORY_FLAGS)

    try:

        for part in path.absolute().parts[1:]:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child

        return descriptor

    except BaseException:
        os.close(descriptor)
        raise


def identify(metadata: os.stat_result) -> tuple[int, int]:
    """Return the device and inode used to detect path replacement."""
    return metadata.st_dev, metadata.st_ino


@dataclass(frozen=True)
class FileSnapshot:
    identity: tuple[int, int]
    mode: int
    content: bytes
    digest: str


class SDTree:
    """Own a selected directory and lock it, never mount or open raw devices."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(root))
        self.descriptor: int | None = None
        self.identity: tuple[int, int] | None = None

    def __enter__(self) -> Self:

        if self.root == Path("/"):
            raise TrampolineError("The filesystem root is not an SD directory")

        descriptor = open_absolute_directory(self.root)

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.identity = identify(os.fstat(descriptor))
            self.descriptor = descriptor
            return self

        except BaseException:
            os.close(descriptor)
            raise

    def __exit__(self, *exception: object) -> None:

        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def validate_root(self) -> None:
        """Check that the SD path still names the directory held by this session."""

        if self.descriptor is None:
            raise RuntimeError("SDTree is not open")

        descriptor = open_absolute_directory(self.root)

        try:

            if identify(os.fstat(descriptor)) != self.identity:
                raise TrampolineError("SD root was replaced")

        finally:
            os.close(descriptor)

    @contextmanager
    def open_parent(self, path: str, *, create: bool = False) -> Iterator[tuple[int, str]]:
        """Hold a checked parent FD until context exit, optionally creating missing directories."""
        self.validate_root()
        parts = split_relative(path)
        descriptors = [os.dup(self.descriptor)]

        try:

            for part in parts[:-1]:

                if create:

                    try:
                        os.mkdir(part, 0o755, dir_fd=descriptors[-1])

                    except FileExistsError:
                        pass

                child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptors[-1])
                descriptors.append(child)

                if os.fstat(child).st_dev != self.identity[0]:
                    raise TrampolineError("A nested filesystem is not part of the selected SD")

            yield descriptors[-1], parts[-1]
            self.validate_root()

            for parent, child, name in zip(descriptors, descriptors[1:], parts[:-1]):

                if identify(os.stat(name, dir_fd=parent, follow_symlinks=False)) != identify(os.fstat(child)):
                    raise TrampolineError("An SD directory was replaced")

        finally:

            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def capture(self, path: str) -> FileSnapshot | None:
        """Read a bounded regular file snapshot, or return None when it is absent."""

        try:

            with self.open_parent(path) as (parent, name):
                descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)

                try:
                    metadata = os.fstat(descriptor)

                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                        raise TrampolineError(f"Expected a regular single-link file: {path}")

                    if metadata.st_dev != self.identity[0] or metadata.st_size > MAX_FILE_BYTES:
                        raise TrampolineError(f"Unexpected device or oversized file: {path}")

                    chunks = []
                    total = 0

                    while chunk := os.read(descriptor, 1024 * 1024):
                        chunks.append(chunk)
                        total += len(chunk)

                        if total > MAX_FILE_BYTES:
                            raise TrampolineError(f"File grew during reading: {path}")

                    after = os.fstat(descriptor)

                    if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns,
                    ):
                        raise TrampolineError(f"File changed during reading: {path}")

                    content = b"".join(chunks)
                    return FileSnapshot(identify(metadata), stat.S_IMODE(metadata.st_mode), content,
                                        hashlib.sha256(content).hexdigest())

                finally:
                    os.close(descriptor)

        except FileNotFoundError:
            return None

    def validate_snapshot(self, path: str, expected: FileSnapshot | None) -> None:
        """Reject a file whose contents, metadata or presence changed since preflight."""
        current = self.capture(path)

        if current != expected:
            raise TrampolineError(f"Destination changed since preflight: {path}")

    def validate_parent(self, path: str, descriptor: int) -> None:
        """Check that a held parent FD still belongs to the requested destination."""

        with self.open_parent(path) as (current, _):

            if identify(os.fstat(current)) != identify(os.fstat(descriptor)):
                raise TrampolineError(f"Parent directory changed before publication: {path}")

    def validate_database_idle(self, path: str) -> None:
        """Reject SQLite sidecar files that indicate an active or unfinished database update."""

        for suffix in ("-wal", "-shm", "-journal"):

            with self.open_parent(path + suffix) as (parent, name):

                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)

                except FileNotFoundError:
                    continue

                raise TrampolineError("Stop the stock GUI and close SQLite before installation (sidecar present)")

    def publish(self, path: str, content: bytes, mode: int, before: FileSnapshot | None) -> None:
        """One atomic rename, not an atomic update of the complete installation."""
        self.validate_snapshot(path, before)

        with self.open_parent(path, create=True) as (parent, name):
            self.validate_parent(path, parent)
            temporary = f".itrampoline-{uuid.uuid4().hex}"
            descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)

            try:
                remaining = memoryview(content)

                while remaining:
                    written = os.write(descriptor, remaining)

                    if written == 0:
                        raise TrampolineError("Short write during publication")

                    remaining = remaining[written:]

                os.fchmod(descriptor, mode)
                os.lseek(descriptor, 0, os.SEEK_SET)
                digest = hashlib.sha256()

                while chunk := os.read(descriptor, 1024 * 1024):
                    digest.update(chunk)

                if digest.digest() != hashlib.sha256(content).digest():
                    raise TrampolineError(f"Staged file verification failed: {path}")

                os.fsync(descriptor)
                self.validate_snapshot(path, before)
                self.validate_parent(path, parent)
                os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)

            finally:
                os.close(descriptor)

                try:
                    os.unlink(temporary, dir_fd=parent)

                except FileNotFoundError:
                    pass
