# Current Codex authoring workflow

Use the installed `runtime/loki.py` by its actual absolute path. In the examples,
`<runtime>` is the directory next to this skill containing `loki.py`, and
`<workspace>` is a separately initialized user-owned directory. Angle-bracketed
values are replaced with observed paths/identities, never executed literally.

## Prepare one public VOD

The default `multimodal` installation includes public-source dependencies, local
Whisper large-v3-turbo, Silero VAD and the existing anonymous F2 speaker-turn path.
Use the interpreter/runtime returned by installation:

```text
<workspace-python> <runtime>/source_acquire.py --workspace <workspace> --vod https://chzzk.naver.com/video/<video-number>
```

This is a single requested public VOD preparation, not a live detector or scheduler.
It contacts the anonymous public API/media endpoints, downloads an admitted video,
checks the media clock and runs local STT. It does not use cookies, a login profile,
a remote ASR service or another semantic author. Login/age/subscription-restricted
VODs fail; use user-supplied authorized local sources instead.

The default height is 480; `--height` accepts 144, 360, 480, 720 or 1080.
`--max-download-seconds` bounds the download phase, defaults to 21600, and accepts
60–21600. It is not an end-to-end STT timeout. Media also obeys `max_media_bytes`.
Files and checkpoint records stay in `<work_dir>/<video_no>/source-<attempt>/`.
A failed STT attempt can retain its verified media for retry; preserve previous
artifacts and inspect the recorded failure before rerunning.

Read the result and its `prepare_spec_path`. The generated template includes VOD
identity, the actual SRT/media paths, source directory and base. Add the real
current conversation `author` object, preserving all observed source values, then
pass that file to `loki.py prepare` below. The runtime binds its source version.
`author_required=true` means there is still an authoring job to do. Check the
transcription receipt and its actual model/device/F2 status; assets being ready
is not a successful transcription or a claim that you watched the source.

For an existing workspace-owned video that needs only local transcription:

```text
<workspace-python> <runtime>/transcribe_source.py --workspace <workspace> --video <owned-video> --output <new-sibling.srt>
```

The video must be under configured `<work_dir>/<video_no>/`; nested attempt
folders are allowed. The output is a new sibling `.srt`, not an overwrite.
Continue with the same source-bound preparation and actual original reads.

## Prepare the existing sources

Place the existing source-ready SRT and admitted same-VOD sidecars under
`<workspace>/<work_dir>/<video_no>/`. Preserve confirmed source timing metadata.
Do not synthesize timing authority or reuse a different broadcast's source files.
The initialized empty `base.md` can be the base for a new report. Read existing
material first if the user supplies a different base.

For visual work, add the same-VOD local `.mp4`, `.mkv`, `.webm` or `.mov` there or inside its source-attempt subdirectory.
The source directory basename must equal `video_no`; a local video from another
source or a network URL cannot establish this VOD's visual evidence. Use the
installer's media or default multimodal profile before preparing that job.

Create a JSON spec inside the workspace with the adapter's required fields:

```json
{
  "vod_info": {
    "video_no": "<confirmed-video-number>",
    "title": "<actual-title>",
    "channel_id": "<confirmed-channel-id>",
    "channel_name": "<actual-channel-name>",
    "duration": 3600,
    "publish_date": "<confirmed-broadcast-start>"
  },
  "work_root": "work",
  "source_work_dir": "work/<confirmed-video-number>",
  "srt_path": "work/<confirmed-video-number>/transcript.srt",
  "base_path": "base.md",
  "job_id": "<unique-owned-job-id>",
  "code_version": "<set-by-runtime>",
  "author": {
    "thread_id": "<actual-current-thread-id>",
    "turn_id": "<actual-current-turn-id>",
    "actual_model": "unknown"
  }
}
```

`duration` above is an example: replace it with the confirmed source duration in
seconds. `publish_date` is the historical broadcast-start field, not the replay
upload time. Record available real author identity; do not invent a model identity.
`actual_model=unknown` is valid when unverified. The runtime replaces `work_root`
with the configured work directory and binds `code_version` to the release source
commit. `job_id` uses letters, digits, `_`, `-`, `.` and begins with a letter/digit.

Optional `packet_path` and `orientation_paths` admit existing, same-source local
context. Optional `hierarchy`/`retrieval` use the bundled adapter's existing contract;
read that contract before using them. To attach the local video, add
`"media_path": "work/<confirmed-video-number>/source.mp4"` to the spec. The runtime
checks its separate media byte limit and binds the video hash to the prepared job.
The default multimodal installation supplies the verified local model runtime;
do not copy SDK or model paths from another host. Source acquisition is performed
before this spec; do not add downloader, provider or publisher settings to it. Source-read contracts returned by this exact job
take precedence over generic examples.

For broadcast-specific editorial preferences and known context, use the existing
`<source_work_dir>/<video_no>_context.md` memo input. Keep preferences distinct from
claims about what happened: source originals still decide scene meaning and time.
The job freezes this input; edit it before preparation and use the returned frozen
context when resuming. Selected local `timeline_comments.json`, clip/chat snapshots
and audio feature sidecars remain optional source evidence, with their own native
contracts. Do not manufacture an available sidecar to hide missing evidence.

```text
python <runtime>/loki.py --config <workspace>/loki.json doctor
python <runtime>/loki.py --config <workspace>/loki.json prepare --spec <workspace>/spec.json
```

Keep returned `job_root`, `prepared_revision`, `source_id`, `world_revision` and
`author_input_path`. Open `author_input_path` and read its source material,
`author_prompt`, `response_fields`, availability and source-reading contract.
Reopening identical preparation uses the existing immutable job. Changed sources
or author identity require explicit inspection rather than silently adopting it.

