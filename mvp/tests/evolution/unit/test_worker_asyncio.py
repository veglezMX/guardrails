"""Real closed worker subprocesses, including awaited bodies and safe failures."""

from __future__ import annotations

import json

import pytest
from evolution import diagnostics, run
from test_runner import case, make_test_root

CANARY = "async-worker-private-value-never-retained"


def test_closed_worker_uses_private_case_temp_and_retains_failure_outside_reports(tmp_path, monkeypatch):
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    for name in ("TMPDIR", "TMP", "TEMP", "PYTEST_DEBUG_TEMPROOT"):
        monkeypatch.setenv(name, str(ambient))
    _, destination, _, result = execute(tmp_path, f'''import os
import tempfile
from pathlib import Path

def test_behavior(tmp_path, tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    system = Path(tempfile.gettempdir())
    assert base.parent == system.parent
    assert base.parent.stat().st_mode & 0o777 == 0o700
    assert base.stat().st_mode & 0o777 == 0o700
    assert system.stat().st_mode & 0o777 == 0o700
    assert all(Path(os.environ[key]) == system for key in ("TMPDIR", "TMP", "TEMP"))
    assert "PYTEST_DEBUG_TEMPROOT" not in os.environ
    (tmp_path / "fixture.txt").write_text({CANARY!r})
    raise RuntimeError({CANARY!r})
''')
    assert result["status"] == "fail"
    assert result["assertions"] == 6
    assert "case_call_failed" in result["reason_codes"]
    temporaries = list(destination.parent.glob(".worker-*"))
    assert len(temporaries) == 1
    retained = list(temporaries[0].rglob("fixture.txt"))
    assert len(retained) == 1
    assert retained[0].read_text() == CANARY
    assert not list(ambient.iterdir())
    assert CANARY not in "".join(path.read_text() for path in destination.rglob("*.json"))


def test_closed_worker_does_not_reuse_or_delete_another_attempt_temp(tmp_path):
    body = '''def test_behavior(tmp_path):
    assert not (tmp_path / "sentinel").exists()
    (tmp_path / "sentinel").write_text("preserved")
'''
    for attempt in ("first", "second"):
        _, _, _, result = execute(tmp_path / attempt, body)
        assert result["status"] == "pass"
    sentinels = [path for path in tmp_path.glob("*/.worker-*/pytest/*/sentinel")
                 if not path.parent.is_symlink()]
    assert len(sentinels) == 2
    assert all(path.read_text() == "preserved" for path in sentinels)
    assert len({path.parents[2] for path in sentinels}) == 2


def test_closed_worker_refuses_symlinked_temp_parent_without_touching_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    root = make_test_root(tmp_path / "root", "def test_behavior():\n    assert True\n")
    destination = alias / "evidence"
    destination.mkdir(mode=0o700)
    result = run.execute_case(case(timeout=20), root, destination)
    assert result["status"] == "fail"
    assert "worker_start_failed" in result["reason_codes"]
    assert not list(target.glob(".worker-*"))


def execute(tmp_path, body):
    root = make_test_root(tmp_path / "root", body)
    destination = tmp_path / "evidence"
    destination.mkdir(mode=0o700)
    selected = case(timeout=20)
    result = run.execute_case(selected, root, destination)
    worker = json.loads((destination / (selected.case_id + ".json")).read_text())
    assert set(worker) == {"nodeid", "status", "duration_ms", "assertions", "reason_codes"}
    return root, destination, selected, result


def test_closed_worker_awaits_async_test_and_fixture_but_counts_only_call_assertions(tmp_path):
    _, destination, selected, result = execute(tmp_path, '''import asyncio
import pytest
import pytest_asyncio

@pytest_asyncio.fixture
async def prepared():
    await asyncio.sleep(0)
    assert True
    yield "ready"
    await asyncio.sleep(0)
    assert True

@pytest.mark.asyncio
async def test_behavior(prepared):
    transitions = []
    async def complete():
        await asyncio.sleep(0)
        transitions.append("completed")
        return prepared
    assert await complete() == "ready"
    assert transitions == ["completed"]
''')
    assert result["status"] == "pass"
    assert result["assertions"] == 2
    assert result["reason_codes"] == []
    assert not diagnostics.sidecar(destination / (selected.case_id + ".json")).exists()


