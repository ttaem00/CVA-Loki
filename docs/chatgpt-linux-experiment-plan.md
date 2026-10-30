# ChatGPT 자체 환경에서 Loki를 사용하는 실험 계획

이 문서는 실행 결과가 아니라 조건부 실험 계획이다. GitHub 연결만으로 Linux 실행, VOD 다운로드, GPU 또는 하위 에이전트가 준비됐다고 가정하지 않는다. 실제 배포 파일과 버전은 [CVA-Loki 공개 저장소](https://github.com/ttaem00/CVA-Loki)의 게시된 릴리스에서 확인한다. 확인한 태그·ZIP SHA와 현재 대화의 도구를 기록한 뒤 시작한다.

목표는 별도 Codex 작성자나 모델 API를 호출하지 않고 현재 ChatGPT가 원자료를 읽어 요약·D1/D2·Point·Story·Highlight를 작성하는 것이다. Loki의 현재 ChatGPT 호환성이나 사용량 분리가 검증됐다는 뜻은 아니다. 설치·다운로드·전사·분석·최종 작성·개선 제안을 구분한다.

## 먼저 구분할 세 가지 환경

| 환경 | 확인된 역할 | 이 계획에서의 조건 |
|---|---|---|
| ChatGPT의 GitHub 연결 | 권한이 있는 코드·README·문서를 필요할 때 검색하고 읽는다. | 연결됐다는 사실만으로 저장소가 Python 파일시스템에 clone되거나 셸·패키지 설치·외부 네트워크가 제공되지는 않는다. |
| 일반 ChatGPT의 데이터 분석 | 대화에 제공된 파일을 stateful Python/Jupyter 환경에서 분석할 수 있다. 공식 안내상 외부 웹 요청과 API 호출은 할 수 없다. | 원자료 ZIP을 첨부하는 경로를 우선한다. VOD 번호만 주어 다운로드·모델 설치까지 완료한다고 약속하지 않는다. |
| ChatGPT Work의 Cloud 실행 | 제공되는 code/shell 도구와 허용된 네트워크로 여러 단계를 수행할 수 있다. | 실제 도구·계정·workspace 권한과 네트워크 설정을 확인한다. 사용자의 PC 파일·브라우저 로그인·설치 도구를 자동으로 상속하지 않는다. |

근거: [GitHub 연결](https://help.openai.com/en/articles/11145903-connecting-github-to-chatgpt), [데이터 분석](https://help.openai.com/en/articles/8437071-data-analysis-with-chatgpt), [Work 실행과 네트워크](https://learn.chatgpt.com/docs/enterprise/chatgpt-work-overview). 공식 안내 확인일은 2026-10-31이며 계정별 실제 도구가 우선한다.

## 사용량의 의미

`Codex CLI나 외부 모델 API를 작성자로 실행하지 않는다`와 `Codex에 쓸 할당량을 전혀 소모하지 않는다`는 다른 요구다. 일부 플랜에서는 Work·Codex 등이 같은 agentic allowance와 credit pool을 사용한다. 현재 ChatGPT가 직접 판단하는 작업도 해당 ChatGPT 기능의 사용량을 쓴다. 별도 API 키를 사용하지 않았다는 이유로 무료라고 보고하지 않는다. 일반 Chat의 원자료 분석도 자체 사용 제한을 따른다.

[공식 사용량 안내](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)는 Work/Codex 공유 allowance와 regular Chat의 사용량 표시를 구분한다. 계정의 실제 사용량 화면을 확인할 수 없으면 `공유 할당량 영향 미확인`으로 남긴다. 이 계획은 한도 회피 방법이 아니다.

## 실행 전 한 번만 확인할 것

1. 실제 code/shell 또는 Python 도구, 현재 OS, Python, 쓰기 가능한 작업 폴더를 확인한다. 전체 환경 변수·인증 파일을 출력하지 않는다.
2. 셸 네트워크가 있다면 공개 릴리스 파일, 패키지 저장소, CHZZK 메타데이터·영상 CDN, 필요한 모델 파일 각각에 실제 접근 가능한지 구분한다. GitHub 도구의 성공을 CDN 접근 성공으로 바꾸지 않는다. 차단된 접근을 우회하지 않는다.
3. 같은 방송의 자막·메타데이터·채팅·클립이 이미 제공됐는지 확인한다. 영상 길이, cue/메시지 시각, offset, 파일 SHA, 수집 범위와 누락을 유지한다.
4. FFmpeg/FFprobe, 모델 의존성, RAM·디스크·장치와 현재 실행 시간을 확인한다. GPU·Docker·sudo·장기 백그라운드 실행·환경 영속성을 가정하지 않는다. 필요한 모델과 영상이 환경에 들어갈 수 없으면 원자료 ZIP 경로로 전환한다.
5. 하위 에이전트 도구가 실제로 있을 때만 독립 자료 검증이나 반례 점검을 위임한다. 최종 의미 작성과 통합 책임은 주 대화 하나가 맡는다. 같은 job·읽기 상태·응답 파일의 변경자는 하나로 둔다.

Work의 shell networking, 연결 앱, 웹 검색, cloud browser는 별도 권한이다. 환경의 파일·수명·재개 가능성도 실제로 확인하고 결과를 내려받을 수 있게 남긴다. Codex Cloud 문서의 VM 사양·보관 기간을 Work Cloud의 보장으로 옮기지 않는다. [Work cloud 경계](https://learn.chatgpt.com/docs/enterprise/chatgpt-work-cloud-security)

하위 에이전트는 eligible Work 계정에 제공되며 더 많은 모델·도구 사용량을 소모한다. 노출되지 않은 도구를 프롬프트만으로 만들거나 위임했다고 주장하지 않는다. [공식 subagents 안내](https://learn.chatgpt.com/docs/agent-configuration/subagents)

## 경로 A: 자체 셸과 원자료 준비 기능이 실제로 있는 경우

공개 릴리스 ZIP·manifest·checksum을 확인해 별도 작업 폴더에 푼다. 개발 저장소의 private 설정이나 운영 서버를 가져오지 않는다. 아래 명령은 공개 배포에 존재하는 인터페이스이며 실행한 결과를 뜻하지 않는다. `<runtime>`과 `<workspace-python>`은 설치 결과의 실제 경로로 바꾼다.

준비된 SRT 등 텍스트 자료만 분석하는 작은 설치 예시는 다음과 같다. 이 core 설치가 영상 다운로드나 STT까지 준비한다는 뜻은 아니다.

```sh
python3 install.py --workspace ../loki-workspace --mode skill --features core
<workspace-python> <runtime>/loki.py --config <workspace>/loki.json doctor
```

VOD 다운로드·전사는 필요한 media/speech 의존성과 모델을 먼저 준비해야 한다. 기본 전체 설치는 로컬 모델 준비가 포함되며 CPU 경로도 있지만, 환경의 자원·네트워크와 실제 소요 시간은 별도 확인한다. `--mode plugin`은 native Codex 등록을 수행하므로 이 실험의 기본 선택으로 삼지 않는다. 현재 구현에서 Codex CLI의 plugin 등록과 별도 모델 작성 호출은 서로 다른 동작이다.

공개 익명 접근이 가능한 VOD 한 개의 원자료 준비 인터페이스는 다음과 같다.

```sh
<workspace-python> <runtime>/source_acquire.py --workspace <workspace> --vod https://chzzk.naver.com/video/<VOD번호>
```

이 명령은 영상·메타데이터·자막·clock 기록과 prepare spec 템플릿을 준비하고 선택적인 익명 채팅 수집 상태를 남긴다. 의미 요약은 만들지 않는다. 익명 접근이 실패하면 로그인·유료 접근·쿠키 수집으로 전환하지 않는다. 영상·음성의 다운로드 성공이나 STT 결과를 실제 시청·청취로 보고하지 않는다.

`features`는 설치된 도구의 범위이며 원자료 전체 수집 보장이 아니다. 공개 `prepare`는 확정 VOD clock의 STT를 필수로 사용하고, `media_path`를 제공하면 해당 기능·실제 파일·source 소유 경로를 검증한다. media·multimodal 설치도 모든 job에 영상·완전 채팅·클립·댓글을 요구하지 않는다. 정상 CHZZ 저장소의 엄격한 수집 완료 조건은 별도 제품 경로에 유지되며 이 공개 입력 계약과 혼동하지 않는다.

VOD 번호만 제공된 실험은 다음 순서로 가능 여부를 판정한다. 아래 작성자 신원 확장은 **향후 최소 제안이며 3.0.6에 구현됐다는 뜻이 아니다**.

1. 실제 셸·네트워크·설치 자원과 `source_acquire`의 대상 VOD 접근을 확인한다. 이 조건이 없으면 같은 방송의 ZIP을 사용하는 경로 B로 진행한다.
2. 원자료의 수집 범위를 확인한다. `source_acquire`는 영상·메타데이터·STT와 선택적 채팅을 준비하며 클립·댓글까지 수집하지 않는다. 공개 prepare는 같은 VOD의 `user_clips.json`·`timeline_comments.json`이 있으면 소비하고, 없어도 미수집 상태를 유지한다. `not_collected`·부분 수집·일시 실패를 확인된 부재와 구분하고 빈 스냅샷으로 바꾸지 않는다. 판단을 바꿀 질문에 필요한 경우 같은 방송의 기존 ZIP·스냅샷을 재사용하거나 준비 단계의 허용된 기존 익명 수집 경로를 사용한다. 공개 도구에 그 수집 연결이 없으면 기존 수집 owner에 한정한 향후 변경안을 제안한다. 선택 자료 전체나 모든 매체의 수집을 작성 선행 조건으로 만들지 않는다.
3. 아래 작성자 신원 계약을 확인한다. 현재 Codex 전용 계약과 ChatGPT의 실제 신원이 맞지 않으면, 기존 author identity·consumer receipt가 ChatGPT 작성 사실을 정직하게 검증·기록하도록 확장하는 최소 호환성 변경을 제안한다. 거짓 Codex/대화 ID가 거절되는 반례도 포함한다. 별도 작성자나 두 번째 실행 경로를 추가하지 않는다. 지원 전에는 경로 B의 외부 초안으로 완성하고 정식 Loki submit 성공으로 보고하지 않는다.

입력과 작성자 계약이 실제로 충족된 경우에만 다음 로컬 작성 경로로 이어간다. 지원하지 않는 submit을 반복 시도하며 호환성 격차를 숨기지 않는다.

준비된 자료에 대한 기존 로컬 도구는 다음과 같다. `--config`는 subcommand 앞에 둔다.

```sh
<workspace-python> <runtime>/loki.py --config <workspace>/loki.json prepare --spec <workspace>/spec.json
<workspace-python> <runtime>/loki.py --config <workspace>/loki.json read --job-root <job-root> --expected-revision <revision> --action <workspace>/read-action.json
<workspace-python> <runtime>/loki.py --config <workspace>/loki.json remember --job-root <job-root> --expected-revision <revision> --note <workspace>/note.json
<workspace-python> <runtime>/loki.py --config <workspace>/loki.json memory --job-root <job-root> --expected-revision <revision> --query <query>
<workspace-python> <runtime>/loki.py --config <workspace>/loki.json submit --job-root <job-root> --expected-revision <revision> --response <workspace>/response.json
```

### 현재의 실제 호환성 경계

공개 `skills/loki/runtime/loki.py`는 `network=deny`, `author_mode=current_codex`, `model_transport=disabled` 계약을 사용한다. 현재 conversation job은 실제 `author={thread_id,turn_id,actual_model}`를 요구하고 provenance mode도 `current_conversation_codex`다. prepare/read/remember/submit은 로컬 어댑터이며 별도 provider 작성 호출은 없지만, 그것만으로 일반 ChatGPT의 정식 작성·제출 지원이 입증되지는 않는다.

실제 대화 정체성을 확인하지 못하거나 ChatGPT 작성자가 이 provenance 계약에 맞지 않으면 ID·모델명·Codex 실행 이력을 만들어 넣지 않는다. config나 validator를 약화해 통과시키지 않는다. 가능한 원문 준비·분석은 유지하고 경로 B의 명시적인 ChatGPT 초안으로 결과를 내며, 정식 Loki submit은 `호환성 미확인`으로 구분한다. 이 계획은 위 호환성 변경을 제안할 뿐 ChatGPT provenance 지원을 구현하지 않는다.

## 경로 B: GitHub 읽기와 파일 분석만 가능한 경우

기존 CHZZ의 **ChatGPT용 방송 자료 ZIP**을 사용한다. 관리자가 같은 VOD·같은 실행의 자료를 선택해 내보낸 ZIP에는 `START_PROMPT.txt`, `EDITORIAL_CONTRACT.md`, 실제 입력 manifest·원자료·해시·누락 상태가 들어간다. 기존 요약을 ZIP 때문에 다시 생성하지 않는다.

운영자 내보내기 인터페이스는 다음과 같다. 이것은 CHZZ 원자료를 가진 환경에서 실행하는 명령이며, 일반 ChatGPT의 외부 다운로드 기능을 추가하지 않는다. output 생략은 미리보기이고 기존 ZIP을 덮어쓰지 않는다.

```sh
python -m pipeline.chatgpt_export --video-no <VOD번호> --work-root <같은-VOD-자료폴더> --metadata <같은-실행-metadata.json> --runtime-root <같은-실행-root>
python -m pipeline.chatgpt_export --video-no <VOD번호> --work-root <같은-VOD-자료폴더> --metadata <같은-실행-metadata.json> --runtime-root <같은-실행-root> --output <새-원자료.zip>
```

원자료 ZIP은 기존 AI 선정 답·평가 정답·HTML·전체 영상·모델·인증·설정·검색 DB를 포함하는 패키지가 아니다. 이미지나 음성이 필요한 판단은 해당 자료를 명시적으로 추가하거나 미확인으로 남긴다. 텍스트 ZIP만으로 시청·청취·음향 기반 발견을 통과했다고 보고하지 않는다.

ChatGPT는 ZIP 안의 `chzz.chatgpt_editorial_draft.v2` 계약에 맞는 새 초안을 작성한다. 이 결과를 이름만 바꿔 accepted QG1이나 정식 Loki 생성 결과로 만들지 않는다. 제품 가져오기·관리자 저장·페이지 발행은 별도 권한과 정상 경로다.

## 작성과 개선안 도출

- 단서로 위치를 찾고 판단을 바꿀 맥락만 확인한다. 웃음·물음표·감정 반응·클립·음향 피크는 자동 정답이나 채택 할당량이 아니다. 낮은 반응의 정보·태도·정서, 긴 대화와 먼 설정/회수도 같은 가치 기준으로 판단한다.
- D1/D2 활동 지도, Point 탐색 주소, Point와 1:1 Story, 실제 재생 가능한 Highlight 편집 구성을 구분한다. Point-only·Point-free·여러 Point의 한 Highlight·단일/다중 span·긴 지속 재미를 보존한다. 관계·화자·시계가 불확실하면 정확히 남긴다.
- 준비된 원자료에서 10–15분의 집중 작성으로 완성본을 내는 것을 목표로 한다. 다운로드·STT·빌드·복구·provider 대기를 포함한 전체 job 강제 종료 시간이 아니다. 해결한 판단은 기존 본문·채택/제외 필드로 옮기고 notes에는 미해결 질문만 남긴다. 이미 해결한 원문을 가용성만으로 다시 열지 않는다.
- 중요한 판단을 바꿀 질문이 더 없으면 신호·도구를 소진하지 않고 완성한다. 최종 JSON과 읽기/판단 근거를 먼저 고정한 뒤 별도 개선 메모에서 최초 손실 지점·작은 코드/프롬프트 변경안·반례·남은 불확실성을 정리한다. 기존 수락본이나 평가 정답을 먼저 writer에게 주입하지 않는다.
- 결과에는 사용 버전·실제 작성자·원자료 SHA·읽은 범위·미확인 매체·실행한 도구·실패·설치와 작성의 시간을 구분한다. 코드 실행·자료 분석·실험 제안을 실제 요약 성공이나 자동 품질 향상으로 과장하지 않는다. 재시작·반복 호출·새 job을 만들어 한도나 실패를 숨기지 않는다.

실험 완료의 최소 산출물은 실제 최종 JSON 또는 명시적인 ChatGPT 초안, 근거/누락 상태, 재개에 필요한 파일, 작은 개선 제안이다. 지원하지 않는 환경에서는 정확한 차단 조건과 필요한 원자료 ZIP을 알려주며, 무한 대기나 가상의 백그라운드·에이전트 실행을 약속하지 않는다.

복사할 지시문은 [ChatGPT 실험 입력문](chatgpt-linux-experiment-prompt.md)을 사용한다.
