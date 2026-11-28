---
name: loki
description: 치지직을 중심으로 공개 YouTube 라이브 다시보기·SOOP VOD 또는 같은 방송의 로컬 자료를 읽고, 선택한 작성자 호스트(Codex·Claude·Antigravity·Gemini CLI·Web AI·로컬 LLM)가 요약·타임라인·Point·Story·Highlight와 선택 이미지를 직접 작성한 뒤 검토·내보내기·저장한다.
---

# Loki로 직접 작성하기

선택한 전담 작성자(`current_<host>`)가 동봉 런타임으로 실제 원문과 이미지를 읽고 편집 판단을 내려
새 보고서를 작성한다. 다른 유료 API 모델·에이전트 브리지나 이전 생성 보고서에 의미 작성을 맡기지 않는다.
Whisper는 전사, EmbeddingGemma 2는 근거 탐색, 스크립트는 준비·기록·검사·렌더·내보내기를 맡는다.

호스트 모델 선택은 동적 호스트 라우팅을 따른다(멀티에이전트 호스트는 기본 `inherit`,
로컬 LLM은 `--model auto`). 특정 시점 모델 버전에 고정하지 않으며,
권장 능력 티어와 웹/로컬/Gemini CLI 가이드는 배포 루트의 `docs/llm-guides/`를 참고한다.

## 공통 작성 원칙

**목표와 수락 기준 및 탐사 → 이해 → 수립 → 연결**은 준비된 `author-input.json`의 `author_prompt.text`에서 확인한다. 전편 탐색부터 Point·Highlight 선택, Span 경계와 제목 대조까지 이 기준을 적용한다.

## 경로와 읽을 문서

이 `SKILL.md`를 기준으로 `runtime/loki.py`의 실제 경로를 구한다. 플러그인·스킬의
설치 위치와 관계없이 전체 `loki/` 폴더가 필요하다. 설치 파일은 읽기 전용으로 유지하고
입력·결과·모델은 분리한 사용자 소유 작업공간에 둔다. 인증 파일을 검색하지 않는다.

| 상황 | 읽을 문서 |
| --- | --- |
| 첫 사용·설치·설정 확인 | [설정 안내](references/configuration.md), [실행 정책](references/POLICY.md), [자료 처리 원칙](references/PRIVACY.md) |
| VOD·기존 자료로 보고서 작성 | [작성 절차](references/workflow.md), 준비 결과의 `author-input.json` |
| 보안 문제 보고·재배포 | [보안 안내](references/SECURITY.md), [제3자 고지](references/THIRD_PARTY_NOTICES.md), [이용허락](LICENSE) |
| 외부 코드·문서 기여 | [기여 안내와 동의 문안](CONTRIBUTING.md) |

배포·플러그인·스킬·요약 메커니즘은 3.0.9.2이며 `latest`도 Loki 3.0.9.2이다. 새 작업의 실제 버전을 확인하고, 이미 준비한
작업의 버전·원자료·응답·예산은 별칭 변경을 이유로 다시 쓰지 않는다.

### 3.0.9.2 실행 모드·다중 호스트·내보내기

- **실행 모드 (`--mode lite|normal|full`)**: `source_acquire.py`와 `prepare`는 `lite`(360p 빠른 취득·Warm-Only 벡터·15분 작성 목표), `normal`(480p·전체 채팅·공개 클립·댓글·30분), `full`(720p·전체 사전 수집·결정론적 5분 구간 힌트·60분) 모드를 지원한다.
- **준비 시간 vs 작성 시간 분리**: 다운로드·STT·임베딩 준비 시간(`prep_time_note`)은 사전 단계이며 작성 예산(`authoring_minutes`)에서 차감하지 않는다.
- **다중 작성자 호스트 (`--author-mode`)**: `current_codex`, `current_chatgpt`, `current_claude`, `current_antigravity`, `current_gemini_cli`, `current_gemini_web`, `current_claude_web`, `current_local_llm`을 지원한다.
- **무부작용 사전 검사 및 중복 읽기 방지**: 최종 제출 전 `loki.py submit --validate-only`로 스키마·근거·미반영 메모(`unreconciled_note_reads`)를 확인하고, `loki.py status --include-read-summary`로 이미 읽은 STT/채팅 구간을 조회한다.
- **웹 AI·로컬 LLM·크리에이터 내보내기**: `loki.py export-pack` / `import-draft`, `local_llm_author.py`(Ollama/vLLM 3-Pass `--model auto`), `loki.py export --format comment|markers-csv|fcpxml`을 오프라인(`network=deny`) 계약 안에서 사용할 수 있다.
- **선택적 짧은 영상 확인 (`gemini-api`)**: 움직임·음향 확인이 필요한 <=60초 구간만 `short_video_check.py`(`editorial_short_video_check`)로 보조 확인하며, 미설치·미인증·14MiB 초과 시 차단을 일으키지 않고 건너뛴다.

