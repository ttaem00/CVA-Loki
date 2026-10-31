---
name: loki
description: 공개 치지직 다시보기 번호나 같은 방송의 로컬 자료를 읽고, 현재 대화의 ChatGPT 또는 Codex가 요약·타임라인·Point·Story·Highlight와 선택 이미지를 직접 작성한 뒤 검토·저장한다.
---

# Loki로 직접 작성하기

현재 대화의 ChatGPT 또는 Codex가 동봉 런타임으로 실제 원문과 이미지를 읽고 편집 판단을 내려
새 보고서를 작성한다. 다른 Codex/Claude CLI·API 모델·에이전트 브리지나 이전 생성
보고서에 의미 작성을 맡기지 않는다. Whisper는 전사, EmbeddingGemma 2는 근거 탐색,
스크립트는 준비·기록·검사·렌더를 맡는다.

[출시 버전 정책](https://github.com/ttaem00/CVA-Loki#release-version-policy)에 따라 정식 공개 전 오류·재빌드는 현재 후보 버전 안에서 해결하며, 공개한 태그·ZIP은 보존한다.

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

배포·플러그인·스킬·요약 메커니즘은 3.0.6이며 `latest`도 Loki 3.0.6이다. 새 작업의 실제 버전을 확인하고, 이미 준비한
작업의 버전·원자료·응답·예산은 별칭 변경을 이유로 다시 쓰지 않는다.

## 1. 설치와 자료 준비

설치가 필요하면 `python <skill>/runtime/install.py --workspace <new-user-workspace> --mode setup`을
사용한다. 기본값은 전체 기능 `--features multimodal`과 GPU 자동 선택 `--device auto`다.
ChatGPT 자체 Linux에서는 설치 명령에 `--author-mode current_chatgpt`를 명시한다. 스킬 또는 setup 모드는 별도 Codex 작성자를 실행하지 않는다. 실제 셸·허용된 네트워크·저장공간·모델 자원을 먼저 확인한다.
Node/npm·FFmpeg·장치별 PyTorch·EmbeddingGemma 2·Whisper·Silero·익명 F2를 준비한다.
호환 GPU가 없으면 CPU를 선택하며, 감지된 GPU의 실행 실패를 CPU 성공으로 숨기지 않는다.
호스트 드라이버·Docker GPU 노출은 먼저 준비돼 있어야 한다. 설치로 이를 변경하지 않는다.

`installation-result.json`의 실제 Python·runtime 경로, doctor와 기능별 검사 결과를 확인한다.
`speech.verification=assets_only_not_transcription`은 모델 자산 준비이며 실제 전사 성공이 아니다.
합성 검사도 새 보고서 작성이나 방송 편집 품질의 증거가 아니다. 자세한 설치·F2·VAD·캐시
기준은 설정 안내를 따른다. `media`·`core`는 사용자 요청 범위에 맞을 때만 선택한다.

공개 치지직 VOD 번호 또는 URL 한 개, 같은 방송의 SRT·확인된 식별/시각 정보와 선택적 로컬
영상으로 시작한다. 공개 자료 취득은 별도의 온라인 준비 단계이며 로컬 STT와 작성 도구는
오프라인을 유지한다. 쿠키·로그인 프로필을 수집하지 않는다. 원자료 준비 결과는 작성 완료가 아니다.

## 2. 준비 → 원문·이미지 읽기 → 직접 작성 → 제출

[작성 절차](references/workflow.md)의 명령과 이 작업이 반환한 계약을 사용한다.

1. `prepare`가 반환한 작업 경로와 revision, 원자료 식별 값을 보존한다.
   `author-input.json`의 작성 프롬프트·방송 정보·응답 필드·읽기 계약을 읽는다.
2. `read`로 실제 원자료를 확인한다. 인용할 완전한 STT cue와 필요한 앞뒤 맥락을 읽고,
   원문 조각·페이지가 남으면 cursor를 따라 이어 읽는다. 검색·요약·피크·메모는 탐색 단서다.
3. 필요한 시각 근거는 `read_frames`/`read_frame_page`가 반환한 실제 로컬 이미지를 연다.
   경로·임베딩·이미지 모음의 위치 정보만으로 봤다고 기록하지 않는다. 선택한 프레임은
   작업의 `image` 계약에 맞는 `{frame_ref, alt, caption}`으로 연결한다.
4. 유용한 출처 연결 메모와 반대 근거를 대조하고, 원문의 순서와 독립적으로 완결된 장면을
   판단한다. 없는 음성·영상·맥락은 미확인으로 남긴다. 로컬 검색 모델은 판단을 대신하지 않는다.
5. 설정된 `ko`/`en` 언어로 새 응답 JSON을 직접 작성하고 정확한 준비 revision으로
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

- 선택한 `author_mode=current_codex` 또는 `current_chatgpt`와 `network=deny`를 유지한다. 작업을 끝내기 위해 provider를
  바꾸거나 API 키를 요청하거나 실행 정책을 우회하지 않는다.
- 실제로 전달받고 확인하지 않은 자료를 들었거나 봤다고 기록하지 않는다. 시각·원문의
  의미를 임의로 바꾸지 않는다. 도구 실행 기록은 검사 성공을 보여주며 편집 품질을 보증하지 않는다.
- 재배포·생성 HTML에는 동봉 TTaem CVA 로고, 읽을 수 있는 `ttaem.com` 및
  https://ttaem.com 링크, `CVA-Loki` 및 https://github.com/ttaem00/CVA-Loki 링크를 유지한다.
  로고가 표시되지 않아도 텍스트 출처는 남긴다. 제3자 이용 조건도 보존한다.
- 상업 이용·배포·정책 문의는 [ttaem00@naver.com](mailto:ttaem00@naver.com)이다.
  외부 기여 병합 전에는 실제 기여자·권리자의 명시적 동의와 기록을 확인한다.
  현재 Codex가 타인의 동의나 서명을 대신 만들지 않는다.
