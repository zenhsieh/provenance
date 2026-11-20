import json
import os
import subprocess
import sys
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "bin" / "provenance"


def lint(tmp_path, content, *, verify=False, home=None):
    path = tmp_path / "record.md"
    path.write_text(content, encoding="utf-8")
    command = [sys.executable, str(CLI), "lint"]
    if verify:
        command.append("--verify")
    env = os.environ.copy()
    if home:
        env["HOME"] = str(home)
    return subprocess.run([*command, str(path)], text=True, capture_output=True, env=env)


def test_flags_unquoted_approval_and_embedded_inference(tmp_path):
    result = lint(tmp_path, "The user approved production deployment.\n\n"
                  "⟦U 1118 19:5x Deploy this. Agent infers all tenants too.⟧\n")
    assert result.returncode == 1
    assert "lacks 𝕌" in result.stdout
    assert "inference inside human quote" in result.stdout


def test_legacy_card_and_agent_brackets_are_accepted(tmp_path):
    result = lint(tmp_path, "The user approved the draft: ⟦U #447⟧\n\n"
                  "⟪claude 1118 20:00 Check card 447.⟫\n"
                  "⦅codex 1118 20:00 Scope is narrow.⦆\n"
                  "⟬grok 1118 20:00 Check source.⟭\n")
    assert result.returncode == 0, result.stdout


def test_code_fence_is_ignored(tmp_path):
    result = lint(tmp_path, "```text\nThe user approved it.\n```\n")
    assert result.returncode == 0


def test_quote_exemption_is_per_list_item(tmp_path):
    result = lint(tmp_path, "- user 已核准部署。\n"
                  "- User approved draft: ⟦U 1118 19:54 Draft only.⟧\n"
                  "- User approved deletion.\n")
    assert result.returncode == 1
    findings = [line for line in result.stdout.splitlines() if "lacks 𝕌" in line]
    assert len(findings) == 2
    assert any(":1:" in line for line in findings)
    assert any(":3:" in line for line in findings)


def test_wrapped_list_item_can_cite_on_its_own_continuation(tmp_path):
    result = lint(tmp_path, "- User approved the draft.\n"
                  "  ⟦U 1118 19:54 The draft is approved.⟧\n"
                  "- user 說可以刪舊庫。\n")
    assert result.returncode == 1
    assert ":1:" not in result.stdout
    assert ":3:" in result.stdout


def test_agent_voice_inside_quote(tmp_path):
    examples = (
        "我推測他也同意刪舊庫", "我判斷可刪", "我認為他想刪",
        "deploy it, so he probably also wants deletion", "presumably he agreed",
        "I infer approval", "the user also agreed", "user 也同意",
        "⟪agent 1118 19:54 I infer approval⟫",
    )
    for words in examples:
        result = lint(tmp_path, f"⟦U 1118 19:54 {words}⟧\n")
        assert result.returncode == 1, words
        assert "inference inside human quote" in result.stdout, words


def test_human_style_with_general_reasoning_words_is_clear(tmp_path):
    result = lint(tmp_path, "⟦U 1118 19:54 這個應該可以先做，所以今天做完，對吧？⟧\n"
                  "⟦U 1118 19:55 I probably want that, so please check.⟧\n")
    assert result.returncode == 0, result.stdout


def test_pending_decision_is_not_claim_but_dated_instruction_is(tmp_path):
    result = lint(tmp_path, "- 待 user 決定部署時段。\n"
                  "- 要不要由 user 決定是否刪除？\n"
                  "- 2026-09-30 user 指示關掉。\n")
    assert result.returncode == 1
    assert result.stdout.count("lacks 𝕌") == 1
    assert ":3:" in result.stdout


def test_new_markers_and_lost_opening_are_checked(tmp_path):
    result = lint(tmp_path, "User approved: 𝕌1118 19:54 Draft only.⟧\n"
                  "𝗫codex 1118 20:00 This is an inference.⦘\n"
                  "U1118 19:54 Lost human marker.⟧\n"
                  "Ccodex 1118 20:00 Lost agent marker.⦘\n")
    assert result.returncode == 1
    assert result.stdout.count("closing marker without opening marker") == 2
    assert ":3:" in result.stdout and ":4:" in result.stdout
    assert ":1:" not in result.stdout and ":2:" not in result.stdout


def test_redundant_user_original_prefix_before_citation(tmp_path):
    result = lint(tmp_path, "user 原話：𝕌1119 00:49 先做。⟧\n"
                  "user 00:49 原話：⟦U 1119 00:49 再做。⟧\n"
                  "直接引用 𝕌1119 00:49 完成。⟧\n")
    assert result.returncode == 1
    assert result.stdout.count("redundant human quote prefix") == 2
    assert ":1:" in result.stdout and ":2:" in result.stdout
    assert ":3:" not in result.stdout


def test_marker_names_in_inline_code_are_not_lost_openings(tmp_path):
    result = lint(tmp_path, "The closing markers `⟧` and `⦘` are documented here.\n")
    assert result.returncode == 0, result.stdout


def test_verify_source_reference_finds_mismatch(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("[2026-11-18T19:54:00+08:00] USER: Deploy the draft.\n", encoding="utf-8")
    result = lint(tmp_path, "- Approved: ⟦U 1118 19:54 Deploy the draft.⟧ source: source.txt\n"
                  "- Approved: ⟦U 1118 19:54 Deploy all sites.⟧ source: source.txt\n", verify=True)
    assert result.returncode == 1
    assert result.stdout.count("quote not found in source") == 1
    assert ":2:" in result.stdout


def test_verify_is_optional_and_skips_unreferenced_quote(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("[2026-11-18T19:54:00+08:00] USER: The draft only.\n", encoding="utf-8")
    content = "⟦U 1118 19:54 Deploy all sites.⟧ source: source.txt\n"
    assert lint(tmp_path, content).returncode == 0
    assert lint(tmp_path, "⟦U 1118 19:54 Deploy all sites.⟧\n", verify=True).returncode == 0


def test_verify_session_reference(tmp_path):
    source = tmp_path / ".codex" / "sessions" / "2026" / "11" / "18" / "rollout-fixture123.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"type": "event_msg", "timestamp": "2026-11-18T11:54:00Z",
                                  "payload": {"type": "user_message", "message": "Draft only."}}) + "\n", encoding="utf-8")
    result = lint(tmp_path, "⟦U 1118 19:54 Draft only.⟧ session: fixture123\n", verify=True, home=tmp_path)
    assert result.returncode == 0, result.stdout


def test_verify_short_src_reference(tmp_path):
    source = tmp_path / ".claude" / "projects" / "fixture" / "abcd1234-0000-0000-0000-000000000000.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"type": "user", "timestamp": "2026-11-18T19:54:00+08:00",
                                  "message": {"role": "user", "content": "Draft only."}}) + "\n", encoding="utf-8")
    result = lint(tmp_path, "𝕌1118 19:54 Draft only.⟧ src: abcd1234:1\n", verify=True, home=tmp_path)
    assert result.returncode == 0, result.stdout