def test_closed_worker_async_failure_retains_static_origin_without_values(tmp_path):
    root, destination, selected, result = execute(tmp_path, f'''import asyncio
import pytest

async def origin():
    await asyncio.sleep(0)
    private_value = "{CANARY}"
    print(private_value)
    raise ValueError(private_value)

@pytest.mark.asyncio
async def test_behavior():
    assert True
    try:
        await origin()
    except ValueError:
        raise RuntimeError("{CANARY}") from None
''')
    assert result["status"] == "fail"
    assert result["assertions"] == 1
    assert "case_call_failed" in result["reason_codes"]
    assert "diagnostic_evidence_missing_or_invalid" not in result["reason_codes"]
    path = diagnostics.sidecar(destination / (selected.case_id + ".json"))
    value, record = diagnostics.read_diagnostic(path, root)
    rows = value["failures"][0]["exceptions"]
    assert [row["type"] for row in rows] == ["RuntimeError", "ValueError"]
    assert rows[1]["relation"] == "context"
    assert any(frame["function"] == "origin" for frame in rows[1]["frames"])
    assert record["source_files"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert CANARY not in "".join(p.read_text() for p in destination.glob("*.json"))


def test_closed_worker_maps_actual_pytest_fail_class_without_retaining_message(tmp_path):
    root, destination, selected, result = execute(tmp_path, f'''import asyncio
import pytest

@pytest.mark.asyncio
async def test_behavior():
    await asyncio.sleep(0)
    assert True
    pytest.fail("{CANARY}", pytrace=False)
''')
    assert result["status"] == "fail"
    assert result["assertions"] == 1
    path = diagnostics.sidecar(destination / (selected.case_id + ".json"))
    value, _ = diagnostics.read_diagnostic(path, root)
    assert value["failures"][0]["exceptions"][0]["type"] == "PytestFailed"
    assert "case_call_failed" in result["reason_codes"]
    assert "diagnostic_evidence_missing_or_invalid" not in result["reason_codes"]
    assert CANARY not in "".join(p.read_text() for p in destination.glob("*.json"))


def test_closed_worker_retains_sync_and_raises_assertion_accounting(tmp_path):
    _, _, _, result = execute(tmp_path, '''import pytest

def test_behavior():
    assert 2 + 3 == 5
    with pytest.raises(ValueError):
        raise ValueError("synthetic")
    assert "safe".upper() == "SAFE"
''')
    assert result["status"] == "pass"
    assert result["assertions"] == 3
    assert result["reason_codes"] == []


def test_closed_worker_loads_only_approved_async_plugin_despite_ambient_entrypoint(tmp_path, monkeypatch):
    marker = tmp_path / "unapproved-plugin-loaded"
    root = make_test_root(tmp_path / "root", f'''import importlib.metadata
import os
from pathlib import Path
import sys
import pytest
import pytest_asyncio.plugin

@pytest.mark.asyncio
async def test_behavior(pytestconfig):
    assert os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert pytestconfig.pluginmanager.is_registered(pytest_asyncio.plugin)
    assert any(ep.name == "unapproved_worker_probe" for ep in importlib.metadata.entry_points(group="pytest11"))
    assert "unapproved_worker_plugin" not in sys.modules
    assert not Path({str(marker)!r}).exists()
''')
    # A discoverable third-party entry point, plus both ambient injection paths.
    distribution = root / "mvp/unapproved_worker_probe-1.0.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: unapproved-worker-probe\nVersion: 1.0\n"
    )
    (distribution / "entry_points.txt").write_text(
        "[pytest11]\nunapproved_worker_probe = unapproved_worker_plugin\n"
    )
    (root / "mvp/unapproved_worker_plugin.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
    )
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "0")
    monkeypatch.setenv("PYTEST_PLUGINS", "unapproved_worker_plugin")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p unapproved_worker_plugin")
    destination = tmp_path / "evidence"
    destination.mkdir(mode=0o700)
    result = run.execute_case(case(timeout=20), root, destination)
    assert result["status"] == "pass"
    assert result["assertions"] == 5
    assert result["reason_codes"] == []
    assert not marker.exists()


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_closed_worker_async_fixture_failure_is_finite_and_not_a_pass(tmp_path, phase):
    root, destination, selected, result = execute(tmp_path, f'''import asyncio
import pytest
import pytest_asyncio

@pytest_asyncio.fixture
async def prepared():
    await asyncio.sleep(0)
    assert True
    {"raise ValueError('" + CANARY + "')" if phase == "setup" else "yield"}
    {"yield" if phase == "setup" else "raise ValueError('" + CANARY + "')"}

@pytest.mark.asyncio
async def test_behavior(prepared):
    await asyncio.sleep(0)
    assert True
''')
    assert result["status"] == "fail"
    assert result["assertions"] == (0 if phase == "setup" else 1)
    assert "case_" + phase + "_failed" in result["reason_codes"]
    assert "diagnostic_evidence_missing_or_invalid" not in result["reason_codes"]
    path = diagnostics.sidecar(destination / (selected.case_id + ".json"))
    value, _ = diagnostics.read_diagnostic(path, root)
    assert [failure["phase"] for failure in value["failures"]] == [phase]
    assert value["failures"][0]["exceptions"][0]["type"] == "ValueError"
    assert CANARY not in "".join(p.read_text() for p in destination.glob("*.json"))
