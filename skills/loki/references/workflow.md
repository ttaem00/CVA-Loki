# 현재 대화의 보고서 작성 절차

현재 대화의 ChatGPT 또는 Codex가 원문과 실제 이미지를 읽고 보고서를 직접 작성한다.
Loki는 자료 준비·읽기 기록·검사·렌더를 맡는다. 단계는
**원자료 준비 → 작업 고정 → 원문·이미지 읽기 → 작성·제출 → 검토·저장**이다.

## 경로와 식별 값

| 표기 | 사용할 값 |
| --- | --- |
| `<workspace-python>` | 설치 결과 `python`에 기록된 전용 Python |
| `<runtime>` | 설치된 `loki.py`가 있는 실제 디렉터리 |
| `<workspace>` | 설치 폴더와 분리한 사용자 소유 작업공간 |
| `<job-root>`, `<revision>` | `prepare`가 반환한 `job_root`, `prepared_revision` |

`<...>`는 실제 관찰한 값으로 바꾼다. 예시의 `python`도 설치 후에는
`<workspace-python>`으로 실행한다. 공백이 있는 경로는 사용하는 셸에 맞게 인용한다.
플러그인 캐시와 스킬 폴더에는 입력·결과를 저장하지 않는다.
처음 설치하거나 설정을 확인할 때는 [설정 안내](configuration.md)를 읽는다.

## 1. 원자료 준비

### 공개 치지직 VOD로 시작

기본 전체 프로필 `multimodal`에는 공개 자료 수집, 로컬 Whisper large-v3-turbo,
Silero VAD와 익명 F2 화자 전환 처리가 포함된다.

```text
<workspace-python> <runtime>/source_acquire.py --workspace <workspace> --vod <video-number>
```

사용자가 지정한 공개 VOD 한 편을 내려받고 미디어 시계를 확인한 뒤 로컬 STT를 실행한다.
쿠키·로그인 프로필·원격 ASR을 사용하지 않는다. 로그인·연령·구독 제한으로 접근할 수
없으면 실패 원인을 남기고, 사용자가 제공한 이용 권한 있는 로컬 자료로 진행할 수 있다.
방송 종료 감지나 상시 수집 스케줄러는 포함하지 않는다.

| 옵션·제한 | 의미 |
| --- | --- |
| `--height` | 기본 480. 허용값 144, 360, 480, 720, 1080 |
| `--max-download-seconds` | 기본 21600초, 범위 60–21600초. 다운로드 단계의 제한이며 전체 STT 제한 시간이 아님 |
| `max_media_bytes` | 작업공간 설정의 미디어 입력 크기 제한 |

파일과 체크포인트는 `<work_dir>/<video_no>/source-<attempt>/`에 남는다.
STT가 실패해도 검증된 미디어와 이전 기록은 보존하고, 최초 실패를 확인한 뒤 재개한다.

결과의 `prepare_spec_path`를 연다. 템플릿의 VOD 식별·SRT·미디어·원자료 폴더·base 경로를 보존한다.
`current_chatgpt` 작업공간은 도구가 발급한 `author={host:chatgpt,session_id:<로컬 UUID>,actual_model:unknown,identity_basis:workspace_local}`를 그대로 사용한다. 이 UUID는 플랫폼 계정·대화 ID가 아니며 Codex 신원이 필요하지 않다. 재개할 때 새 ID를 만들거나 기존 작성자를 바꾸지 않는다.
기존 `current_codex` 작업공간은 확인한 `thread_id/turn_id/actual_model`을 넣는다. `author_required`는 추가 identity 입력이 필요한지 나타내며, 어느 값이든 현재 대화의 실제 내용 작성은 아직 남아 있다.
전사 기록의 실제 모델·장치·F2 결과를 확인한다. 모델 자산 준비나 전사 성공을 작성자의
청취·시청 기록으로 바꾸지 않는다.

### 기존 영상에서 전사만 준비

```text
<workspace-python> <runtime>/transcribe_source.py --workspace <workspace> --video <owned-video> --output <new-sibling.srt>
```

