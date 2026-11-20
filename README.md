# Provenance

Provenance keeps human words separate from agent judgments in multi-agent work. It provides a short agent skill, a local quote verifier, a heuristic linter, and an optional Claude Code hook example. The verifier reads logs; it does not modify them or grant permission to act.

Read [the article](docs/article.md) for the workflow and examples. After cloning, run `./bin/provenance quote 'distinctive words' --source /path/to/session.jsonl`; link `skill/` into your agent's skill directory to install the skill.

## Quote format

```text
𝕌2026-11-18T20:15:00+08:00 "Review the draft first."⟧
𝕌1118 20:15 Review the draft first.⟧
𝕌#447⟧
𝗫codex 1118 20:16 The review is still pending.⦘
```

The ISO form includes its offset. The `MMDD HH:MM` form uses Asia/Taipei by default; `--tz` accepts an IANA name or an offset such as `+08:00`. `20:1x` spans 20:10–20:19. A card pointer needs a card containing the human's words and time. Agent judgments use `𝗖` Claude, `𝗫` Codex, `𝗚` Grok, or `𝗚𝗲` Gemini, followed by `author time content⦘`. Older `⟦U …⟧`, `⟦U #card⟧`, `⟪…⟫`, `⦅…⦆`, and `⟬…⟭` remain valid. Parse markers before NFKC normalization; NFKC would fold `𝕌` to `U`. See [SPEC.md](SPEC.md) for the authority and scope rules.

## Verify a quote

Python 3.11 or newer is required. The CLI has no third-party runtime dependencies.

```sh
./bin/provenance verify '𝕌1118 20:15 Review the draft first.⟧' --source /path/to/session.jsonl
./bin/provenance verify '𝕌1118 20:15 Review the draft first.⟧' --session <session-id>
./bin/provenance verify '𝕌1118 20:15 Review the draft first.⟧' --source 'src: 8a04bbef:1040'
./bin/provenance verify '𝕌1118 20:15 Review the draft first.⟧'
./bin/provenance verify '𝕌1118 12:15 Review the draft first.⟧' --source /path/to/log.txt --tz UTC
```

`--session` searches only `~/.claude/projects/*/<id>.jsonl` and `~/.codex/sessions/YYYY/MM/DD/*<id>.jsonl`. Without either source option, `quote` and `verify` search recently modified files in those fixed locations. The default window is three days; `--days N` adjusts it. Implicit `quote` lookup has a three-second budget, and implicit `verify` lookup has a one-second budget; each reports a timeout when it cannot finish. A plain text log must label the speaker, for example `[2026-11-18T20:15:00+08:00] USER: Review the draft first.` Claude `queued_command` attachments count only when `humanTurn=true` and `origin.kind=human`. Assistant messages, tool results, and unlabeled lines do not count.

Success prints one JSON receipt with the matched words, actual source timestamp, absolute source path, line number, byte offset, and SHA-256 of the source line. Failure exits 1 and prints a reason to stderr. Matching normalizes whitespace and full-width characters, and accepts a quote inside a longer human message at word boundaries. CJK characters count as boundaries even when adjacent to ASCII letters. An exact timestamp may differ by up to five minutes; an `Nx` minute must match that ten-minute window. A receipt establishes that the words occurred in a human message. Check authority and action scope separately.

Create new citations with the source clock instead of typing a time from memory:

```sh
./bin/provenance quote 'distinctive words from the human message' --session <session-id>
./bin/provenance quote 'distinctive words' --source /path/to/session.jsonl --iso
./bin/provenance quote 'distinctive words'
```

`quote` prints the complete matched human message as `𝕌MMDD HH:MM …⟧` and, for session files, `src: <first 8 session characters>:<line>`. `verify --source 'src: …'` locates and checks that line. If the short prefix names multiple sessions, it exits 2 with candidates; use a full `--session` or file path. An explicit source outside the fixed session roots retains its full path reference. The clock defaults to Asia/Taipei and accepts `--tz`; `--iso` includes an explicit offset; `--legacy` emits `⟦U …⟧`. It never emits `5x`. No match exits 1. Multiple human messages matching the fragment are listed as candidates on stderr and exit 2; provide a longer fragment. Before relaying an existing citation, run `verify` against the **original human** session and preserve its verified time.

Agent deliveries can appear as `type=user` in a receiving agent's session. The reader excludes messages whose normalized text hash is in the append-only `~/.local/state/provenance/deliveries.jsonl` ledger. It also excludes messages beginning with delivery-style `【…→…】`, `【…｜…】`, `<agent> → <agent>：`, or `⟨<agent> 轉交⟩` headers when no record is available. `PROVENANCE_DELIVERY_LOG` or `--delivery-log` selects another ledger path. The ledger stores hashes of the final delivered text, not the human's words; see [SPEC.md](SPEC.md) for its format. A receiving agent's transcript is not a substitute for the original human session.

## Lint records

```sh
./bin/provenance lint notes.md decisions/*.md
```

