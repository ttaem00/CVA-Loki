# 작업공간 설치와 설정

기본 설치는 **전체 기능(`multimodal`)과 GPU 자동 선택(`auto`)**이다.
입력·결과·환경·모델은 설치 스킬과 분리한 사용자 소유 작업공간에 둔다.

## 1. 한 명령으로 설치

```text
python <skill>/runtime/install.py --workspace <new-workspace> --mode setup
```

설치기는 설정을 초기화하고 전용 `.venv`를 만든 뒤 도구·의존성·고정 모델을 준비한다.
이어서 doctor와 합성 원문·보고서·미디어·모델 실행 검사를 수행하고
`installation-result.json`을 남긴다. 이후에는 이 결과가 반환한 Python과 runtime 경로를 쓴다.
실패하면 종료 코드와 최초 실패 단계를 확인한다. doctor 하나의 성공만으로 전체 설치가
끝났다고 판단하지 않는다.

| 옵션 | 설치 범위 |
| --- | --- |
| `--features multimodal` | 기본 전체 프로필. core/UI, Node/npm, FFmpeg/ffprobe, text/image/audio/video 임베딩, 공개 VOD 준비와 로컬 Whisper/Silero/F2 STT |
| `--features media` | core/UI, Node/npm과 로컬 프레임 추출. 임베딩 모델은 내려받지 않음 |
| `--features core` | 텍스트 원문 읽기·메모·응답 검사·보고서와 검토 UI |
| `--model-source <directory>` | 고정 revision의 EmbeddingGemma 2 파일을 명시적으로 제공. 13개 해시가 모두 일치해야 하며 Whisper/F2는 별도의 고정 자산을 준비 |
| `--device auto` | 지원되는 NVIDIA CUDA 또는 Apple Silicon MPS를 선택. 호환 GPU가 없으면 CPU 선택 |
| `--device cpu` | GPU가 있는 호스트를 포함해 CPU 실행을 명시적으로 선택 |

기본 프로필 대신 `media`·`core`를 사용하는 것은 사용자 요청 범위에 맞을 때만 선택한다.
설치 스킬이나 플러그인 캐시를 작업 폴더로 사용하지 않으며, 설치를 위해 호스트 인증
프로필을 검색·복사하거나 시스템 Python 환경을 설치 대상으로 삼지 않는다.

## 2. GPU·모델·필요 자원

감지된 GPU가 지원되지 않거나 실행이 실패하면 원인과 조치를 알린다. CPU 성공으로
숨기지 않는다. 호스트 드라이버와 Docker의 GPU 노출은 먼저 준비돼 있어야 하며,
설치기는 호스트 커널 드라이버를 설치하거나 바꾸지 않는다.

| 구성 | 장치와 준비 방식 |
| --- | --- |
| NVIDIA Linux/Windows | 장치에 맞는 고정 PyTorch 패키지와 공식 CUDA 12.8 wheel index 사용 |
| Apple Silicon | 네이티브 macOS MPS 실행을 실제로 확인해야 함. CUDA 결과가 MPS 성공을 뜻하지 않음 |
| Whisper·EmbeddingGemma 2 | 선택된 장치로 실행하고 실제 장치 결과 확인 |
| 익명 F2 화자 전환 | 별도의 Sherpa CPU 작업자 사용. 실명 화자 식별이 아님 |

F2는 활성화한 채 유지하고 VAD 사전 검사는 직렬로 수행한다. 로컬 STT에 원격 ASR이나
동적 Hub 모델을 대체 경로로 사용하지 않는다. 모델 파일은 작업공간 `.models/`에 둔다.

EmbeddingGemma 2 약 1.5GB, Whisper 약 1.62GB, F2 약 47MB 외에도 PyTorch·패키지·캐시·
원본 영상·디코드 결과의 저장공간이 필요하다. 임베딩 작업자는 가용 RAM 8GiB 이상을
확인하지만, 이것이 모든 작업의 메모리 사용량이나 설치 성공을 보장하지는 않는다.

준비된 전체 환경의 같은 고정 모델로 실행·캐시 검사를 다시 확인할 수 있다.

```text
<workspace-python> <runtime>/multimodal_setup.py --workspace <workspace> --device auto --verify
```

이 검사는 합성 text/image/audio/video 벡터와 캐시 재사용을 확인한다. 실제 방송 분석이나
현재 Codex의 원문 읽기를 대신하지 않는다. `device_selection`에서 요청·선택 장치,
backend/index, 감지 이유와 실제 실행 장치를 확인한다. 미디어 selftest는 별도 `media`
검증 설정을 사용하므로 설치 설정을 보존하며 임베딩 실행 성공을 대신하지 않는다.

## 3. 자동 준비되는 의존성

설치기는 Node/npm(`nodejs-wheel` 24.19.0), PATH 또는 전용 환경의 FFmpeg/ffprobe
(`static-ffmpeg` 3.0), torch 2.11.0·torchvision 0.26.0, transformers 5.19.0,
sentence-transformers 6.1.0, LanceDB 0.40.0·PyArrow 21.0.0, 음성 처리 의존성,
accelerate 1.15.0·Silero VAD 6.2.3·sherpa-onnx 1.13.7과 공개 자료 수집 의존성을 준비한다.
plugin 모드에서 기존 Codex CLI를 사용할 수 없으면 작업공간의 전용 npm 경로에 준비한다.
구성요소의 라이선스는 [제3자 고지](THIRD_PARTY_NOTICES.md)를 따른다.

네트워크가 필요한 설치·공개 자료 준비와 오프라인 작성 도구의 경계는 다음과 같다.