영상은 설정된 `<work_dir>/<video_no>/` 또는 그 안의 자료 준비 폴더에 있어야 한다.
출력은 기존 파일을 덮어쓰지 않는 새 형제 `.srt`다. 이후 같은 원자료의 작업을 준비하고
실제로 원문을 읽는다.

### 기존 SRT로 시작

SRT와 같은 VOD의 허용된 보조 자료를 `<workspace>/<work_dir>/<video_no>/` 아래에 둔다.
확인된 원본 시각 정보를 보존하며 다른 방송 자료나 임의의 시각을 섞지 않는다.
새 보고서에는 초기화된 빈 `base.md`를 사용할 수 있다. 사용자가 다른 base를 제공했다면
그 내용을 먼저 확인한다.

- **텍스트만 사용하는 작업:** `source_work_dir`는 해당 VOD 폴더 안의 하위 자료 폴더여도 된다.
  SRT는 지정한 `source_work_dir` 안에 있어야 한다.
- **`media_path`를 명시하는 작업:** `source_work_dir`는 마지막 폴더명이 `video_no`인
  `work/<video_no>/`로 지정한다. SRT와 같은 VOD의 `.mp4`, `.mkv`, `.webm`, `.mov`는
  그 안이나 `source-<attempt>/` 같은 하위 폴더에 둘 수 있다. 영상은 `media` 또는
  기본 `multimodal` 프로필에서 사용한다.

공개 VOD 준비 도구는 `source_work_dir`에 VOD 루트를 반환하고 실제 자료 경로에는
하위 준비 폴더를 포함한다. 반환된 경로를 임의로 상위·하위 폴더로 바꾸지 않는다.
다른 VOD의 영상이나 네트워크 URL은 이 작업의 시각 근거가 될 수 없다.

## 2. 원자료와 작성 작업 고정

작업공간 안에 다음 형식의 준비 spec을 만든다.

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

`duration`은 확인한 원본 길이(초), `publish_date`는 방송 시작 시각이며 다시보기 업로드
시각과 구분한다. 예시 값을 실제 정보로 바꾼다. 실제 작성자 식별 정보를 사용하고,
확인하지 못한 모델 이름은 만들지 않는다. `actual_model=unknown`은 허용된다.
런타임이 `work_root`를 설정된 작업 디렉터리로, `code_version`을 동봉 소스 버전으로
고정한다. `job_id`는 영문자·숫자로 시작하고 영문자·숫자·`_`·`-`·`.`을 사용한다.

| 선택 필드 | 사용 기준 |
| --- | --- |
| `media_path` | 같은 VOD의 로컬 영상. 예: `work/<confirmed-video-number>/source.mp4`. 해시와 별도 미디어 크기 제한을 검사 |
| `packet_path`, `orientation_paths` | 같은 원자료에 연결된 기존 로컬 맥락. 해당 계약을 확인한 뒤 사용 |
| `hierarchy`, `retrieval` | 동봉 adapter의 현재 계약을 읽고 선택. 다른 호스트의 SDK·모델 경로를 복사하지 않음 |
| `read_page_policy` | 새 작업의 기본값 `balanced_compact_v1`. 재개할 때는 저장된 정책 유지 |
| `search_mode` | 기본 `lexical`. `hybrid_dbsf_v1`은 명시적으로 선택한 작업에만 적용 |

이 필드는 `loki.json`에 추가하는 설정이 아니라 작업별 준비 spec이다. 허용값과 도구
형식은 이 작업이 반환한 실제 계약을 따른다. 검색 모드 선택을 영상·음성 검색이나 정렬
전반에 일괄 적용하지 않는다. 공개 자료 수집은 준비 spec 이전의 별도
단계이며 downloader·provider·publisher 설정을 spec에 추가하지 않는다.

방송별 편집 선호와 알려진 배경은 `<source_work_dir>/<video_no>_context.md`에 둔다.
선호와 실제 발생 사실은 구분한다. 입력은 작업 준비 시 고정되므로 먼저 수정하고,
재개할 때는 고정된 맥락을 사용한다. `timeline_comments.json`, 클립·채팅·오디오
sidecar는 각 계약에 맞는 선택 자료이며, 자료가 없다는 이유로 내용을 만들어 넣지 않는다.

