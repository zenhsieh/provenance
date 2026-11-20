import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "examples" / "claude-pretooluse.py"
CLI = ROOT / "bin" / "provenance"


def invoke(command):
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}), text=True, capture_output=True)


def test_hook_denies_without_verified_receipt(tmp_path):
    result = invoke("deploy --env production")
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert invoke("python3 -m unittest discover -s tests").stdout == ""


def test_hook_rechecks_receipt(tmp_path):
    source = tmp_path / "log.txt"
    source.write_text("[2026-11-18T20:15:00+08:00] USER: Deploy to production.\n", encoding="utf-8")
    verified = subprocess.run([sys.executable, str(CLI), "verify", "⟦U 1118 20:15 Deploy to production.⟧", "--source", str(source)], text=True, capture_output=True)
    assert verified.returncode == 0
    receipt = tmp_path / "receipt.json"
    receipt.write_text(verified.stdout, encoding="utf-8")
    command = f"deploy --env production\n# provenance-receipt: {receipt}"
    assert invoke(command).stdout == ""
    source.write_text("[2026-11-18T20:15:00+08:00] USER: Wait for review.\n", encoding="utf-8")
    assert json.loads(invoke(command).stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
