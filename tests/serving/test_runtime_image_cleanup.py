"""Execute the Dockerfile cleanup offline; keep dependency metadata and imports."""

import json
from pathlib import Path
import shlex
import subprocess
import sys


def test_builder_cleanup_removes_installer_and_bytecode_preserving_runtime(tmp_path):
    dockerfile = Path(__file__).resolve().parents[2] / "docker/serving/Dockerfile"
    source = dockerfile.read_text()
    start = source.index("RUN /opt/runtime/bin/python -m pip check")
    end = source.index("# END_RUNTIME_BUILD_CLEANUP", start)
    assert end < source.index("FROM ${PYTHON_IMAGE} AS runtime")
    cleanup = source[start + 4 : end].strip()
    environment = tmp_path / "runtime with spaces"
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True, timeout=60)
    python = environment / "bin/python"
    site = Path(
        subprocess.check_output(
            [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True,
        ).strip()
    )
    # A runtime-owned module with metadata and bytecode. The cleanup may only
    # remove pip and caches; it must not delete application code or metadata.
    (site / "runtime_probe.py").write_text("VALUE = 42\n")
    metadata = site / "runtime_probe-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: runtime-probe\nVersion: 1.0\n")
    subprocess.run([str(python), "-I", "-c", "import runtime_probe"], check=True)
    assert list(site.rglob("*.pyc"))
    command = cleanup.replace("/opt/runtime", shlex.quote(str(environment)))
    subprocess.run(["sh", "-eu", "-c", command], check=True, timeout=60)
    assert not list(site.rglob("*.pyc"))
    assert not list(site.glob("pip*"))
    result = subprocess.check_output(
        [
            str(python),
            "-I",
            "-B",
            "-c",
            (
                "import importlib.util,json,runtime_probe; from importlib.metadata import version; "
                "print(json.dumps([runtime_probe.VALUE,version('runtime-probe'),"
                "importlib.util.find_spec('pip') is None]))"
            ),
        ],
        text=True,
    )
    assert json.loads(result) == [42, "1.0", True]
    # Retry is not an operational command: only the builder runs this block.
    assert "PYTHONDONTWRITEBYTECODE=1" in source[source.index("AS runtime") :]