```text
python <runtime>/loki.py --config <workspace>/loki.json doctor
python <runtime>/loki.py --config <workspace>/loki.json prepare --spec <workspace>/spec.json
```

다음 반환 값을 보존한다.

| 반환 값 | 용도 |
| --- | --- |
| `job_root`, `prepared_revision` | 이후 `read`·메모·`submit`의 작업과 revision |
| `source_id`, `world_revision` | 원자료 도구 요청의 출처 식별과 원자료 revision |
| `author_input_path` | 실제 작성 프롬프트·응답 필드·자료 가용성·읽기 계약이 담긴 `author-input.json` |

`author_input_path`를 읽고 이 작업의 계약으로 진행한다. 동일한 준비 입력을 다시 열면
기존의 고정된 작업을 사용한다. 자료나 작성자 정보가 바뀌었다면 변경을 확인하며,
기존 작업에 조용히 덮어쓰지 않는다.

## 3. 원문 읽기와 메모

반환된 식별 값과 도구 계약으로 `ew.tool.v1` action을 만들고 작업공간에 저장한다.

```text
python <runtime>/loki.py --config <workspace>/loki.json read --job-root <job-root> --expected-revision <revision> --action <workspace>/read-action.json
```

검색·탐색 결과는 읽을 위치를 찾는 단서다. 인용하거나 판단에 사용할 STT cue 전체와
필요한 앞뒤 맥락을 실제로 읽는다. 이전 작성자의 판단, 요약 힌트, 채팅 피크, 검색 순위는
원문을 대신하지 않는다. 읽기 기록과 원래 식별 값은 변경하지 않는다.

기본 compact 응답 `ew.owner_read.v1`은 원문을 요약하지 않고 반복 정보를 줄인다.
`record_columns`와 `record_rows`를 대응시키고 `record_defaults`를 합쳐 읽는다.
긴 원문이 나뉘면 같은 `record_id`의 `raw_char_start`·`raw_char_end`·`raw_char_total`을
따라 모든 조각을 확인한다. `next_cursor`가 남으면 같은 요청에 그 cursor를 넣어 계속
읽는다. 한 행이나 한 페이지를 완전한 cue로 가정하지 않는다. 재개 시 원래 작업의
읽기·검색 정책과 cursor를 유지하고, 정책 변경을 기존 작업에 일괄 적용하지 않는다.
자료 종류별 전송 순서를 사건 순서로 해석하지 않는다. 원래 시각과 locator를 대조해
방송의 흐름을 판단한다.

유용한 가설이나 판단은 작업이 노출한 `remember` 계약으로 출처에 연결해 남긴다.
`memory`는 그 메모를 불러온다. 메모를 사실로 채택하기 전에 원문과 후속 정정을 대조한다.

```text
python <runtime>/loki.py --config <workspace>/loki.json remember --job-root <job-root> --expected-revision <revision> --note <workspace>/note.json
python <runtime>/loki.py --config <workspace>/loki.json memory --job-root <job-root> --expected-revision <revision> --query <search-text>
```

## 4. 이미지와 멀티모달 근거 확인

아래 요청은 같은 작업의 `read --action <path>`로 실행한다. JSON은 요청 한 개의 예시이며,
식별 값·질문·구간은 실제 작업과 원본 길이에 맞게 바꾼다.

기본 전체 프로필의 `navigate_multimodal`은 로컬 EmbeddingGemma 2로
text/image/audio/video 후보를 찾는다. 검색 모델은 보고서를 작성하지 않으며,
검색 결과만으로 현재 Codex가 해당 장면을 보거나 들었다고 기록하지 않는다.

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

미디어 구간은 각각 1–30초이고 한 요청에 최대 세 구간을 받는다. 구간을 생략하면 텍스트
범위를 검색할 수 있다. 후보의 원문을 읽고 유용한 프레임을 확인한 뒤 의미를 판단한다.
도구가 자료 부재를 알리면 남은 한계를 구체적으로 보존한다.

실제 프레임은 `scene` 또는 `ocr` 모드의 제한된 구간으로 요청한다.

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