## 1. 설치와 자료 준비

설치가 필요하면 `python <skill>/runtime/install.py --workspace <new-user-workspace> --mode setup --author-mode <author-mode>`를
사용한다. 기본값은 전체 기능 `--features multimodal`과 GPU 자동 선택 `--device auto`다.
선택 가능한 작성자 호스트는 `current_codex`, `current_claude`, `current_antigravity`, `current_gemini_cli`, `current_chatgpt`, `current_gemini_web`, `current_claude_web`, `current_local_llm`이다. 스킬 또는 setup 모드는 별도 유료 CLI 작성자를 실행하지 않는다. 실제 셸·허용된 네트워크·저장공간·모델 자원을 먼저 확인한다.
Node/npm·FFmpeg·장치별 PyTorch·EmbeddingGemma 2·Whisper·Silero·익명 F2를 준비한다.
호환 GPU가 없으면 CPU를 선택하며, 감지된 GPU의 실행 실패를 CPU 성공으로 숨기지 않는다.
호스트 드라이버·Docker GPU 노출은 먼저 준비돼 있어야 한다. 설치로 이를 변경하지 않는다.

`installation-result.json`의 실제 Python·runtime 경로, doctor와 기능별 검사 결과를 확인한다.
`speech.verification=assets_only_not_transcription`은 모델 자산 준비이며 실제 전사 성공이 아니다.
합성 검사도 새 보고서 작성이나 방송 편집 품질의 증거가 아니다. 자세한 설치·F2·VAD·캐시
기준은 설정 안내를 따른다. `media`·`core`는 사용자 요청 범위에 맞을 때만 선택한다.

치지직 VOD 번호, 종료된 YouTube Live 다시보기·SOOP VOD의 단일 URL, 또는 같은 방송의 SRT·확인된 식별/시각 정보와 선택적 로컬
영상으로 시작한다. 공개 자료 취득은 별도의 온라인 준비 단계이며 로컬 STT와 작성 도구는
오프라인을 유지한다. 쿠키·로그인 프로필을 수집하지 않는다. 원자료 준비 결과는 작성 완료가 아니다.
온라인 준비는 `source_acquire.py --workspace <workspace> --vod <단일-다시보기-URL>`을 사용한다.
준비 결과의 플랫폼·원본 ID·재생 시계를 유지하며, 채팅의 미제공·일부 수집을 빈 채팅으로 단정하지 않는다.

## 2. 준비 → 원문·이미지 읽기 → 직접 작성 → 제출

새 보고서는 이 VOD만 맡는 전담 대화에서 작성한다. 코드 작업, 다른 VOD 또는 무관한 요청을
처리하던 대화라면 `prepare` 전에 새 전담 대화로 넘긴다. Codex 호스트가 자체적으로 제공하는
독립 에이전트를 사용할 때는 이전 대화 이력 없이 배정하고 그 에이전트가 직접 전담 작성자로 진행한다.
독립 에이전트 기능이 없으면 전담 대화에서 진행한다. 작성자는 기존 설치의 작성자 모드와 신원을
사용해 준비·원문 읽기·제출을 이어가고, 기존 작업 재개 시 원래 소유와 준비 개정을 확인한다.

[작성 절차](references/workflow.md)의 명령과 이 작업이 반환한 계약을 사용한다.

