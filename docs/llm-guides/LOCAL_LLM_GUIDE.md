# Local LLM Workflow (Ollama · vLLM · LM Studio)

`runtime/local_llm_author.py` enables **100% local, zero-cloud-cost** authoring against an Ollama or OpenAI-compatible local server (`http://127.0.0.1:11434` for Ollama or any `/v1/chat/completions` endpoint such as vLLM or LM Studio), followed by an offline `loki.isolate()` final `submit` pass.

## 1. Prerequisites & Context Size

- Configure the workspace with `--author-mode current_local_llm`.
- Ensure your local serving runtime exposes at least **32K context** (`32768` tokens):
  - For Ollama, `local_llm_author.py` automatically sends `"options": {"num_ctx": 32768}` to `/api/chat`.
  - For vLLM or LM Studio, start the server with `--max-model-len 32768` (or >=32K context preset).
- Keep only **one generative chat model** loaded when using `--model auto`, or specify `--model <exact-tag>` when multiple chat models are available.

## 2. Dynamic `--model auto` Resolution

When `--model auto` (the default) is passed:
1. If `--base-url` responds to `GET /api/tags` (Ollama), Loki inspects loaded chat tags; otherwise it queries `GET /v1/models` (OpenAI-compatible).
2. Utility/non-generative models matching `embed`, `rerank`, `whisper`, `tts`, `bge-`, `nomic-`, or `clip` are automatically ignored.
3. If **exactly one** generative chat candidate remains, Loki selects it automatically and logs its actual name in `local-llm-runs/*.json`. If multiple generative candidates exist, pass `--model <name>` explicitly.

## 3. Running 3-Pass Offline Authoring

```sh
python <runtime>/local_llm_author.py ^
  --config <workspace>/loki.json ^
  --job-root <job-root> ^
  --expected-revision <revision> ^
  --endpoint http://127.0.0.1:11434/v1 ^
  --model auto
```

(`--config` is automatically discovered from `--job-root` ancestors if `loki.json` lives at the workspace root.)

### How the 3-Pass Loop Works

1. **Pass 0 (Local Evidence Acquisition — 0 LLM calls)**: Fetches `author-input.json`, prior read coverage, vector clusters/peaks, and executes `read_evidence` (plus paginated continuations) in priority order until complete or byte-budgeted.
2. **Pass 1 (Macro `D1`/`D2` Outline — Structured Schema)**: Generates `summary`, `summary_evidence_refs`, `D1`/`D2` chronological structure, and `group_outcomes`. Strips reasoning tokens (`<think>...</think>` or Harmony channel wrappers) before extracting JSON.
3. **Pass 2 (Points · Stories · Highlights · Repair)**: Generates `points`, `stories`, `proposals`, `unresolved_questions`, normalizes minor D1/D2 `parent_id` or `span[0]` edge cases deterministically, runs offline `submit --validate-only`, executes at most one targeted repair turn if validation errors remain, and enforces **`loki.isolate()`** before calling the final offline `current.submit()`.

Run receipts are saved cleanly under `<job-root>/local-llm-runs/`.
