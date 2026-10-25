# Workspace configuration

For automatic setup and a verified installation receipt, run one command:

```text
python <skill>/runtime/install.py --workspace <new-workspace> --mode setup
```

The default `--features multimodal` initializes configuration before creating its
owned `.venv`, installs media, public-source, speech and device-matched embedding
dependencies, fetches the pinned public models into workspace `.models/`, requires doctor `ready`, and runs the
packaged source/report, media and model-execution checks. Use the returned
interpreter and runtime paths. A failed installation
exits nonzero with the exact stage; an exit code from doctor alone is insufficient.

| Option | Scope |
| --- | --- |
| `--features multimodal` | Default full profile: core/UI, Node/npm, FFmpeg/ffprobe, local text/image/audio/video embeddings, public VOD preparation and local Whisper/Silero/F2 STT. |
| `--features media` | Core, Node/npm and local video frame extraction. No embedding model download. |
| `--features core` | Original text reads, notes, response validation and reports. |
| `--model-source <directory>` | Use explicitly supplied EmbeddingGemma 2 files from the pinned revision. All 13 hashes must match. Whisper/F2 still use their separate pinned downloads. |
| `--device auto` | Default: supported NVIDIA CUDA or Apple Silicon MPS; CPU when no compatible GPU is detected. Detected unsupported or broken GPU setups fail with a reason. |
| `--device cpu` | Explicit CPU execution, including on GPU-equipped hosts. |

Default installation selects supported NVIDIA CUDA or Apple Silicon MPS and
matching PyTorch packages; without compatible GPU detection it selects CPU.
NVIDIA Linux/Windows uses the pinned official CUDA 12.8 wheel index. Whisper and the embedding encoder use the selected device; existing F2 diarization
uses its separate Sherpa CPU worker. MPS requires actual macOS runtime verification;
a CUDA proof does not establish MPS readiness. The host driver and Docker GPU exposure must
already work. The installer does not install or replace host kernel drivers.
EmbeddingGemma 2 is about 1.5GB, Whisper weights about 1.62GB, and F2 assets
about 47MB. PyTorch, downloaded packages, caches, original video and decoded media
need additional disk. The existing embedding worker checks for at least 8GiB available RAM before
work. That check is not a guarantee that installation or every workload will fit.
Use a smaller feature profile when media/model processing is unnecessary.

In an already prepared multimodal environment, the model installer can repeat its
execution and cache checks with the same pinned files:

```text
<workspace-python> <runtime>/multimodal_setup.py --workspace <workspace> --device auto --verify
```

This checks synthetic four-modality vectors and cache reuse; it does not evaluate
the user's broadcast or replace the current Codex author's original reads.
Check the returned actual device and execution result. The media selftest uses
a separate media-only verification configuration to avoid model inference;
it preserves the installed configuration and does not certify multimodal execution.
The installation receipt's `device_selection` records requested and selected
devices, backend/index, detection reason, and actual device after verification.

The installer provides Node/npm through `nodejs-wheel` 24.19.0, FFmpeg/ffprobe
from PATH or venv-local `static-ffmpeg` 3.0, torch 2.11.0 and torchvision 0.26.0,
transformers 5.19.0, sentence-transformers 6.1.0, LanceDB 0.40.0, PyArrow 21.0.0
and the supplied audio requirements, accelerate 1.15.0, Silero VAD 6.2.3,
sherpa-onnx 1.13.7 and public-source dependencies. Plugin mode prepares a workspace-local npm
Codex CLI when an existing CLI is unavailable. It does not search or copy host
authentication profiles. See the root third-party notices for distribution licenses.

The following manual steps prepare the core text environment only. Native plugin
registration does not by itself install these dependencies or the embedding model.

Select a Python 3.11+ project environment or a separate virtual environment:

```text
python -m pip install -r <skill>/runtime/requirements.txt
```

Dependency installation can contact package
repositories; runtime `network=deny` applies to authoring, not this setup command.
These manual core-only steps do not install the full media, source or STT
dependencies. Use the default installer for public VOD preparation.

Run `python <skill>/runtime/loki.py init --directory <empty-workspace>` once.
Initialization creates `loki.json`, `base.md` and `work/`. It refuses a nonempty
directory. Subsequent commands use `--config <workspace>/loki.json` **before** the
subcommand. Do not store working material in the installed plugin/skill folder.

The manual `init` command creates this core configuration. The default installer
then sets `features` to `multimodal` and records its verified model runtime ref.

```json
{
  "schema_version": "loki.workspace.v1",
  "language": "ko",
  "max_input_bytes": 50000000,
  "max_media_bytes": 20000000000,
  "features": "core",
  "multimodal_runtime": null,
  "network": "deny",
  "author_mode": "current_codex",
  "work_dir": "work"
}
```

| Field | Meaning |
| --- | --- |
| `schema_version` | Fixed `loki.workspace.v1`. |
| `language` | Writer output language: `ko` or `en`. This is not machine translation of the sources. |
| `max_input_bytes` | Default 50,000,000 bytes; configurable integer 1,024–200,000,000. A per-file admission limit, not an overall disk, memory, token or execution budget. |
| `max_media_bytes` | Default 20,000,000,000 bytes; configurable integer 1,024–100,000,000,000. Applies to the explicit `media_path`; other source files retain the text-file limit. |
| `features` | `core`, `media` or `multimodal`, matching the installation profile. Changing this field alone does not install missing dependencies. |
| `multimodal_runtime` | Installer-owned `{path, sha256}` reference to `.models/multimodal-runtime.json`, or `null` for core/media. Keep the observed path and hash; do not fabricate a manifest. |
| `network` | Fixed `deny` for authoring and its local tools. Installation and explicitly requested public VOD acquisition are separate online stages. |
| `author_mode` | Fixed `current_codex`; no alternate model/provider configuration. |
| `work_dir` | Relative directory within this workspace. No absolute path, escape or linked path. |

Keep exactly these configuration fields and no additional keys. Do not put tokens, provider endpoints,
browser profile paths or private deployment configuration here. The tool rejects
unsupported fields instead of silently applying them. `doctor` checks the source
manifest, local dependencies and selected feature readiness; it does not test report
quality or grant host permissions.

Prepare spec paths are relative to the workspace when not absolute. They must
resolve inside it. Put VOD originals below `<work_dir>/<video_no>/`; the SRT must
be inside `source_work_dir`. Prepare, read and submit use the same fixed originals
and base revision. Retain the full workspace if moving it; absolute prepared
paths mean moving a settled job is not an automatic relocation workflow.

For images or multimodal media, use a source folder whose basename is the
confirmed video number, such as `work/12345/`, and put the same-VOD local video
there or below an owned source-attempt child. Set `media_path` in the prepare spec to this `.mp4`, `.mkv`, `.webm` or
`.mov` file. Media is optional; missing media is not evidence that the writer
watched the broadcast. The installer records media executables and hashes in
`.loki-tools.json`. Preserve that receipt and the model files across retries. Default full setup also
writes `.loki-speech.json`, a hash-bound reference to pinned Whisper/Silero/F2
metadata under `.models/`; it is separate from `multimodal_runtime`.
`speech.verification=assets_only_not_transcription` is asset readiness, not actual
STT success. `transcribe_source.py` records actual transcription output/model/device
and the F2 metadata status in a sibling receipt.

Policy enforcement is local to this runtime process. The current Codex still
follows its host's permissions and data handling. Reading sources into the
conversation does not make them private from that Codex service.
