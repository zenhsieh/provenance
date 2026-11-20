import json
import os
import subprocess
import sys
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "bin" / "provenance"


def call(fragment, *, source=None, session=None, home=None, iso=False, legacy=False, tz=None, delivery_log=None, days=None):
    command = [sys.executable, str(CLI), "quote", fragment]
    if source:
        command += ["--source", str(source)]
    elif session:
        command += ["--session", session]
    if days is not None:
        command += ["--days", str(days)]
    if iso:
        command.append("--iso")
    if legacy:
        command.append("--legacy")
    if tz:
        command += ["--tz", tz]
    if delivery_log:
        command += ["--delivery-log", str(delivery_log)]
    env = os.environ.copy()
    if home:
        env["HOME"] = str(home)
    env["PROVENANCE_DELIVERY_LOG"] = str((Path(home) if home else Path(source).parent) / "missing-deliveries.jsonl")
    return subprocess.run(command, text=True, capture_output=True, env=env)


def test_quote_uses_source_time_and_full_human_message(tmp_path):
    path = tmp_path / "chat.jsonl"
    path.write_text(json.dumps({"type": "event_msg", "timestamp": "2026-11-18T12:11:12Z",
                                "payload": {"type": "user_message", "message": "Please write all Markdown files now."}}) + "\n", encoding="utf-8")
    result = call("write all Markdown", source=path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"𝕌1118 20:11 Please write all Markdown files now.⟧\nsource: {path} line 1\n"
    assert "5x" not in result.stdout
    iso = call("write all Markdown", source=path, iso=True)
    assert iso.returncode == 0
    assert iso.stdout.startswith('𝕌2026-11-18T20:11:12+08:00 "Please write all Markdown files now."⟧\n')
    old = call("write all Markdown", source=path, legacy=True)
    assert old.stdout.startswith("⟦U 1118 20:11 Please write all Markdown files now.⟧\n")


def test_quote_ignores_words_inside_human_citation(tmp_path):
    words = "不過應該也快備份完了吧 備份完就可以繼續刪了"
    cited = f"𝕌1119 00:49 {words}⟧"
    relay = tmp_path / "relay.jsonl"
    relay.write_text(json.dumps({"type": "user", "timestamp": "2026-11-18T16:57:56Z",
                                 "message": {"role": "user", "content": f"user 00:49 原話：{cited} ⇒ note"}},
                                ensure_ascii=False) + "\n", encoding="utf-8")
    assert call(words, source=relay).returncode == 1
    original = tmp_path / "original.jsonl"
    original.write_text(json.dumps({"type": "user", "timestamp": "2026-11-18T16:49:27Z",
                                    "message": {"role": "user", "content": words}},
                                   ensure_ascii=False) + "\n", encoding="utf-8")
    result = call(words, source=original)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"𝕌1119 00:49 {words}⟧")


def test_quote_ambiguous_lists_candidates_and_exits_two(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text("[2026-11-18T20:11:00+08:00] USER: Review draft A.\n"
                    "[2026-11-18T20:12:00+08:00] USER: Review draft B.\n", encoding="utf-8")
    result = call("Review draft", source=path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.count("candidate:") == 2
    assert "longer fragment" in result.stderr


def test_quote_rejects_missing_or_nonhuman_text(tmp_path):
    path = tmp_path / "chat.jsonl"
    path.write_text(json.dumps({"type": "event_msg", "timestamp": "2026-11-18T12:11:12Z",
                                "payload": {"type": "agent_message", "message": "Deploy now."}}) + "\n", encoding="utf-8")
    result = call("Deploy now", source=path)
    assert result.returncode == 1
    assert result.stdout == ""


def test_quote_session_reference_and_timezone_override(tmp_path):
    path = tmp_path / ".codex" / "sessions" / "2026" / "11" / "18" / "rollout-fixture123.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "event_msg", "timestamp": "2026-11-18T12:11:12Z",
                                "payload": {"type": "user_message", "message": "Use source time."}}) + "\n", encoding="utf-8")
    result = call("source time", session="fixture123", home=tmp_path, tz="UTC")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "𝕌1118 12:11 Use source time.⟧\nsrc: fixture1:1\n"


def test_quote_rejects_agent_relay_header_even_with_user_role(tmp_path):
    path = tmp_path / "claude.jsonl"
    path.write_text(json.dumps({"type": "user", "timestamp": "2026-11-18T12:11:12Z",
                                "origin": {"kind": "human"}, "userType": "external",
                                "message": {"role": "user", "content": "【sender-test｜relay】Quote these words."}}) + "\n", encoding="utf-8")
    assert call("Quote these words", source=path).returncode == 1


def test_quote_ascii_fragment_next_to_cjk_character(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text("[2026-11-18T20:11:00+08:00] USER: 這樣會反而浪費token嗎\n", encoding="utf-8")
    result = call("這樣會反而浪費token", source=path)
    assert result.returncode == 0, result.stderr
    assert "𝕌1118 20:11 這樣會反而浪費token嗎⟧" in result.stdout
    assert call("token", source=path).returncode == 0


def test_non_cjk_letters_still_share_a_word(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text("[2026-11-18T20:11:00+08:00] USER: café\n", encoding="utf-8")
    assert call("caf", source=path).returncode == 1


def test_quote_rejects_historical_relay_headers(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text("[2026-11-18T20:11:00+08:00] USER: sender-a → receiver-a：Quoted words.\n"
                    "[2026-11-18T20:12:00+08:00] USER: ⟨sender-a 轉交⟩ More words.\n", encoding="utf-8")
    assert call("Quoted words", source=path).returncode == 1
    assert call("More words", source=path).returncode == 1
