import json
import os
import subprocess
import sys
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "bin" / "provenance"


def invoke(tmp_path, event):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PROVENANCE_DELIVERY_LOG"] = str(tmp_path / "missing-deliveries.jsonl")
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    payload = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
    return subprocess.run([sys.executable, str(CLI), "hook"], input=payload,
                          text=True, capture_output=True, env=env)


def verdict_reason(result):
    """Mismatch verdicts are structured JSON on stdout with exit 0."""
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] in ("PreToolUse", "PostToolUse")
    assert out["permissionDecision"] == "allow"
    assert "updatedInput" in out
    return out["additionalContext"]


def transcript(tmp_path, messages):
    path = tmp_path / ".claude/projects/fixture/12345678-0000-4000-8000-000000000000.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "user", "timestamp": stamp,
             "message": {"role": "user", "content": content}}
            for stamp, content in messages]
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return path


def bash_quote(tmp_path, path, citation):
    return invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                             "tool_input": {"command": f"message-relay send '{citation}'"},
                             "transcript_path": str(path)})


def rewritten_command(result):
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "allow"
    return output["updatedInput"]["command"]


def test_no_quote_fast_path_is_silent(tmp_path):
    result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                               "tool_input": {"command": "echo hello"},
                               "transcript_path": str(tmp_path / "missing.jsonl")})
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""


def test_pre_write_wrong_time_rewrite_any_extension(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T12:11:12Z", "Please review the draft. The rest of this message is much longer.")])
    result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Write",
                               "tool_input": {"file_path": "/tmp/notes.txt", "content": "⟦U 1118 20:20 review the draft.⟧"},
                               "transcript_path": str(path)})
    assert "𝕌1118 20:11 review the draft.⟧" in verdict_reason(result)


def test_new_quote_wrong_time_feedback(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T12:11:12Z", "Review the draft.")])
    result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Write",
                               "tool_input": {"content": "𝕌1118 20:20 Review the draft.⟧"},
                               "transcript_path": str(path)})
    assert "𝕌1118 20:11 Review the draft.⟧" in verdict_reason(result)


def test_post_write_iso_correction_keeps_fragment_and_offset(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T12:11:12Z", "Please review the draft before noon.")])
    result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Write",
                               "tool_input": {"content": '⟦U 2026-11-18T20:20:00+08:00 "review the draft"⟧'},
                               "transcript_path": str(path)})
    assert '𝕌2026-11-18T20:11:12+08:00 "review the draft"⟧' in verdict_reason(result)


def test_post_edit_correct_time_is_silent(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T12:11:12Z", "Review the draft.")])
    result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                               "tool_input": {"file_path": "/tmp/notes.md", "old_string": "old",
                                              "new_string": "⟦U 1118 20:11 Review the draft.⟧"},
                               "transcript_path": str(path)})
    assert result.returncode == 0
    assert result.stderr == ""


def test_pre_bash_wrong_time_is_rewritten_with_exact_quote(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T12:01:12Z", "Another message."),
                                 ("2026-11-18T12:11:12Z", "Review the draft.")])
    for citation in ("⟦U 1118 20:20 Review the draft.⟧", "𝕌1118 23:11 Review the draft.⟧"):
        assert "𝕌1118 20:11 Review the draft.⟧" in rewritten_command(bash_quote(tmp_path, path, citation))


def test_pre_bash_exact_time_passes_in_both_formats(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T12:11:12Z", "Review the draft.")])
    for citation in ("⟦U 1118 20:11 Review the draft.⟧", "𝕌1118 20:11 Review the draft.⟧"):
        result = bash_quote(tmp_path, path, citation)
        assert result.returncode == 0
        assert result.stdout == result.stderr == ""


def test_pre_bash_unresolved_denies_and_current_latest_wins(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T12:11:12Z", "Review the draft."),
                                 ("2026-11-18T14:11:12Z", "Review the draft.")])
    for citation in ("𝕌1118 20:1x Words absent from the transcript.⟧",
                     "⟦U 1118 20:48 Words absent from the transcript.⟧"):
        result = bash_quote(tmp_path, path, citation)
        assert result.returncode == 0
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    result = bash_quote(tmp_path, path, "𝕌1118 20:1x Review the draft.⟧")
    assert "𝕌1118 22:11 Review the draft.⟧" in rewritten_command(result)


