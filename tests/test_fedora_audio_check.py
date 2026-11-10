"""Audio diagnostics must work safely without audio hardware."""
import importlib.util
from pathlib import Path
import subprocess
import sys

spec = importlib.util.spec_from_file_location(
    "fedora_audio_check", Path(__file__).parents[1] / "scripts/fedora_audio_check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def test_missing_tools_are_incomplete_without_subprocesses(monkeypatch):
    monkeypatch.setattr(check.shutil, "which", lambda name: None)
    def unexpected(*args, **kwargs):
        raise AssertionError("unexpected process")
    monkeypatch.setattr(check.subprocess, "run", unexpected)
    result = check.collect(.1)
    assert result["status"] == "incomplete"
    assert all(item["status"] == "missing" for item in result["checks"].values())


def test_timeout_and_nonzero_probe_are_not_success(monkeypatch):
    monkeypatch.setattr(check.shutil, "which", lambda name: "/tool")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(check.subprocess, "run", timeout)
    assert check.probe(["sclang", "-v"], .1)["status"] == "timeout"
    monkeypatch.setattr(check.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "no server"))
    result = check.probe(["pactl", "info"], .1)
    assert result["status"] == "failed"
    assert result["stderr"] == "no server"


def test_existing_report_is_preserved(tmp_path):
    report = tmp_path / "report.json"
    report.write_text("existing evidence")
    result = subprocess.run([sys.executable, str(Path(check.__file__)), "--output", str(report)], capture_output=True, text=True)
    assert result.returncode == 2
    assert report.read_text() == "existing evidence"
    assert "already exists" in result.stderr
