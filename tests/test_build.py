import struct
import subprocess
from pathlib import Path

import pytest

from itrampoline.build import TARGET, build_runtime, validate_elf
from itrampoline.errors import TrampolineError


def create_elf(*, shared: bool, segment_type: int = 1) -> bytes:
    content = bytearray(84)
    content[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HH", content, 16, 3 if shared else 2, 40)
    struct.pack_into("<I", content, 28, 52)
    struct.pack_into("<I", content, 36, 0x05000400)
    struct.pack_into("<HH", content, 42, 32, 1)
    struct.pack_into("<8I", content, 52, segment_type, 0, 0, 0, 84, 84, 5, 4096)
    return bytes(content)


@pytest.mark.parametrize("shared", [False, True])
def test_arm_elf_and_wrong_interpreter_abi_are_checked(shared: bool) -> None:
    content = create_elf(shared=shared)

    validate_elf(content, shared=shared)

    with pytest.raises(TrampolineError, match="interpreter"):
        validate_elf(create_elf(shared=shared, segment_type=3), shared=shared)

    with pytest.raises(TrampolineError, match="ELF32"):
        validate_elf(b"not ELF", shared=shared)

    with pytest.raises(TrampolineError, match="hard-float"):
        validate_elf(content[:36] + b"\0\0\0\0" + content[40:], shared=shared)


def test_build_uses_package_resources_and_exactly_two_binaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []
    monkeypatch.setattr("itrampoline.build.shutil.which", lambda name: "/synthetic/cargo")

    def compile_fixture(
        command: list[str], *, cwd: Path, env: dict[str, str], **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert (cwd / "helper/src/main.rs").is_file()
        assert (cwd / "Cargo.lock").is_file()
        assert "--locked" in command
        assert "CARGO_ENCODED_RUSTFLAGS" not in env
        release = Path(env["CARGO_TARGET_DIR"]) / TARGET / "release"
        release.mkdir(parents=True, exist_ok=True)
        (release / "itrampoline").write_bytes(create_elf(shared=False))
        (release / "libitrampoline_core.so").write_bytes(create_elf(shared=True))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setenv("CARGO_ENCODED_RUSTFLAGS", "unwanted override")
    monkeypatch.setattr("itrampoline.build.subprocess.run", compile_fixture)

    core, helper = build_runtime(tmp_path)

    assert core == create_elf(shared=True)
    assert helper == create_elf(shared=False)
    assert [command[-1] for command in commands] == ["itrampoline-helper", "itrampoline-core"]


def test_build_failure_never_produces_installable_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("itrampoline.build.shutil.which", lambda name: "/synthetic/cargo")
    monkeypatch.setattr(
        "itrampoline.build.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 1, "", "missing target"),
    )

    with pytest.raises(TrampolineError, match="missing target"):
        build_runtime(tmp_path)