def test_short_original_excludes_pasted_relay_and_rewrites_all_quotes(tmp_path):
    path = transcript(tmp_path, [
        ("2026-11-19T22:48:25Z", "掛"),
        ("2026-11-19T22:58:39Z", '<pasted_content id="relay">■ 06:58 provenance｜Codex hook 已掛</pasted_content id="relay">'),
        ("2026-11-19T22:59:00Z", "直接切吧。"),
    ])
    command = 'echo "測試A 𝕌1120 06:30 掛⟧"; echo "測試B 𝕌1120 06:30 直接切吧。⟧"'
    result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                               "tool_input": {"command": command}, "transcript_path": str(path)})
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert "𝕌1120 06:48 掛⟧" in output["updatedInput"]["command"]
    assert "𝕌1120 06:59 直接切吧。⟧" in output["updatedInput"]["command"]
    assert "2 處" in output["additionalContext"]


def test_fabricated_quote_denies_after_bounded_global_search(tmp_path):
    path = transcript(tmp_path, [
        ("2026-11-19T23:00:00Z", '𝕌1120 07:00 把所有備份都刪掉⟧'),
        ("2026-11-19T23:01:00Z", '<pasted_content id="x">把所有備份都刪掉</pasted_content id="x">'),
    ])
    result = bash_quote(tmp_path, path, "𝕌1120 07:00 把所有備份都刪掉⟧")
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "找不到引文原話" in output["permissionDecisionReason"]


def test_pre_bash_relay_not_in_human_source_is_silent(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T12:11:12Z", "【sender-test｜relay】Review the draft.")])
    result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                               "tool_input": {"command": "herdr agent prompt '⟦U 1118 20:20 Review the draft.⟧'"},
                               "transcript_path": str(path)})
    assert result.returncode == 0
    assert result.stderr == ""


def test_hook_ignores_pasted_citation_in_pilot_session(tmp_path):
    words = "不過應該也快備份完了吧 備份完就可以繼續刪了"
    citation = f"𝕌1119 00:49 {words}⟧"
    source = transcript(tmp_path, [
        ("2026-11-18T16:49:45Z", f"■ 00:49 sender-a｜sender-a\n【sender-a】user 00:49 原話：{citation} ⇒ 續刪。"),
        ("2026-11-18T16:57:56Z", f"【sender-a】user 00:49 原話：{citation} ⇒ 續刪。 <-- 贅字"),
    ])
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": citation}, "transcript_path": str(source)}
    result = invoke(tmp_path, event)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_bad_json_fails_open_silently(tmp_path):
    result = invoke(tmp_path, "{bad json ⟦U")
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""


def test_pre_bash_rewrites_minute_window(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T23:55:55Z", "smoke 派 haiku 去試就可以了對嗎?")])
    for citation in ("𝕌1119 07:5x smoke 派 haiku 去試就可以了對嗎?⟧",
                     "⟦U 1119 07:5X smoke 派 haiku 去試就可以了對嗎?⟧"):
        assert "𝕌1119 07:55 smoke 派 haiku 去試就可以了對嗎?⟧" in rewritten_command(bash_quote(tmp_path, path, citation))


def test_pre_write_minute_window_containing_source_is_rewritten(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T23:55:55Z", "smoke 派 haiku 去試就可以了對嗎?")])
    result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Write",
                               "tool_input": {"content": "𝕌1119 07:5x smoke 派 haiku 去試就可以了對嗎?⟧"},
                               "transcript_path": str(path)})
    assert result.returncode == 0
    assert "𝕌1119 07:55" in json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["content"]


def test_minute_window_outside_source_is_corrected(tmp_path):
    path = transcript(tmp_path, [("2026-11-18T23:55:55Z", "smoke 派 haiku 去試就可以了對嗎?")])
    result = invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Write",
                               "tool_input": {"content": "𝕌1119 07:4x smoke 派 haiku 去試就可以了對嗎?⟧"},
                               "transcript_path": str(path)})
    assert "𝕌1119 07:55 smoke 派 haiku 去試就可以了對嗎?⟧" in verdict_reason(result)
