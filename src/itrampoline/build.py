import logging
import os
import shutil
import struct
import subprocess
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from .errors import TrampolineError


logger = logging.getLogger(__name__)
TARGET = "armv7-unknown-linux-musleabihf"


def copy_resources(source: Traversable, destination: Path) -> None:
    """Copy runtime source resources, leaving out build and test artifacts."""
    destination.mkdir()

    for child in source.iterdir():

        if child.name in ("target", "tests", "examples", "__pycache__"):
            continue

        if child.is_dir():
            copy_resources(child, destination / child.name)

        else:
            (destination / child.name).write_bytes(child.read_bytes())


def validate_elf(content: bytes, *, shared: bool) -> None:
    """Check ARM EABI5 hard-float and a dependency-free ELF32 program header."""

    if len(content) < 52 or content[:7] != b"\x7fELF\x01\x01\x01":
        raise TrampolineError("Expected an ELF32 little-endian build")

    kind, machine = struct.unpack_from("<HH", content, 16)
    flags = struct.unpack_from("<I", content, 36)[0]

    if machine != 40 or kind != (3 if shared else 2) or flags & 0xFF000600 != 0x05000400:
        raise TrampolineError("Expected ARM EABI5 hard-float with the selected ELF type")

    offset = struct.unpack_from("<I", content, 28)[0]
    entry_size, count = struct.unpack_from("<HH", content, 42)

    if entry_size != 32 or count == 0 or offset + count * entry_size > len(content):
        raise TrampolineError("Invalid ELF program headers")

    for index in range(count):
        segment = struct.unpack_from("<8I", content, offset + index * entry_size)
        segment_type, file_offset, _, _, size, _, _, _ = segment

        if file_offset + size > len(content) or segment_type == 3:
            raise TrampolineError("Truncated ELF or unexpected dynamic interpreter")

        if segment_type == 2:

            if size % 8:
                raise TrampolineError("Invalid ELF dynamic table")

            for tag_offset in range(file_offset, file_offset + size, 8):
                tag, value = struct.unpack_from("<II", content, tag_offset)

                if tag in (1, 22) or (tag == 30 and value & 4):
                    raise TrampolineError("Unexpected ELF dependency or text relocation")


def build_runtime(directory: Path) -> tuple[bytes, bytes]:
    """Compile only our two binaries in caller-owned temporary storage."""
    cargo = shutil.which("cargo")

    if cargo is None:
        raise TrampolineError("cargo is required; install Rust and the ARMv7 musl target")

    source = directory / "runtime"
    copy_resources(files("itrampoline").joinpath("resources", "runtime"), source)
    environment = dict(os.environ)
    environment.pop("CARGO_ENCODED_RUSTFLAGS", None)
    compiled = directory / "compiled"
    environment["CARGO_TARGET_DIR"] = f"{compiled}"

    for package, flags in (
        ("itrampoline-helper", "-C linker=rust-lld -C link-self-contained=yes -C target-feature=+crt-static"),
        ("itrampoline-core", "-C linker=rust-lld -C link-self-contained=no -C target-feature=-crt-static"),
    ):
        logger.info("Building %s for %s", package, TARGET)
        environment["RUSTFLAGS"] = flags
        result = subprocess.run(
            [cargo, "build", "--locked", "--release", "--target", TARGET, "-p", package],
            cwd=source, env=environment, capture_output=True, text=True,
        )

        if result.returncode:
            raise TrampolineError(f"Rust build failed:\n{result.stderr[-8192:]}")

    release = compiled / TARGET / "release"
    helper = (release / "itrampoline").read_bytes()
    core = (release / "libitrampoline_core.so").read_bytes()
    validate_elf(helper, shared=False)
    validate_elf(core, shared=True)
    return core, helper
