# Web AI Pack Workflow (ChatGPT Web · Claude Web · Gemini Web)

When authoring inside a browser conversation without direct local shell execution (`current_chatgpt`, `current_claude_web`, `current_gemini_web`), use `export-pack` and `import-draft` so the full EvidenceWorld context transfers cleanly without violating file-count or context-window limits.

## 1. Initialize & Export the Web Pack

1. Initialize and prepare the workspace for your target Web host:

```sh
python <runtime>/loki.py init --directory <workspace> --author-mode current_claude_web
# Prepare after source_acquire.py (or local SRT):
python <runtime>/loki.py --config <workspace>/loki.json prepare --spec <workspace>/spec.json
```

Supported web modes are `current_chatgpt`, `current_claude_web`, and `current_gemini_web`.

2. Export the Web Pack:

```sh
python <runtime>/loki.py --config <workspace>/loki.json export-pack ^
  --job-root <job-root> --expected-revision <revision> ^
  --output <workspace>/pack/vod_pack.zip
```

This creates **both**:
- `<workspace>/pack/vod_pack.zip` (handy single-archive upload for ChatGPT Web or Google AI Studio)
- `<workspace>/pack/vod_pack_files/` (unpacked folder of `<= 8` files ready for Gemini Web folder upload or Claude Web Project upload)

## 2. What Is Inside `<stem>_files/`

| File | Purpose |
| :--- | :--- |
| `README_WEB_PROMPT.md` | Authoring instructions, mode goal, strict ID/interval rules, and JSON output schema summary. |
| `evidence_clusters.json` | Vector clusters (untruncated), phrase/topic views, low-reaction coverage windows, user clips, timeline comments, chat samples, broadcast flow context, media availability, `valid_record_ids`, and `exported_chat_ids`. |
| `transcript_condensed.md` or `transcript_part1..5.md` | Full readable transcript, automatically sharded dynamically into at most `5` parts (`>= 220 KB` per part) for 200K-token browser windows such as Claude Web. |
| `verify_result.py` | Local standalone sanity checker to verify cited evidence IDs, schema keys, and highlight timestamp bounds before importing. |

**Privacy & Size Guard**: Raw chat JSONLs and local `.mp4` videos are excluded by default so the pack remains compact (`<= 8` files) and safe for upload. Offline highlight image extraction still runs during `import-draft` on your local machine if `media_path` was prepared.

## 3. Author in the Browser & Verify

Upload `vod_pack.zip` (or the files in `vod_pack_files/`) to your Web AI session and ask it to follow `README_WEB_PROMPT.md` and return the complete JSON draft grounded exclusively in `evidence_clusters.json` and the transcript parts.

Save the model's JSON output to `<workspace>/web_draft.json`. Optionally pre-check it locally:

```sh
python <workspace>/pack/vod_pack_files/verify_result.py <workspace>/web_draft.json <workspace>/pack/vod_pack_files/evidence_clusters.json
```

## 4. Import Draft & Extract Highlight Frames Locally

Run `import-draft` offline (`network=deny` enforced automatically):

```sh
python <runtime>/loki.py --config <workspace>/loki.json import-draft ^
  --job-root <job-root> --expected-revision <revision> ^
  --draft <workspace>/web_draft.json
```

What `import-draft` does under the hood:
1. Validates that the workspace uses a Web author mode (`current_chatgpt`, `current_claude_web`, or `current_gemini_web`).
2. Extracts the JSON object safely (even if wrapped in Markdown code fences) and verifies cited `evidence_refs` against the saved `web-packs/<sha256>.json` contract.
3. Reads any un-read cited original evidence records via `read_evidence` (following `continuation` cursors locally) before calling `submit()`, preserving Loki's source-bound `full_read_sha256` guarantee and offline local MP4 highlight image extraction (`<=720p` supported offline).

## 5. Export Creator Formats (Optional)

Once `submit` succeeds, export directly from the terminal using the returned `report_metadata.json` path:

```sh
python <runtime>/loki.py --config <workspace>/loki.json export --report <report-metadata.json> --format comment
python <runtime>/loki.py --config <workspace>/loki.json export --report <report-metadata.json> --format markers-csv --output <workspace>/markers.csv
python <runtime>/loki.py --config <workspace>/loki.json export --report <report-metadata.json> --format fcpxml --output <workspace>/timeline.fcpxml
```

(`--format comment` outputs the `ttaempad`-compatible comment layout by default; `exporters.export_report(..., style="viewer")` is also available in Python for viewer-only copy.)
