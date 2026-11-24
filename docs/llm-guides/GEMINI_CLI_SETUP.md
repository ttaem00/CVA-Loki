# Gemini CLI Setup & Short Video Check Guide

CVA-Loki ships native support for `@google/gemini-cli` (`gemini-extension.json`, `rules/GEMINI.md`, the `loki` agent skill under `skills/loki/SKILL.md`, and extension policy rules in `policies/loki-author.toml`).

## 1. Install the Gemini CLI Extension

Link or install the extension directly from the bundle root (or GitHub repo) via the Gemini CLI:

```sh
gemini extensions validate <bundle-root>
gemini extensions install <bundle-root> --consent --skip-settings
```

And initialize your separate user-owned workspace & runtime (use `--mode setup` for full offline speech/embedding setup, or `--mode skill` to link an existing workspace):

```sh
python install.py --workspace ../loki-gemini-workspace --mode skill --author-mode current_gemini_cli
```

Inside a Gemini CLI session, invoke the `loki` skill and specify your mode (`--mode lite`, `--mode normal`, or `--mode full`):
- **LITE** (`--mode lite`) — 360p fast prep, Warm-Only vector index, 15m budget
- **NORMAL** (`--mode normal`) — 480p balanced prep, full chat/clips/comments, 30m budget
- **FULL** (`--mode full`) — 720p full pre-collection, deterministic 5m hints, 60m budget

## 2. Extension Policy Rules vs. Optional User Auto-Allow

By design, Gemini CLI **blocks `allow` rules inside extension policies** (`policies/loki-author.toml`) and restricts them to Tier 2 (`ask_user` / `deny`). The shipped `policies/loki-author.toml` denies credential/cookie file reads and prompts before online `source_acquire.py` or `gemini-api` invocations.

If you prefer auto-approving offline `loki.py` read/status/validate commands in interactive sessions, add a **user-tier** policy file at `~/.gemini/policies/cva-loki-user.toml`:

```toml
[[rule]]
toolName = "run_shell_command"
commandRegex = "loki\\.py\\s+.*\\b(doctor|prepare|status|read|remember|memory|submit --validate-only)\\b"
decision = "allow"
priority = 100
```

## 3. Optional Short Video Verification (`gemini-api`)

When STT + static `read_frames` images cannot resolve an action/motion or ambiguous audio scene inside a **<= 60 second** window, you can optionally run `runtime/short_video_check.py`:

```sh
python <runtime>/short_video_check.py ^
  --video <workspace>/work/<video_no>/source.mp4 ^
  --start-sec 120 --end-sec 145 ^
  --question "Did the player clear the boss phase before time expired?"
```

- **Zero model lock-in**: Default model is `None` (defers to your `gemini-api` CLI configuration). Override on demand with `--model <model-name>` or env `LOKI_SHORT_VIDEO_MODEL`.
- **Payload safety**: Slices are transcode-capped (~480p, 1fps, `<= 14 MiB` inline limit) to avoid overflowing inline CLI payloads.
- **Non-blocking fallback**: If `gemini-api` is not installed (`gemini_api_unavailable`) or needs OAuth login (`gemini_api_auth_required`, exit code `3`), Loki leaves that observation as unresolved/deferred and continues report submission cleanly.