| 단계 | 네트워크 |
| --- | --- |
| 의존성·모델 설치 | 공개 패키지·모델 저장소에 접근할 수 있음 |
| 사용자가 요청한 공개 VOD 준비 | 해당 공개 API·미디어에 접근 |
| 로컬 STT·원문 읽기·작성 도구 | 로컬 자료·고정 모델 사용, 작성 runtime의 `network=deny` 유지 |

## 4. 수동 core 설정이 필요한 경우

아래는 텍스트 중심 core 환경만 준비하는 경로다. 기본 전체 설치와 구분한다.
플러그인 등록만으로 Python 의존성이나 모델 설치가 끝나는 것도 아니다.
Python 3.11 이상인 별도 프로젝트 환경이나 가상환경에서 실행한다.

```text
python -m pip install -r <skill>/runtime/requirements.txt
```

이 명령은 패키지 저장소에 접근할 수 있다. 미디어·공개 자료·STT 전체 의존성은 준비하지
않으므로 공개 VOD부터 처리할 때는 기본 설치기를 사용한다.

`python <skill>/runtime/loki.py init --directory <empty-workspace>`는 한 번만 실행한다.
비어 있는 폴더에 `loki.json`, `base.md`, `work/`를 만들며, 비어 있지 않으면 거부한다.
이후 명령의 `--config <workspace>/loki.json`은 **하위 명령 앞**에 둔다.

수동 `init`은 아래 core 설정을 만든다. 기본 설치기는 이후 `features`를 `multimodal`로
맞추고 검증된 모델 runtime 참조를 기록한다.

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

## 5. 설정 키

| 필드 | 의미와 범위 |
| --- | --- |
| `schema_version` | 고정 `loki.workspace.v1` |
| `language` | 작성 언어 `ko` 또는 `en`. 원자료를 기계 번역하는 옵션이 아님 |
| `max_input_bytes` | 기본 50,000,000바이트, 정수 1,024–200,000,000. 파일별 입력 한도이며 전체 디스크·RAM·토큰·실행 예산이 아님 |
| `max_media_bytes` | 기본 20,000,000,000바이트, 정수 1,024–100,000,000,000. 명시한 `media_path`에 적용하며 다른 원자료는 텍스트 파일 한도를 따름 |
| `features` | 설치 프로필과 맞는 `core`, `media`, `multimodal`. 값만 바꿔도 의존성이 설치되는 것은 아님 |
| `multimodal_runtime` | 설치기가 기록하는 `.models/multimodal-runtime.json`의 `{path, sha256}` 참조. core/media는 `null`. 관찰한 경로·해시를 보존 |
| `network` | 작성과 로컬 도구의 고정 `deny`. 설치·명시적으로 요청한 공개 VOD 준비는 별도 단계 |
| `author_mode` | 고정 `current_codex`. 다른 작성 모델·provider를 선택하지 않음 |
| `work_dir` | 작업공간 안의 상대 디렉터리. 절대 경로·탈출·링크 경로를 사용하지 않음 |

설정 키는 정확히 이 아홉 개다. 토큰·provider endpoint·브라우저 프로필·비공개 배포
설정을 추가하지 않는다. 지원하지 않는 키는 무시하지 않고 거부한다. doctor는 소스
manifest·의존성·기능별 준비 상태를 확인하며 편집 품질이나 호스트 권한을 보증하지 않는다.

작업별 `read_page_policy`·`search_mode`는 `loki.json`의 추가 키가 아니다.
[작성 절차](workflow.md)의 준비 spec에서 선택하고 재개 시 저장된 값을 유지한다.

## 6. 원자료 경로와 실행 기록

준비 spec의 상대 경로는 작업공간 기준이며, 절대 경로를 제공해도 그 안에 있어야 한다.
원자료는 `<work_dir>/<video_no>/` 아래에 두고 SRT는 `source_work_dir` 안에 둔다.
텍스트만 사용할 때는 VOD 안의 하위 자료 폴더를 `source_work_dir`로 지정할 수 있다.
`media_path`를 명시할 때는 마지막 폴더명이 해당 영상 번호인 `work/<video_no>/`를
`source_work_dir`로 지정하고, 영상·SRT는 그 안이나 소유한 준비 하위 폴더에 둔다.
같은 VOD의 `.mp4`, `.mkv`, `.webm`, `.mov` 로컬 영상만 연결한다.

자료와 base revision은 `prepare`·`read`·`submit`에서 함께 고정된다. 준비된 경로에는
절대 경로가 포함되므로 폴더 이동이 자동 재배치를 뜻하지 않는다. 이동·재시도 전에
전체 작업공간과 기록을 보존한다. 영상이 없으면 시청했다고 기록하지 않는다.

| 기록 | 확인할 내용 |
| --- | --- |
| `.loki-tools.json` | 기록된 미디어 실행 파일과 해시 |
| `multimodal_runtime` | 작업공간의 고정 임베딩 모델 runtime 참조 |
| `.loki-speech.json` | `.models/` 아래 고정 Whisper·Silero·F2 메타데이터의 별도 해시 참조 |
| 전사 기록 | 실제 출력 SRT·모델·장치·F2 상태. `transcribe_source.py`가 형제 기록 파일에 남김 |

`speech.verification=assets_only_not_transcription`은 자산 준비만 증명한다. 실제 STT 성공은
해당 자료를 처리한 실행과 전사 기록으로 확인한다. 원자료·모델·성공한 캐시는 재시도 때 보존한다.

프로세스 안의 실행 정책은 OS 보안 경계가 아니다. 현재 Codex에는 호스트의 권한과
서비스의 자료 처리 정책이 계속 적용된다. 원자료를 대화에 읽어 전달하면 그 Codex
서비스에도 전달되므로 로컬 파일이라는 이유로 서비스에 비공개라고 해석하지 않는다.
