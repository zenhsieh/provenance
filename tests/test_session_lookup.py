import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "bin" / "provenance"


def call(home, command, *args):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PROVENANCE_DELIVERY_LOG"] = str(home / "missing-deliveries.jsonl")
    return subprocess.run([sys.executable, str(CLI), command, *args],
                          text=True, capture_output=True, env=env)


def claude_session(home, session_id, message):
    path = home / ".claude" / "projects" / "fixture" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "user", "timestamp": "2026-11-18T20:11:00+08:00",
                                "message": {"role": "user", "content": message}}) + "\n", encoding="utf-8")
    return path


def codex_session(home, session_id, message):
    day = datetime.now().strftime("%Y/%m/%d")
    path = home / ".codex" / "sessions" / day / f"rollout-current-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "event_msg", "timestamp": "2026-11-18T12:11:00Z",
                                "payload": {"type": "user_message", "message": message}}) + "\n", encoding="utf-8")
    return path


def test_implicit_lookup_and_short_reference(tmp_path):
    claude_session(tmp_path, "abcd1234-0000-0000-0000-000000000000", "Review only this draft.")
    codex_session(tmp_path, "abcd5678-0000-0000-0000-000000000000", "Check another item.")
    found = call(tmp_path, "quote", "only this draft")
    assert found.returncode == 0, found.stderr
    assert "src: abcd1234:1" in found.stdout
    verified = call(tmp_path, "verify", "𝕌1118 20:11 Review only this draft.⟧",
                    "--source", "src: abcd1234:1")
    assert verified.returncode == 0, verified.stderr
    receipt = json.loads(verified.stdout)
    assert receipt["src"] == "src: abcd1234:1"
    wrong_line = call(tmp_path, "verify", "𝕌1118 20:11 Review only this draft.⟧",
                      "--source", "src: abcd1234:2")
    assert wrong_line.returncode == 1


def test_multiple_implicit_matches_list_candidates_and_exit_two(tmp_path):
    claude_session(tmp_path, "aaaa1111-0000-0000-0000-000000000000", "Review the draft.")
    codex_session(tmp_path, "bbbb2222-0000-0000-0000-000000000000", "Review the draft.")
    found = call(tmp_path, "quote", "Review the draft")
    assert found.returncode == 2
    assert found.stderr.count("candidate:") == 2
    checked = call(tmp_path, "verify", "𝕌1118 20:11 Review the draft.⟧")
    assert checked.returncode == 2
    assert checked.stderr.count("candidate:") == 2


def test_recent_mtime_filter_and_days_override(tmp_path):
    path = claude_session(tmp_path, "cccc3333-0000-0000-0000-000000000000", "Old file, unique words.")
    old = time.time() - 5 * 86400
    os.utime(path, (old, old))
    assert call(tmp_path, "quote", "unique words").returncode == 1
    assert call(tmp_path, "quote", "unique words", "--days", "7").returncode == 0
    assert call(tmp_path, "quote", "unique words", "--days", "0").returncode == 1


def test_short_reference_collision_fails_closed(tmp_path):
    claude_session(tmp_path, "dddd4444-0000-0000-0000-000000000000", "First source.")
    claude_session(tmp_path, "dddd4444-1111-1111-1111-111111111111", "Second source.")
    found = call(tmp_path, "verify", "𝕌1118 20:11 First source.⟧",
                 "--source", "src: dddd4444:1")
    assert found.returncode == 2
    assert found.stderr.count("candidate:") == 2


def test_implicit_lookup_falls_back_to_normalized_words(tmp_path):
    claude_session(tmp_path, "eeee5555-0000-0000-0000-000000000000", "Review　this draft.")
    found = call(tmp_path, "quote", "Review this draft")
    assert found.returncode == 0, found.stderr
    assert "src: eeee5555:1" in found.stdout


def test_quote_prefers_current_session_then_falls_back(tmp_path):
    first = "aaaa1111-0000-0000-0000-000000000000"
    second = "bbbb2222-0000-0000-0000-000000000000"
    claude_session(tmp_path, first, "Review the draft.")
    claude_session(tmp_path, second, "Review the draft. Also inspect the backup.")
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), PROVENANCE_DELIVERY_LOG=str(tmp_path / "missing-deliveries.jsonl"),
               PROVENANCE_SESSION_ID=first)
    current = subprocess.run([sys.executable, str(CLI), "quote", "Review the draft"],
                             env=env, text=True, capture_output=True)
    assert current.returncode == 0 and "src: aaaa1111:1" in current.stdout
    fallback = subprocess.run([sys.executable, str(CLI), "quote", "inspect the backup"],
                              env=env, text=True, capture_output=True)
    assert fallback.returncode == 0 and "src: bbbb2222:1" in fallback.stdout
