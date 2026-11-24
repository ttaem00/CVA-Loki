# CVA-Loki Capability-Tier & Host Model Recommendations

CVA-Loki does **not** lock you to a single hardcoded model version. Frontier model families evolve rapidly, so Loki separates **capability tiers** from **concrete model labels** and lets each host route dynamically (`inherit` in multi-agent hosts, `--model auto` in single-model local servers, or explicit override when needed).

## 1. Mode × Capability Tier Matrix

| Mode | Prep Speed & Media | Authoring Budget | Recommended Capability Tier | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **LITE** (`--mode lite`) | `360p` (fast download), sampled chat (`partial`), public clips & comments, Warm-Only vector index | 15 min | **Fast Balanced / Workhorse Tier** (fast reasoning or single 14B–32B local quant) | Optimized for rapid timeline + highlight overview without cold GPU vector indexing delays. |
| **NORMAL** (`--mode normal`) | `480p`, full chat (up to 2,500 pages / 600s), public clips & comments | 30 min | **Flagship Reasoning Tier** (strong long-context structured JSON + Korean nuance) | Default everyday production quality: preserves contra-evidence, distant setups, and D1/D2 causality. |
| **FULL** (`--mode full`) | `720p`, full chat (up to 5,000 pages / 1,200s), clips, comments, glossary, deterministic 5m `local_chunk_hints` | 60 min | **Highest Reasoning / Frontier Tier** (deep cross-hour synthesis + multimodal cross-check) | Best for 6–12h broadcasts, complex collab timelines, and full creator archive preparation. |

*Separate clocks*: **Prep Time** (`source_acquire.py` download, STT, and optional local embedding indexing) happens **before** authoring and is reported in `prep_time_note`, never subtracted from `authoring_minutes`.

## 2. Dynamic Host Routing Principles

- **Codex / Antigravity / Claude Code / Gemini CLI**: Use the workspace's active flagship or balanced host session (`--author-mode current_<host>`). In subagent-capable hosts, keep helper/worker lanes on `inherit` unless a user explicitly selects another model.
- **Web AI (`current_chatgpt`, `current_claude_web`, `current_gemini_web`)**: Generate a self-contained bundle with `loki.py export-pack`, upload the `<stem>_files/` directory (at most 8 compact files; ultra-long transcripts sharded automatically into `<= 5` parts), run the included `verify_result.py` check locally or ask Web AI to verify, and ingest via `loki.py import-draft`.
- **Local LLM (`current_local_llm`)**: Run `runtime/local_llm_author.py --model auto`. When the local endpoint exposes multiple GGUF/Ollama tags, Loki filters out non-generative utility tags (`embed`, `rerank`, `whisper`, `tts`, `bge-`, `nomic-`, `clip`) and requires a single unambiguous chat model unless you pass `--model <tag>` explicitly.
- **Short Video Verification (`runtime/short_video_check.py`)**: Defaults to `None` (lets the configured `gemini-api` CLI default run) unless you pass `--model <name>` or set `LOKI_SHORT_VIDEO_MODEL`. Missing CLI binaries or missing auth cleanly degrade without blocking report generation.

## 3. Illustrative Model Examples (Replaceable, Not Locked)

Concrete releases change frequently; treat the names below strictly as point-in-time illustrations of each capability tier:

- **Flagship Reasoning Tier (FULL / NORMAL)**: Flagship coding & frontier reasoning models such as OpenAI `gpt-6.2-sol` (or newer GPT flagship), Google Gemini frontier (`Gemini 4` Pro / flagship class), or Anthropic Claude flagship (`Claude 5.5` Sonnet/Opus class).
- **Fast Balanced Tier (LITE)**: Fast low-latency models such as Gemini Flash / Flash-Lite class, GPT Mini class, or Claude Haiku/Sonnet fast class.
- **Local Single-GPU Open Weights (`local_llm_author.py`)**: Reasoning-capable dense or MoE local chat weights with 32K+ context configured (for example, `Qwen 3.5` / `Gemma 4` instruction variants or equivalent 14B–32B quantizations with `num_ctx >= 32768`). Always keep embedding search on the pinned local `EmbeddingGemma 2` index inside EvidenceWorld rather than swapping chat models into vector storage.
