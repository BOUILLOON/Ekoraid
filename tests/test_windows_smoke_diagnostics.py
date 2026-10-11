"""CI failure diagnostics survive cleanup without starting Windows services."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "packaging" / "windows_smoke.py"
SPEC = spec_from_file_location("windows_smoke_diagnostics", SCRIPT)
smoke = module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_readiness_failure_includes_bounded_fixture_log(tmp_path):
    log = tmp_path / "supervisor.log"
    log.write_bytes(b"old output" * 2000 + b"\nModuleNotFoundError: required_module\n")
    process = SimpleNamespace(poll=lambda: 1, returncode=1)
    with pytest.raises(RuntimeError, match="required_module") as caught:
        smoke.wait_ready(process, "http://127.0.0.1:1", "guest", None, log_path=log)
    assert "old output" in str(caught.value)
    assert len(str(caught.value).encode("utf-8")) < 8300


def test_timeout_also_preserves_fixture_log(tmp_path, monkeypatch):
    log = tmp_path / "guest.log"
    log.write_text("server startup stalled", encoding="utf-8")
    ticks = iter((0, 41))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(ticks))
    process = SimpleNamespace(poll=lambda: None)
    with pytest.raises(RuntimeError, match="server startup stalled"):
        smoke.wait_ready(process, "http://127.0.0.1:1", "guest", None, log_path=log)


def test_failed_report_retains_completed_checks_and_logs(tmp_path, monkeypatch):
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--executable", "fixture.exe", "--report", str(report_path)])
    def failing_smoke(executable, *, report):
        report["checks"].extend(("frozen doctor", "real frozen Tk GUI opened and closed"))
        report["runtime_versions"] = {"node.exe": "v24.19.0"}
        log = tmp_path / "supervisor.log"
        log.write_text("ModuleNotFoundError: missing_import", encoding="utf-8")
        smoke.preserve_fixture_logs(tmp_path, report)
        log.unlink()
        raise RuntimeError("fixture service failed")
    monkeypatch.setattr(smoke, "smoke", failing_smoke)
    with pytest.raises(RuntimeError, match="fixture service failed"):
        smoke.main()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["checks"][-1] == "real frozen Tk GUI opened and closed"
    assert report["runtime_versions"] == {"node.exe": "v24.19.0"}
    assert "missing_import" in report["local_fixture_logs"]["supervisor.log"]
    assert report["external_audio_downloads"] is False
    assert report["supabase_mutations"] is False
