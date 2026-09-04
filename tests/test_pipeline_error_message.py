"""stage_1_process_viewport's error message on a failed run.

Confirmed live: a viewport processing failure surfaced as a wall of raw
subprocess internals -- "Stage 1 failed - Process viewport (exit code 1):
stderr: (no stderr output) last stdout: [2025] Fetching mosaic... [2025]
Attempt 1/3 failed... HTTPError: HTTP Error 500..." -- when
process_viewport.py had already printed a clean "[2025] FAILED: <reason>"
line per year. stage_1_process_viewport now prefers that clean line.
"""

from __future__ import annotations

import subprocess

from lib.pipeline import PipelineRunner


def _runner(tmp_path):
    return PipelineRunner(project_root=tmp_path, venv_python=tmp_path / "python")


def _fake_result(returncode, stdout, stderr=""):
    return subprocess.CompletedProcess(["process_viewport.py"], returncode, stdout, stderr)


def test_clean_failed_line_replaces_the_raw_dump(tmp_path, monkeypatch):
    stdout = (
        "Processing viewport: gaza\n"
        "  [2025] Fetching mosaic...\n"
        "  [2025] Attempt 1/3 failed, retrying in 5s: HTTPError: HTTP Error 500: INTERNAL SERVER ERROR\n"
        "  [2025] Attempt 2/3 failed, retrying in 5s: HTTPError: HTTP Error 500: INTERNAL SERVER ERROR\n"
        "  [2025] Failed after 3 attempts: HTTPError: HTTP Error 500: INTERNAL SERVER ERROR\n"
        "\n"
        "============================================================\n"
        "  [2025] FAILED: No embeddings could be found for 2025 at this location.\n"
    )
    r = _runner(tmp_path)
    monkeypatch.setattr(r, "run_script", lambda *a, **k: _fake_result(1, stdout, ""))

    success, error = r.stage_1_process_viewport("gaza", "2025")

    assert success is False
    assert error == "No embeddings could be found for 2025 at this location."
    # None of the retry-attempt / HTTPError internals leak into the message
    assert "HTTPError" not in error
    assert "Attempt" not in error
    assert "stderr:" not in error


def test_multiple_failed_years_are_joined_and_deduped(tmp_path, monkeypatch):
    stdout = (
        "  [2023] FAILED: No embeddings could be found for 2023 at this location.\n"
        "  [2025] FAILED: No embeddings could be found for 2025 at this location.\n"
    )
    r = _runner(tmp_path)
    monkeypatch.setattr(r, "run_script", lambda *a, **k: _fake_result(1, stdout, ""))

    success, error = r.stage_1_process_viewport("gaza", "2023,2025")

    assert success is False
    assert "2023" in error and "2025" in error
    assert error.count("No embeddings could be found") == 2


def test_falls_back_to_the_raw_dump_when_no_clean_line_exists(tmp_path, monkeypatch):
    # A genuine crash: no "[YYYY] FAILED:" line, just a traceback on stderr.
    r = _runner(tmp_path)
    monkeypatch.setattr(
        r, "run_script",
        lambda *a, **k: _fake_result(1, "Processing viewport: gaza\n", "Traceback (most recent call last):\nZeroDivisionError\n"),
    )

    success, error = r.stage_1_process_viewport("gaza", "2025")

    assert success is False
    assert "ZeroDivisionError" in error
    assert "Stage 1 failed" in error


def test_oom_kill_falls_back_to_the_raw_dump_even_with_a_failed_line(tmp_path, monkeypatch):
    # A SIGKILL (e.g. OOM) is itself the important signal -- don't let a
    # stray earlier "[YYYY] FAILED:" line from a prior year mask it.
    stdout = "  [2023] FAILED: No embeddings could be found for 2023 at this location.\n"
    r = _runner(tmp_path)
    monkeypatch.setattr(r, "run_script", lambda *a, **k: _fake_result(-9, stdout, ""))

    success, error = r.stage_1_process_viewport("gaza", "2023,2025")

    assert success is False
    assert "signal 9" in error
    assert "out of memory" in error