1. `prepare`가 반환한 작업 경로와 revision, 원자료 식별 값을 보존한다.
   `author-input.json`의 작성 프롬프트·방송 정보·응답 필드·읽기 계약을 읽는다.
2. 장면을 고르기 전에 기존 시간순 원문 탐색으로 방송 전편을 훑고, 조용한 중간·끝 구간에서 흐름이 달라지는 단서도 확인한다.
   `read`로 인용할 완전한 STT cue와 필요한 앞뒤 맥락을 읽고,
   원문 조각·페이지가 남으면 cursor를 따라 이어 읽는다. 검색·요약·피크·메모는 탐색 단서다.
3. 필요한 시각 근거는 `read_frames`/`read_frame_page`가 반환한 실제 로컬 이미지를 연다.
   경로·임베딩·이미지 모음의 위치 정보만으로 봤다고 기록하지 않는다. 선택한 프레임은
   작업의 `image` 계약에 맞는 `{frame_ref, alt, caption}`으로 연결한다.
4. 유용한 출처 연결 메모와 반대 근거를 대조하고, 원문의 순서와 독립적으로 완결된 장면을
   판단한다. 없는 음성·영상·맥락은 미확인으로 남긴다. 로컬 검색 모델은 판단을 대신하지 않는다.
5. 작성한 타임라인을 전편 탐색 결과와 대조한 뒤, 설정된 `ko`/`en` 언어로 새 응답 JSON을 직접 작성하고 정확한 준비 revision으로
   `submit`한다. 반환된 Markdown·HTML·메타데이터와 중요한 불확실성을 확인한다.

원본 응답·원문 읽기 기록·생성 결과를 보존한다. 중단되거나 응답이 바뀌면 먼저 기존 제출
상태를 확인한다. 충돌을 감추기 위해 중복 작업을 만들지 않는다. 제출은 새 로컬 초안을
만들며 기존 보고서 교체나 외부 공개를 뜻하지 않는다.

## 3. 검토·저장·복원

설치 결과의 `review.command`·`review.url` 또는 설치된 Python의
`runtime/review.py --config <workspace>/loki.json --port 8787`을 사용한다.
출력된 loopback URL은 해당 환경에 실제 브라우저 접근이 있을 때 연다. 동봉한 기존
Report Admin의 목록·영상 작업실·상세 편집기에서 확인한다.

수정안은 미리보기 → 명시적 저장 → 재열기로 확인하고, 필요하면 저장 이력을 비교·복원한다.
최초 제출·원본 SRT·선택 이미지와 근거 연결을 보존한다. 수동 편집은 다른 모델을 호출하지
않는다. 외부 게시·카페·내보내기 연결, 방송 종료 상시 감지·지속 스케줄러는 포함하지 않는다.

## 지켜야 할 실행·출처 조건

- 선택한 `author_mode` (`current_codex`, `current_chatgpt`, `current_claude`, `current_antigravity`, `current_gemini_cli`, `current_gemini_web`, `current_claude_web`, `current_local_llm`)와 `network=deny`를 유지한다. 작업을 끝내기 위해 provider를
  바꾸거나 API 키를 요청하거나 실행 정책을 우회하지 않는다.
- 실제로 전달받고 확인하지 않은 자료를 들었거나 봤다고 기록하지 않는다. 시각·원문의
  의미를 임의로 바꾸지 않는다. 도구 실행 기록은 검사 성공을 보여주며 편집 품질을 보증하지 않는다.
- 재배포·생성 HTML에는 동봉 TTaem CVA 로고, 읽을 수 있는 `ttaem.com` 및
  https://ttaem.com 링크, `CVA-Loki` 및 https://github.com/ttaem00/CVA-Loki 링크를 유지한다.
  로고가 표시되지 않아도 텍스트 출처는 남긴다. 제3자 이용 조건도 보존한다.
- 상업 이용·배포·정책 문의는 [ttaem00@naver.com](mailto:ttaem00@naver.com)이다.
  외부 기여 병합 전에는 실제 기여자·권리자의 명시적 동의와 기록을 확인한다.
  작성자 AI가 타인의 동의나 서명을 대신 만들지 않는다.
