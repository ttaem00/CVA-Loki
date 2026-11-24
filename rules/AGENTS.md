# CVA-Loki Antigravity Rules

1. **Authoring Boundary (`network=deny`)**: Never call external paid LLM APIs, web scrapers, or secondary author CLIs inside the authoring phase. When a workspace runs under `--author-mode current_antigravity`, the active Antigravity conversation reads complete evidence via `runtime/loki.py` and authors the final structured report directly.
2. **Dynamic Host Model Selection (`inherit`)**: Use the user's currently active flagship/balanced Antigravity model (default `inherit` for subagents/writers; avoid hardcoding transient model version names). Allocate `lite` (15m), `normal` (30m), or `full` (60m) mode according to task scope, keeping prep time (`source_acquire`) separate from bounded authoring time (`authoring_minutes`).
3. **Zero-Re-Read & Pre-Submit Hygiene**:
   - Inspect `loki.py status --include-read-summary` before re-reading STT/chat ranges to avoid duplicate chunk fetches.
   - Always run `loki.py submit --validate-only` before finalizing submission and reconcile any `unreconciled_note_reads`.
   - Use optional `runtime/short_video_check.py` only when motion/audio ambiguity cannot be resolved by transcripts and `read_frames`; keep missing/unauthenticated CLI checks non-blocking.
4. **Workspace Isolation**: Keep runtime inputs, outputs, and virtual environments strictly inside the owned user workspace outside read-only plugin files. Never inspect host credentials, cookies, or browser profiles.
