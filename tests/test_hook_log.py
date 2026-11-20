import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "bin" / "provenance"


def run_hook(tmp_path, event, *, state=None):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["XDG_STATE_HOME"] = str(state or tmp_path / "state")
    env["PROVENANCE_DELIVERY_LOG"] = str(tmp_path / "missing-deliveries.jsonl")
    payload = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
    result = subprocess.run([sys.executable, str(CLI), "hook"], input=payload,
                            text=True, capture_output=True, env=env)
    return result, Path(env["XDG_STATE_HOME"]) / "provenance" / "hook.jsonl"


def human_log(tmp_path, words):
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "user", "timestamp": "2026-11-18T12:11:00Z",
                                "message": {"role": "user", "content": words}}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def test_hook_records_pass_without_content_and_private_mode(tmp_path):
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "abcdefgh-1234",
             "cwd": "/tmp/fixture", "tool_input": {"command": "echo hello"}}
    result, path = run_hook(tmp_path, event)
    assert result.returncode == 0
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["verdict"] == "pass"
    assert record["session8"] == "abcdefgh"
    assert record["cwd"] == "/tmp/fixture"
    assert record["tool"] == "Bash"
    assert record["quote_count"] == 0
    assert record["elapsed_ms"] >= 0
    assert "hello" not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_hook_records_skipped_delivery_warning_without_disabling_time_check(tmp_path):
    ledger = tmp_path / "deliveries.jsonl"
    ledger.write_text("{bad json\n", encoding="utf-8")
    source = human_log(tmp_path, "Review this draft.")
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": "𝕌1118 20:20 Review this draft.⟧"},
             "transcript_path": str(source)}
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env["PROVENANCE_DELIVERY_LOG"] = str(ledger)
    result = subprocess.run([sys.executable, str(CLI), "hook"], input=json.dumps(event),
                            text=True, capture_output=True, env=env)
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert "20:11" in output["updatedInput"]["command"]
    assert output["permissionDecision"] == "allow"
    assert result.stderr.count("InvalidDeliveryRecord") == 1
    record = json.loads((tmp_path / "state" / "provenance" / "hook.jsonl").read_text().splitlines()[0])
    assert record["verdict"] == "rewrite"
    assert record["detail"].startswith("InvalidDeliveryRecord count=1")


def test_hook_records_feedback_with_bounded_fragment(tmp_path):
    words = "Please review this very long draft before noon."
    source = human_log(tmp_path, words)
    citation = "𝕌1118 20:20 Please review this very long draft before noon.⟧"
    for event_name, tool_name, field, expected in (
        ("PreToolUse", "Bash", "command", "rewrite"),
        ("PreToolUse", "Write", "content", "rewrite"),
    ):
        event = {"hook_event_name": event_name, "tool_name": tool_name,
                 "tool_input": {field: citation}, "transcript_path": str(source)}
        result, path = run_hook(tmp_path, event)
        assert result.returncode == 0
        assert "𝕌1118 20:11" in result.stdout
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["verdict"] for record in records] == ["rewrite", "rewrite"]
    for record in records:
        assert record["quote_count"] == 1
        assert record["fragment_sha256"] == hashlib.sha256(words.encode()).hexdigest()
        assert record["fragment_preview"] == words[:20]
    assert words[20:] not in path.read_text()


def test_hook_write_failure_does_not_change_decision(tmp_path):
    source = human_log(tmp_path, "Review this draft.")
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x")
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": "𝕌1118 20:20 Review this draft.⟧"},
             "transcript_path": str(source)}
    result, _ = run_hook(tmp_path, event, state=blocked)
    assert result.returncode == 0
    assert "𝕌1118 20:11" in result.stdout


def test_hook_report_counts_latency_and_error_details(tmp_path):
    path = tmp_path / "state" / "provenance" / "hook.jsonl"
    path.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    records = [
        {"ts": now.isoformat(), "verdict": "pass", "elapsed_ms": 10, "quote_count": 0},
        {"ts": now.isoformat(), "verdict": "deny", "elapsed_ms": 20, "quote_count": 1},
        {"ts": now.isoformat(), "verdict": "timeout", "elapsed_ms": 30,
         "session8": "abc12345", "tool": "Write", "cwd": "/tmp/fixture", "quote_count": 1, "detail": "TimeoutError"},
        {"ts": (now - timedelta(hours=3)).isoformat(), "verdict": "error", "elapsed_ms": 200},
    ]
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    result = subprocess.run([sys.executable, str(CLI), "hook-report", "--hours", "1"],
                            text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "last 1h: 3" in result.stdout
    assert "pass=1 rewrite=0 deny=1 feedback=0 timeout=1 error=0" in result.stdout
    assert "p50=20 p95=30" in result.stdout
    assert "TimeoutError" in result.stdout
    assert "elapsed_ms=200" not in result.stdout


def test_malformed_hook_event_is_logged_as_error(tmp_path):
    result, path = run_hook(tmp_path, "{broken")
    assert result.returncode == 0
    record = json.loads(path.read_text().splitlines()[0])
    assert record["verdict"] == "error"
    assert record["detail"] == "JSONDecodeError"
