# CVA-Loki Gemini CLI Extension Context

Use the bundled `loki` skill (`skills/loki/SKILL.md` and `skills/loki/references/workflow.md`) for evidence-grounded CHZZK, YouTube Live, and SOOP VOD analysis and structured report authoring.

## Host & Author Contract
- Selected author mode in user workspace: `--author-mode current_gemini_cli` (with `network=deny` during offline authoring).
- Use whichever Gemini model tier the user configured in Gemini CLI dynamically (default reasoning tier for FULL/NORMAL; faster tier optional for LITE). Never hardcode fixed model names in commands or drafts.
- Execution modes supported through the `loki` skill: `lite` (`--mode lite`, 360p fast prep, Warm-Only vectors, 15m budget), `normal` (`--mode normal`, 480p balanced prep, full chat/clips/comments, 30m budget), and `full` (`--mode full`, 720p full prep + 5m deterministic chunk hints, 60m budget).

## Key Runtime Commands
- Inspect quick contract & previously read intervals before issuing new reads: `status --include-read-summary`.
- Pre-flight validate before final submission: `submit --validate-only`.
- Optional non-blocking motion/audio verification (<=60s window only when `read_frames` + STT cannot decide): `runtime/short_video_check.py`.
- Creator exports: `export --format comment --style ttaempad|viewer`, `markers-csv`, `fcpxml`.
