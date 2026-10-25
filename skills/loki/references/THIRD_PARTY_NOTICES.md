# 출처와 제3자 고지

CVA-Loki는 CHZZ VOD Analyzer의 현재 대화 작성 경로를 분리한 배포본입니다.
[공개 소스](https://github.com/ttaem00/CVA-Loki)와 동봉한 `LICENSE`는 GPL version 3입니다.
변경·추출 범위와 원본 SHA-256은 `../runtime/source-recipe.json` 및
`../runtime/source-manifest.json`에 기록합니다. 분리용 overlay와 로컬
진입점은 독립 실행을 위한 변경입니다.

번들 HTML 자산에는 Chart.js 4.4.2 배포물이 포함됩니다.
[Chart.js](https://github.com/chartjs/Chart.js/tree/v4.4.2)는 MIT 라이선스이며,
해당 파일의 원래 저작권·라이선스 헤더와
[MIT 전문](Chart.js.LICENSE.txt)을 보존합니다. 이 배포물 안의
`@kurkle/color` 0.3.2도 Jukka Kurkela의 MIT 저작권 헤더와
[MIT 전문](kurkle-color.LICENSE.txt)을 유지합니다. `pipeline/assets/`의
TTaempad 댓글 형식 파서는 CHZZ VOD Analyzer에서 가져온 소스 사본이며 원본 헤더를 유지합니다.

검토 UI는 같은 GPL-3.0 소스의 기존 `home.html`, `report_workspace.html`,
`report.html`과 참조 CSS/JS·타임라인·scrubber·상태·이력 모듈을 재사용합니다.
로컬 API·MP4 재생·revision ID·기능 범위 연결에 필요한 asset overlay의 원본과
배포 SHA-256도 manifest에 기록합니다. UI의 [hls.js](https://github.com/video-dev/hls.js)는
Apache-2.0이며 동봉한 원본 고지
[`hls.LICENSE.txt`](../runtime/scripts/admin/static/vendor/hls.LICENSE.txt)를 보존합니다.
외부 Google Fonts 리소스 요청은 제거하고 기존 시스템 글꼴 fallback을 사용합니다.

Evidence World의 `_pageindex/markdown.py`는 VectifyAI/PageIndex의 순수 Markdown
함수 일부를 고정 revision `6d23caf416858f2ca136840305d1f479a86f6ef7`에서 가져온 MIT
소스입니다. `../runtime/pipeline/evidence_world/_pageindex/`에 원본
`LICENSE`, `NOTICE.md`, `upstream.json`을 함께 보존합니다. PageIndex SDK의 모델·인증
기능은 설치하지 않습니다.

기본 `multimodal` 설치는 아래 공개 구성요소를 작업공간의 전용 환경에 준비합니다.
모델 가중치와 아래 도구의 바이너리는 Loki 배포 ZIP에 포함하지 않습니다. 설치된
패키지·모델을 다시 배포할 때는 해당 배포물의 라이선스와 제3자 고지도 보존하세요.

| 구성요소 | 출처와 고지 |
| --- | --- |
| EmbeddingGemma 2 | Google의 [`google/embeddinggemma-2`](https://huggingface.co/google/embeddinggemma-2), Apache-2.0. revision `914f7f89142e33e77833254d9c9b90c3cef7303b`의 필요한 13개 파일을 SHA-256으로 고정합니다. 가중치는 설치 시 `.models/`에 내려받거나 명시한 모델 폴더에서 복사합니다. |
| Whisper large-v3-turbo | OpenAI의 [고정 모델 카드](https://huggingface.co/openai/whisper-large-v3-turbo/blob/41f01f3fe87f28c78e2fbf8b568835947dd65ed9/README.md)는 MIT를 명시합니다. revision `41f01f3fe87f28c78e2fbf8b568835947dd65ed9`의 모델·processor 파일 11개를 SHA-256으로 고정하고 설치 시 작업공간에 준비합니다. |
| Silero VAD | [`silero-vad` 6.2.3](https://github.com/snakers4/silero-vad/tree/v6.2.3)은 [Silero Team의 MIT 라이선스](https://github.com/snakers4/silero-vad/blob/v6.2.3/LICENSE)를 따릅니다. 해시를 고정한 plain wheel의 번들 JIT를 사용하며 실행 중 Hub 소스를 내려받지 않습니다. |
| Sherpa F2 음성 자산 | [`sherpa-onnx` 1.13.7](https://github.com/k2-fsa/sherpa-onnx)은 Apache-2.0입니다. [공식 모델 안내](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html)의 pyannote segmentation 3.0과 TitaNet Small ONNX 자산을 공개 release에서 SHA-256으로 고정해 준비합니다. [pyannote 모델 카드](https://huggingface.co/pyannote/segmentation-3.0)는 MIT를, [NVIDIA TitaNet Small 원배포처](https://catalog.ngc.nvidia.com/orgs/nvidia/nemo/models/titanet_small/-)는 NeMo Toolkit의 라이선스 적용을 안내합니다. 라이브러리 라이선스를 모든 모델의 동일 라이선스로 해석하지 말고 각 모델의 원본 고지를 유지하세요. |
| Accelerate·공개 소스 처리 | [Accelerate 1.15.0](https://github.com/huggingface/accelerate/tree/v1.15.0)은 Apache-2.0입니다. 공개 VOD 준비의 PyCryptodomex 등 의존성은 requirements에 고정하며, [PyCryptodome 라이선스](https://www.pycryptodome.org/src/license)와 각 패키지의 고지를 따릅니다. |
| Node.js/npm | [`nodejs-wheel` 24.19.0](https://pypi.org/project/nodejs-wheel/24.19.0/)은 Node.js를 PyPI wheel로 배포하는 비공식 프로젝트이며 MIT입니다. [Node.js의 라이선스와 포함된 제3자 고지](https://github.com/nodejs/node/blob/v24.19.0/LICENSE)도 적용됩니다. |
| FFmpeg/ffprobe | Docker는 OS 패키지를 사용하고 일반 설치는 기존 도구 또는 [`static-ffmpeg` 3.0](https://pypi.org/project/static-ffmpeg/3.0/)을 사용합니다. Python wrapper는 [MIT](https://github.com/zackees/static_ffmpeg/blob/main/LICENSE)이며, 내려받는 바이너리는 [별도 배포처](https://github.com/zackees/ffmpeg_bins)와 [FFmpeg의 빌드별 LGPL/GPL 조건](https://ffmpeg.org/legal.html)을 따릅니다. wrapper의 MIT를 모든 codec·바이너리의 라이선스로 해석하지 마세요. |
| PyTorch/torchvision | torch 2.11.0·torchvision 0.26.0을 선택한 CPU/CUDA 장치에 맞게 설치합니다. [PyTorch](https://github.com/pytorch/pytorch/blob/v2.11.0/LICENSE)와 [torchvision](https://github.com/pytorch/vision/blob/v0.26.0/LICENSE)의 원본 라이선스·제3자 고지를 따릅니다. CUDA용 PyTorch와 함께 설치하는 NVIDIA 라이브러리에는 [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html)와 해당 배포물의 고지가 별도로 적용됩니다. PyTorch나 Python wrapper의 라이선스가 NVIDIA 라이브러리·호스트 드라이버의 조건을 대신하지 않습니다. |
| 모델 SDK | [Transformers 5.19.0](https://github.com/huggingface/transformers/tree/v5.19.0)와 [sentence-transformers 6.1.0](https://github.com/huggingface/sentence-transformers/tree/v6.1.0)을 사용하며 각 프로젝트의 Apache-2.0 고지를 따릅니다. |
| 벡터·오디오·기본 처리 | [LanceDB](https://github.com/lancedb/lancedb), [Apache Arrow/PyArrow](https://arrow.apache.org/), soundfile·torchcodec·librosa·soxr 및 numpy·requests·Beautiful Soup·Pillow를 사용합니다. 설치 버전은 requirements와 설치 결과에 기록하며 각 패키지의 라이선스·의존 바이너리 고지가 적용됩니다. |

plugin 모드에서 필요한 경우 npm으로 설치하는 [OpenAI Codex CLI](https://github.com/openai/codex)는
설치·조회용 도구입니다. 해당 패키지의 라이선스와 제3자 고지를 유지하세요.
현재 Codex 대화 서비스의 이용 조건은 로컬 소스 라이선스와 별개입니다.

방송 영상·자막·채팅·닉네임·썸네일과 사용자 결과에 대한 권리는 이 코드 라이선스가
부여하지 않습니다. 배포본에는 실제 방송 원자료나 사용자의 결과물을 포함하지 않습니다.