Lint exits 1 when it finds a claim that the human approved or decided something without `𝕌…⟧` or a legacy quote in that line or list item, or agent-voice material inside a human quote. Pending questions such as “待 user 決定” are not approval claims. Ordinary human wording such as “應該” and “所以” is not treated as agent inference. It accepts new and legacy quotes, card pointers, and agent markers. It also warns about a closing `⟧` or `⦘` with no opening marker, which can signal that normalization lost `𝕌` or a bold agent letter. It skips fenced code and reports file and line. These are lexical findings for review, not a proof that a record is valid or invalid.

Use `lint --verify` to check quotes whose line or list item has a `source: <path>`, `source: session <id>`, `session: <id>`, or `src: <session8>:<line>` reference. Relative paths are resolved from the document's directory. A mismatch reports `quote not found in source`; quotes without a reference are left unverified. Card pointers require reading the referenced card and are not checked as verbatim quotes by this option.

## Install the skill and CLI

The repository keeps the skill in `skill/` and the executable in `bin/`. A fleet administrator can point the agent skill directories and command path at them, for example:

```sh
ln -s /absolute/path/to/provenance/skill ~/.agents/skills/provenance
ln -s /absolute/path/to/provenance/skill ~/.claude/skills/provenance
ln -s /absolute/path/to/provenance/bin/provenance ~/bin/provenance
```

Use the paths and installation timing approved by your fleet administrator. The repository does not install them automatically.

## Quote time hook

`provenance hook` handles `PreToolUse` Bash, Write, and Edit. It checks only new tool input containing `𝕌` or `⟦U`. For a unique human source, it rewrites wrong, approximate, or omitted quote times in the pending tool input and allows the call. It searches the current transcript first, then recent sessions. A genuinely unfindable quote is denied; ambiguous or timed-out lookups are left unchanged. [Claude Code tool hooks](https://code.claude.com/docs/en/hooks)

The fleet administrator can opt in through Claude Code hook settings, using the same command for both events:

```json
{
  "hooks": {
    "PreToolUse": [{"matcher": "Bash|Write|Edit", "hooks": [{"type": "command", "command": "python3 /absolute/path/to/provenance/bin/provenance hook"}]}]
  }
}
```

The Codex adapter is `provenance codex-hook --mode enforce`. It handles `PreToolUse` Bash (including unified exec) and `apply_patch`; it inspects and rewrites only lines added by the patch. Enforce mode returns `permissionDecision: "allow"` with `updatedInput` for a verifiable correction. Shadow mode returns advisory context without rewriting. [Codex hook event and output format](https://learn.chatgpt.com/docs/hooks). Install a locally reviewed hooks configuration at `~/.codex/hooks.json` and trust its new definitions in Codex `/hooks` before they run.

### Hook observation log

Every `provenance hook` invocation appends one JSONL event to `${XDG_STATE_HOME:-~/.local/state}/provenance/hook.jsonl`. The directory is private (`0700`) and the file is `0600`. Each event records UTC time, the first eight session ID characters, cwd, tool, event name, verdict (`pass`, `rewrite`, `deny`, `feedback`, `timeout`, or `error`), elapsed milliseconds, and parsed quote count. Rewrites, denials, and feedback also record the cited fragment's SHA-256 and first 20 characters. A logging failure leaves the hook result unchanged.
`codex-hook` writes to the same file with `agent=codex`; Claude records have `agent=claude`. Use `hook-report --hours 12 --agent codex` to inspect one agent.

```sh
./bin/provenance hook-report --hours 12
```

`hook-report` counts recent events and verdicts, gives p50/p95 elapsed milliseconds, and lists timeout and error events with their diagnostic metadata. The default window is 24 hours. It reads the local log only; it does not submit data elsewhere.

## Optional five-category Claude Code gate

`examples/claude-pretooluse.py` is a [PreToolUse command hook](https://code.claude.com/docs/en/hooks). It reads Claude Code's JSON event on stdin. For a Bash command matching one of five configurable pattern groups, it requires a receipt marker at the end of the command:

```sh
deploy --env production
# provenance-receipt: /absolute/path/to/receipt.json
```

The hook reruns `provenance verify` on the receipt's quote and source and compares its line and hash. It returns `permissionDecision: deny` when the receipt is absent or fails verification. On success it returns no decision, leaving Claude Code's normal permissions in place. The hook cannot determine whether the quoted words authorize that particular command. Review the target and scope yourself. Pattern matching is a conservative prompt gate, not a complete classifier; change its expressions for your environment with `PROVENANCE_HOOK_PATTERNS=/absolute/path/to/patterns.json`, a JSON object mapping category names to Python regular expressions.

To opt in for one project, add a hook entry to that project's `.claude/settings.local.json` with an absolute script path:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "python3 /absolute/path/to/provenance/examples/claude-pretooluse.py"}
        ]
      }
    ]
  }
}
```

This is an example only. It is not installed into any Claude settings file by this repository.

## Tests

Tests use fabricated logs only:

```sh
python3 -m unittest discover -s tests
```

The live PreToolUse hook also checks test commands. A command that deliberately contains a `⟦U⟧` citation with the wrong time is denied, which is expected. In a test command, spell the opening bracket as the literal escape `\u27e6` so the command text does not contain a citation, or keep the wrong-time case in an existing fixture file and run the test by file path.

If pytest is available, `pytest -q tests` runs the same cases. No real session contents or private quotes belong in this repository.

## License

Licensed under the Apache License, Version 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright 2026 zenhsieh.
