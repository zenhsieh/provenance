"""Codex hook wire format and quote-time decisions."""

import json
import os
import subprocess
import sys
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "bin" / "provenance"
WORDS = "Review the draft."
CORRECT = "𝕌1118 20:11 Review the draft.⟧"
WRONG = "𝕌1118 20:20 Review the draft.⟧"


def invoke(tmp_path, event, *options):
    source = tmp_path / "session.jsonl"
    source.write_text(json.dumps({"type": "event_msg", "timestamp": "2026-11-18T12:11:12Z",
                                  "payload": {"type": "user_message", "message": WORDS}}) + "\n",
                      encoding="utf-8")
    event = {"session_id": "abcdefgh-1234", "transcript_path": str(source),
             "cwd": str(tmp_path), **event}
    env = os.environ.copy()
    env["PROVENANCE_DELIVERY_LOG"] = str(tmp_path / "missing-deliveries.jsonl")
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    result = subprocess.run([sys.executable, str(CLI), "codex-hook", *options],
                            input=json.dumps(event, ensure_ascii=False), text=True,
                            capture_output=True, env=env)
    records = [json.loads(line) for line in (tmp_path / "state" / "provenance" / "hook.jsonl").read_text().splitlines()]
    return result, records[-1], env


def test_codex_pre_bash_pass(tmp_path):
    result, record, _ = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                         "tool_input": {"command": CORRECT}})
    assert result.returncode == 0 and result.stdout == ""
    assert record["agent"] == "codex" and record["verdict"] == "pass"


def test_codex_pre_bash_enforce_rewrites_wrong_time(tmp_path):
    result, record, _ = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                         "tool_input": {"command": WRONG}}, "--mode", "enforce")
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "allow"
    assert CORRECT == output["updatedInput"]["command"]
    assert record["agent"] == "codex" and record["verdict"] == "rewrite"


def test_codex_pre_patch_rewrites_only_added_lines(tmp_path):
    patch = f"*** Begin Patch\n*** Update File: note.md\n@@\n {WRONG}\n+{WRONG}\n*** End Patch"
    result, record, _ = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                                         "tool_input": {"command": patch}}, "--mode", "enforce")
    updated = json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    assert f" {WRONG}\n+{CORRECT}\n" in updated
    assert record["verdict"] == "rewrite"


def test_codex_untimed_quote_gets_source_time(tmp_path):
    result, record, _ = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                         "tool_input": {"command": "𝕌Review the draft.⟧"}}, "--mode", "enforce")
    assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"] == CORRECT
    assert record["verdict"] == "rewrite"


def test_codex_shadow_and_patch_feedback_only_new_lines(tmp_path):
    result, record, _ = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                         "tool_input": {"command": WRONG}})
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert CORRECT in output["additionalContext"]
    assert "permissionDecision" not in output and record["verdict"] == "feedback"

    patch = f"*** Begin Patch\n*** Update File: note.md\n@@\n-{WRONG}\n+{WRONG}\n*** End Patch"
    result, record, _ = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                                         "tool_input": {"command": patch}})
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert CORRECT in output["additionalContext"] and record["verdict"] == "feedback"

    removed_only = f"*** Begin Patch\n*** Update File: note.md\n@@\n-{WRONG}\n+No quote here.\n*** End Patch"
    result, record, _ = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                                         "tool_input": {"command": removed_only}})
    assert result.stdout == "" and record["quote_count"] == 0


def test_codex_timeout_is_logged_and_fails_open(tmp_path):
    result, record, _ = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                         "tool_input": {"command": WRONG}}, "--budget-ms", "0")
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert result.returncode == 0
    assert output["permissionDecision"] == "allow"
    assert "未驗證（查詢逾時）" in output["additionalContext"]
    assert record["agent"] == "codex" and record["verdict"] == "timeout"


def test_hook_report_filters_codex(tmp_path):
    invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                      "tool_input": {"command": CORRECT}})
    env = os.environ.copy()
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    result = subprocess.run([sys.executable, str(CLI), "hook-report", "--hours", "1", "--agent", "codex"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0
    assert "last 1h: 1" in result.stdout and "codex=1" in result.stdout