## Read originals and keep useful notes

Construct an `ew.tool.v1` action using the returned identities and the admitted
tool contract. Put action JSON inside the workspace and execute:

```text
python <runtime>/loki.py --config <workspace>/loki.json read --job-root <job-root> --expected-revision <revision> --action <workspace>/read-action.json
```

Browse/search can locate a scene. Follow returned original-read actions and read
the actual complete cues and necessary context before using them as evidence.
Keep read receipts unchanged. Prior writer decisions, summary hints, chat peaks
and navigation matches are neither proof nor a substitute for the author's reading.

For a useful source-linked hypothesis, use the `remember` note contract exposed
by the prepared job. `memory` recalls those notes; compare originals and later
corrections before adopting their meaning.

```text
python <runtime>/loki.py --config <workspace>/loki.json remember --job-root <job-root> --expected-revision <revision> --note <workspace>/note.json
python <runtime>/loki.py --config <workspace>/loki.json memory --job-root <job-root> --expected-revision <revision> --query <search-text>
```

## Find and inspect visual evidence

Use `read` with the same job and returned source/world identities for all source
tools below. Save the action JSON in the workspace and pass it through the
existing `read --action <path>` command. Example actions show only one request;
replace identities and intervals with this job's observed values.

With the default multimodal profile, `navigate_multimodal` accepts questions and
optional source windows. The local EmbeddingGemma 2 encoder ranks text/image/audio/video
material; it does not author the report or establish that the writer saw or heard it.

```json
{
  "schema_version": "ew.tool.v1",
  "source_id": "<returned-source-id>",
  "world_revision": "<returned-world-revision>",
  "question_id": "scene-question-1",
  "action": "navigate_multimodal",
  "args": {
    "questions": ["Which visible event explains the nearby subtitle?"],
    "intervals_sec": [[120, 130]]
  }
}
```

Each media window is 1–30 seconds; one request accepts at most three windows.
Questions and windows must follow the exact returned tool contract and source
duration. With no media windows, the tool can search text scope. Its results are
navigation candidates: read the cited complete originals and inspect useful
frames before adopting their meaning. Keep a specific unresolved limitation when
the local tool reports unavailable evidence.

For actual source pixels, request a bounded `scene` or `ocr` read:

```json
{
  "schema_version": "ew.tool.v1",
  "source_id": "<returned-source-id>",
  "world_revision": "<returned-world-revision>",
  "question_id": "scene-pixels-1",
  "action": "read_frames",
  "args": {"start_sec": 120, "duration_sec": 9, "inspection_mode": "scene"}
}
```

The local reader samples nine decode positions in a window of at most 120 seconds
and returns at most three images per page. Open and inspect the actual returned
local images with the current Codex's image viewer. Follow `next_cursor` with
`action: "read_frame_page"` and `args: {"cursor": "<returned-cursor>"}` as needed.
Preserve `frame_ref`, image hash, source time and the decode receipt. A decode
position is not independently verified original-frame PTS or evidence of listening.

When an allowed proposal/range in this job's response contract supports an image,
select one of the delivered frames:

```json
{
  "image": {
    "frame_ref": "<actual-returned-frame-ref>",
    "alt": "A concise description of the visible image",
    "caption": "A source-grounded image caption"
  }
}
```

This is a fragment of the response, not a replacement for its required fields.
The selection must lie inside its proposal's source span. Submission preserves
the exact delivered image bytes and time binding in report metadata. HTML displays
a WebP derivative of that selection with its original frame reference and SHA-256;
it does not substitute a different thumbnail. An image or embedding remains
supporting evidence alongside the originals the writer actually read.

## Author, submit and inspect

The current Codex writes fresh response JSON following `author-input.json` and
its bundled full-author prompt. Produce a coherent summary with evidence refs,
D1/D2 ranges, one native timestamp per Point, corresponding Stories, source-span
Highlights, outcomes and honest unresolved questions. Use only the shapes and
allowed evidence identifiers in this job's contract. Set no listening/viewing
claims without actual current delivery and inspection. A prior generated report
is context only and must not be relabeled as a new authored result.

```text
python <runtime>/loki.py --config <workspace>/loki.json submit --job-root <job-root> --expected-revision <revision> --response <workspace>/response.json
```

Inspect the returned Markdown/HTML/metadata paths and editorial continuity.
Correct an actual validation error in the response before resubmitting. An exact
completed submission is idempotent; a changed response conflicts after submission.
If interrupted, inspect existing submission state before any retry. Preserve raw
response and source-read evidence; do not open duplicate jobs to bypass a conflict.

Report the new draft location, what the sources establish and any material limits.
Submission creates a new local draft. Existing reports and edits remain unchanged;
public publishing is a separate action. Highlight proposals contain source spans
and may carry the selected inspected image; this bundle does not edit video.

## Review the local report in the browser

Use the installed workspace Python and runtime path:

```text
<workspace-python> <runtime>/review.py --config <workspace>/loki.json --port 8787
```

Open the printed `http://127.0.0.1:8787/` URL. The canonical report library links
to the existing video workspace and detailed report editor. Inspect the local
video/scene, edit the body or D1/D2, Point and Highlight fields, preview the draft,
then explicitly save it. Reopen after saving to confirm the stored revision.
Use the existing revision history to compare a prior saved version and restore
it through the normal explicit restore action. On a stale revision or failed save, keep the draft and inspect
the latest stored revision before retrying. Source SRT and selected images,
captions and evidence clocks are read-only in this editor. The original author
submission remains preserved; manual revision saving does not call another model
or publish externally. Markdown/HTML outputs remain in the workspace. External
publish, Cafe and comment-export connections are disabled with local-scope hints.
