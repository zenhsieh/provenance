#!/usr/bin/env python3
"""Opt-in Claude Code PreToolUse gate; reads hook JSON on stdin."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_PATTERNS = {
    "customer_data": r"\b(?:psql|mysql|odoo)\b.*\b(?:INSERT|UPDATE|DELETE|write|import)\b",
    "production_deploy": r"\b(?:deploy|release|kubectl apply|helm upgrade)\b.*\b(?:prod|production)\b",
    "security": r"\b(?:chmod|chown|setfacl|grant|revoke|ir\.model\.access|record.rule)\b",
    "delete_existing": r"\b(?:rm|rmdir|drop|destroy|terraform destroy)\b",
    "external_or_spend": r"\b(?:publish|submit|send|purchase|pay|charge)\b",
}
MARKER = re.compile(r"(?:^|\n)# provenance-receipt: (/[^\s]+)\s*$")


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def patterns():
    path = os.environ.get("PROVENANCE_HOOK_PATTERNS")
    if not path:
        return DEFAULT_PATTERNS
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(pattern, str) for key, pattern in value.items()):
        raise ValueError("pattern config must be an object of regex strings")
    return value


def valid_receipt(path):
    receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        return False
    required = ("quote", "ts", "source", "line", "offset", "sha256")
    if any(key not in receipt for key in required):
        return False
    quote = f'⟦U {receipt["ts"]} "{receipt["quote"]}"⟧'
    cli = Path(__file__).resolve().parents[1] / "bin" / "provenance"
    check = subprocess.run([sys.executable, str(cli), "verify", quote, "--source", str(receipt["source"])],
                           text=True, capture_output=True, timeout=10)
    if check.returncode:
        return False
    actual = json.loads(check.stdout)
    return all(actual[key] == receipt[key] for key in required)


def main():
    try:
        event = json.load(sys.stdin)
        if event.get("tool_name") != "Bash":
            return 0
        command = event.get("tool_input", {}).get("command", "")
        if not isinstance(command, str):
            return deny("Invalid command input")
        matched = [name for name, pattern in patterns().items() if re.search(pattern, command, re.IGNORECASE)]
        if not matched:
            return 0
        marker = MARKER.search(command)
        if not marker:
            return deny(f"Provenance receipt required for: {', '.join(matched)}")
        if not valid_receipt(marker.group(1)):
            return deny("Provenance receipt could not be verified against a human source")
        return 0  # no decision: normal Claude Code permissions still apply
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired, re.error) as exc:
        return deny(f"Provenance hook could not validate input: {exc}")


if __name__ == "__main__":
    sys.exit(main())