최대 120초 구간의 아홉 디코드 위치에서 추출하고 한 페이지에 최대 세 이미지를 반환한다.
현재 Codex의 이미지 뷰어로 **반환된 실제 로컬 이미지**를 연다. 필요하면 `next_cursor`를
`action: "read_frame_page"`, `args: {"cursor": "<returned-cursor>"}`로 이어 읽는다.
`frame_ref`·이미지 해시·원본 시각·디코드 기록을 보존한다. 디코드 위치만으로 원본 프레임의
정확한 PTS가 별도로 검증됐다거나 음성을 들었다고 판단하지 않는다.

해당 응답 계약에서 이미지를 허용한 proposal/range에는 확인한 프레임을 선택한다.

```json
{
  "image": {
    "frame_ref": "<actual-returned-frame-ref>",
    "alt": "A concise description of the visible image",
    "caption": "A source-grounded image caption"
  }
}
```

이 예시는 응답의 일부다. 나머지 필수 필드도 실제 계약을 따른다. 선택 프레임은 해당
proposal의 원본 구간 안에 있어야 한다. 제출 시 전달받은 이미지 바이트·시각을 메타데이터에
보존하고, HTML에는 원본 `frame_ref`·SHA-256에 연결된 WebP를 표시한다. 다른 썸네일로
대체하지 않는다. 이미지·임베딩은 작성자가 읽은 원문과 함께 사용하는 보조 근거다.

## 5. 작성·제출·결과 확인

현재 Codex가 `author-input.json`과 동봉 전체 작성 프롬프트에 맞춰 새 JSON 응답을 쓴다.
요약·근거 참조, D1/D2 구간, Point별 원본 시각, 대응하는 Story, 원본 구간을 가진 Highlight,
결과와 미확인 사항을 일관되게 작성한다. 허용된 응답 형식과 근거 ID만 사용한다.
실제로 전달받고 확인하지 않은 음성·영상에 청취·시청 주장을 넣지 않는다. 기존 생성 보고서는
참고 맥락이며 새로 작성한 결과로 바꿔 기록하지 않는다.

```text
python <runtime>/loki.py --config <workspace>/loki.json submit --job-root <job-root> --expected-revision <revision> --response <workspace>/response.json
```

반환된 Markdown·HTML·메타데이터와 내용의 연결을 확인한다. 응답 검사가 실패하면 실제
오류를 고친 뒤 다시 제출한다. 완료된 동일 제출은 같은 결과를 반환하지만, 제출 뒤 바뀐
응답은 충돌한다. 중단되면 기존 제출 상태부터 확인하고, 충돌을 감추려고 중복 작업을
만들지 않는다. 원본 응답·원문 읽기 기록·생성 결과는 보존한다.

사용자에게 새 초안 위치, 원문이 뒷받침하는 내용과 중요한 한계를 알린다. 제출은 새 로컬
초안을 만들며 기존 보고서나 수동 수정본을 교체하지 않는다. Highlight는 원본 구간과
선택 이미지를 포함할 수 있지만 이 도구가 영상 컷 편집을 수행하는 것은 아니다.

## 6. 브라우저에서 검토·저장

```text
<workspace-python> <runtime>/review.py --config <workspace>/loki.json --port 8787
```

출력된 `http://127.0.0.1:8787/`을 연다. 목록에서 기존 영상 작업실과 상세 편집기로 이동한다.

1. 로컬 영상·장면과 보고서를 확인한다.
2. 본문·D1/D2·Point·Highlight를 수정하고 미리보기를 확인한다.
3. 명시적으로 저장한 뒤 다시 열어 저장된 revision을 확인한다.
4. 필요하면 기존 저장 이력을 비교하고 명시적 복원 동작을 사용한다.

오래된 revision이나 저장 오류가 나면 편집안을 보존하고 최신 저장 상태를 확인한 뒤
재시도한다. 원본 SRT, 선택 이미지·캡션과 근거 시계는 이 편집기에서 읽기 전용이다.
최초 작성 제출은 보존하며 수동 저장은 다른 모델을 호출하거나 외부에 게시하지 않는다.
Markdown·HTML은 작업공간에 남는다. 외부 게시·카페·댓글 내보내기 연결은 비활성화돼 있다.
