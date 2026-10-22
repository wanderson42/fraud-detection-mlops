"""Idempotent exact release reuse, atomic interrupted copy and divergent-byte refusal."""

from pathlib import Path
import shutil

import pytest
from scripts.operational_round import seed as s


def snapshot(directory):
    return {
        p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_ino) for p in directory.iterdir()
    }


def test_same_release_reused_without_rewriting_any_file(seed_release, tmp_path):
    source, digest = seed_release
    target = tmp_path / "model"
    assert s.seed(source, target, digest)["files_written"] == 3
    before = snapshot(target)
    result = s.seed(source, target, digest)
    assert result["status"] == "canonical_release_reused" and result["files_written"] == 0
    assert snapshot(target) == before


def test_exact_legacy_partial_release_can_complete(seed_release, tmp_path):
    source, digest = seed_release
    target = tmp_path / "model"
    target.mkdir()
    shutil.copyfile(source / "pipeline.skops", target / "pipeline.skops")
    (target / "pipeline.skops").chmod(0o444)
    inode = (target / "pipeline.skops").stat().st_ino
    result = s.seed(source, target, digest)
    assert result["files_written"] == 2 and result["files_reused"] == 1
    assert (target / "pipeline.skops").stat().st_ino == inode


@pytest.mark.parametrize("name", ["manifest.json", "pipeline.skops", "smoke.json"])
def test_divergent_existing_file_is_never_overwritten(seed_release, tmp_path, name):
    source, digest = seed_release
    target = tmp_path / "model"
    target.mkdir()
    (target / name).write_bytes(b"different-release")
    before = snapshot(target)
    with pytest.raises(ValueError, match="Existing release bytes differ"):
        s.seed(source, target, digest)
    assert snapshot(target) == before


def test_interrupted_temporary_is_recovered_without_partial_final_file(
    seed_release, tmp_path, monkeypatch
):
    source, digest = seed_release
    target = tmp_path / "model"

    def interrupted(inp, out):
        out.write(inp.read(2))
        raise OSError("interrupted write")

    with monkeypatch.context() as patch:
        patch.setattr(s.shutil, "copyfileobj", interrupted)
        with pytest.raises(OSError):
            s.seed(source, target, digest)
    assert not (target / "manifest.json").exists()
    assert any(p.name.startswith(".seed-") for p in target.iterdir())
    assert s.seed(source, target, digest)["status"] == "canonical_release_seeded"
    assert {p.name for p in target.iterdir()} == s.FILES


def test_interruption_after_atomic_link_preserves_complete_file(
    seed_release, tmp_path, monkeypatch
):
    source, digest = seed_release
    target = tmp_path / "model"
    unlink = Path.unlink

    def interrupt(path, *args, **kwargs):
        if path.name.startswith(".seed-"):
            raise OSError("interrupted after link")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupt)
        with pytest.raises(OSError):
            s.seed(source, target, digest)
    assert (target / "manifest.json").read_bytes() == (source / "manifest.json").read_bytes()
    inode = (target / "manifest.json").stat().st_ino
    s.seed(source, target, digest)
    assert (target / "manifest.json").stat().st_ino == inode


@pytest.mark.parametrize("kind", ["symlink", "unexpected", "pending_symlink"])
def test_redirects_and_unknown_files_are_preserved(seed_release, tmp_path, kind):
    source, digest = seed_release
    target = tmp_path / "model"
    target.mkdir()
    if kind == "symlink":
        (target / "pipeline.skops").symlink_to(source / "pipeline.skops")
    elif kind == "pending_symlink":
        (target / f".seed-{digest}-manifest.json.tmp").symlink_to(source / "manifest.json")
    else:
        (target / "do-not-delete").write_text("preserve")
    names = {p.name for p in target.iterdir()}
    with pytest.raises(ValueError):
        s.seed(source, target, digest)
    assert {p.name for p in target.iterdir()} == names


def test_wrong_manifest_pin_refused_before_creating_destination(seed_release, tmp_path):
    source, _ = seed_release
    target = tmp_path / "model"
    with pytest.raises(ValueError):
        s.seed(source, target, "f" * 64)
    assert not target.exists()
