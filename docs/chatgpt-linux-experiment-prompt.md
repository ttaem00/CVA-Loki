# ChatGPT 자체 환경용 Loki 실험 입력문

아래 블록의 VOD 번호와 실제 게시된 릴리스 정보를 바꾸어 사용한다. GitHub 연결과 셸 실행 권한은 다르다. 일부 플랜은 Work와 Codex 사용량을 공유하므로 이 입력문은 Codex 할당량을 전혀 쓰지 않는다는 보장을 하지 않는다. [공식 사용량 안내](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)

현재 공개 런타임의 Codex 작성자/provenance 계약을 ChatGPT용으로 우회하라는 요청도 아니다. 자세한 조건과 정확한 기존 명령은 [실험 계획](chatgpt-linux-experiment-plan.md)에 있다.

```text
대상 VOD: <VOD 번호>
VOD URL: https://chzzk.naver.com/video/<VOD 번호>
공개 저장소: https://github.com/ttaem00/CVA-Loki
사용할 릴리스: <실제로 게시된 버전·릴리스 URL·ZIP SHA256>
첨부 원자료: <없음 / 같은 VOD의 ChatGPT용 원자료 ZIP과 추가 매체 파일>

목표
현재 ChatGPT인 네가 실제 원자료를 읽고 방송 요약·D1/D2·Point·Story·Highlight를 작성해라.
가능한 경우 네 자체 실행 환경의 기존 Loki 도구를 사용하고, 결과를 고정한 뒤 작은 개선안을 도출해라.
별도 Codex/Claude CLI 작성자, 외부 모델 API, API 키·유료 모델 경로를 실행하지 마라.
현재 ChatGPT의 사용량이 발생함을 숨기거나 Work/Codex 공유 할당량을 쓰지 않는다고 단정하지 마라.

1. 실제 환경 확인
- 네 실제 도구에서 GitHub 코드 읽기, 파일 분석 Python, code/shell, 외부 네트워크,
  이미지·음성 확인, 하위 에이전트가 각각 가능한지 짧게 구분해라.
- GitHub 연결을 로컬 clone·Linux 셸·VOD CDN 접근 권한으로 간주하지 마라.
- 사용할 공개 릴리스·checksum과 원자료 파일을 먼저 확인해라.
  확인한 코드와 문서의 명령만 사용하고, 없는 명령·도구·백그라운드 실행을 만들지 마라.
- 장치·RAM·디스크·FFmpeg·STT/탐색 모델·네트워크를 실제로 확인해라.
  GPU·Docker·관리자 권한·환경 영속성을 가정하지 마라.
- 인증 파일·환경 변수 전체·쿠키·브라우저 로그인·비공개 설정을 읽거나 복사하지 마라.

2. 가능한 입력 경로 선택
- 자체 셸과 허용된 다운로드·모델 자원이 있으면 공개 릴리스의 설치·doctor·
  source_acquire 인터페이스로 익명 접근 가능한 이 VOD 한 개의 원문을 준비해라.
  다운로드/STT 성공은 네가 영상·음성을 보고 들었다는 뜻이 아니다.
- VOD 번호만 주어졌다면 셸/네트워크와 원자료 수집 가능 여부, 지원 스냅샷,
  실제 작성자 계약의 순서로 호환성 격차를 먼저 확인해라.
  현재 source_acquire는 영상·메타데이터·STT·채팅을 준비하지만 신규 normal prepare가
  소비하는 user_clips.json·timeline_comments.json까지 수집한다고 가정하지 마라.
  같은 VOD의 기존 스냅샷을 재사용하거나 기존 온라인 수집 owner의 익명 수집을 연결할
  한정된 향후 변경안을 제안해라. 자료 있음·확인된 없음·누락·일시 실패를 보존하고
  빈 자료를 만들어 준비 완료로 처리하지 마라. 이는 3.0.6의 구현된 기능이 아니다.
- 자료가 이미 있으면 같은 VOD·같은 source revision의 SRT·메타데이터·채팅·클립·
  필요한 매체를 재사용하고 영상 길이·원문 순서·시계·offset·SHA를 보존해라.
- 외부 다운로드가 안 되면 우회하거나 끝없이 재시도하지 마라.
  가능한 GitHub 코드 조사와 첨부 자료 분석은 진행하고, 부족한 같은 VOD 원자료 ZIP을 명확히 알려라.
- 일반 데이터 분석 Python만 있으면 첨부된 ChatGPT용 ZIP의 START_PROMPT.txt,
  EDITORIAL_CONTRACT.md와 입력 manifest를 정본으로 사용해라.
- Loki의 author_mode/current conversation provenance가 실제 ChatGPT 작성자와 맞지 않거나
  실제 thread/turn 정보를 확인할 수 없으면 거짓 ID·Codex 이력을 만들어 넣지 마라.
  기존 author identity·consumer receipt에 실제 ChatGPT 작성 사실을 정직하게 연결할
  최소 호환성 변경안과 거짓 신원 거절 반례를 먼저 제안해라. 두 번째 작성자를 부르거나
  가드·config를 약화해 submit을 강제하지 마라. 지원되지 않는 submit을 반복하지 말고
  ZIP의 chzz.chatgpt_editorial_draft.v2 외부 초안 경로로 결과를 완성해라.
  이 제안은 구현·가드 우회 권한이나 정식 Loki 작성 성공의 증거가 아니다.

3. 읽기와 작성
- 방송 전체의 분할 관찰·잠정 지도·반응/클립 신호는 가벼운 위치 단서로 사용해라.
  현재 사실·주체·관계·가치·컷·합리적 제외를 바꿀 구체적인 질문에 필요한 원문만 더 읽어라.
- 웃음, ㅋㅋㅋ, ?, 감정 표현, 고성·비명·억양, 대화 응수는 그 원인과 앞뒤 말을 확인해라.
  신호량만으로 재미·관계·인과를 확정하지 마라. 음향 관측과 실제 청취를 구별해라.
- 가까운 공방과 먼 설정/회수, 긴 지속 대화, 조용한 정보·태도·정서의 가치를 보존해라.
  같은 이름·단어·시간 근접만으로 관계를 만들지 마라.
- D1/D2는 실제 활동 지도, Point는 유용한 탐색 주소, Story는 각 Point의 1:1 설명,
  Highlight는 선택한 실제 span만으로 이해되는 편집 구간이라는 서로 다른 책임을 지켜라.
  Point-only·Point-free·단일/다중 Point·단일/다중 span·긴 연속 구간을 내용에 맞게 허용해라.
  개수·길이·반전·큰 반응을 할당하거나 없는 사실·화자·감정을 만들지 마라.
- 준비된 원자료에서 10–15분의 집중 작성으로 완성본을 내는 것을 목표로 해라.
  설치·다운로드·STT·복구·외부 대기를 포함한 전체 job의 강제 종료 시간으로 쓰지 마라.
- 해결한 중요한 판단은 실제 기존 draft·채택/제외 필드와 지지 원문에 즉시 반영해라.
  notes에는 미해결 질문·가설·불확실성만 남겨라. '기존 초안 유지' 메모로 본문을 대신하지 마라.
- 이미 해결한 판단은 새 반증이나 실제 누락이 있을 때만 다시 열어라.
  활성 원문과 읽기 이력은 필요한 만큼 재사용하고 옛 페이지를 가용성만으로 재활성화하지 마라.
- 판단을 바꿀 중요한 질문이 없거나 추가 읽기의 가치가 낮으면,
  미독 신호·페이지·선택적 도구가 남아 있어도 불확실성을 정확히 남기고 최종 JSON을 완성해라.
- subagent 도구가 실제 제공되고 독립 검증에 이득이 있을 때만 한정된 원자료 검증·반례 점검을 위임해라.
  같은 job/읽기 상태/응답 파일의 동시 수정을 피하고 주 대화가 최종 의미와 JSON을 책임져라.
  하위 에이전트가 더 많은 사용량을 쓸 수 있음을 고려하고 없으면 있다고 주장하지 마라.

4. 결과 고정 후 개선안
- 첫 완성 JSON과 근거를 먼저 보존해라. 기존 AI 답·수락본·평가 정답·특정 장면 정답을
  최초 writer 입력으로 주입하거나 좋은 답으로 몰래 고쳐 자율 발견 성공으로 제시하지 마라.
- 현재 런타임에 실제로 맞으면 기존 prepare/read/remember/memory/submit 계약을 사용해라.
  외부 ChatGPT ZIP 경로라면 EDITORIAL_CONTRACT.md의 chzz.chatgpt_editorial_draft.v2를 지켜라.
  이 초안을 이름만 바꿔 accepted QG1 또는 정식 Loki 생성 이력으로 위장하지 마라.
- 원문 근거·실제 읽은 범위·채택/제외/미확정·볼 수 없거나 듣지 못한 자료를 구분해라.
  이미지 추출·인코딩·파일 존재를 실제 이미지 확인·음성 청취로 대신하지 마라.
- 그다음 최초 손실 지점, 기존 코드/프롬프트의 최소 변경안, 의미 있는 반례와
  아직 검증하지 못한 내용을 짧은 개선 메모로 내라. 새 평가 플랫폼·서비스·필수 단계를 만들지 마라.
- 결과 JSON/초안, 자료·읽기 근거, 실제 실행/미실행과 실패, 사용 버전·작성자,
  재개에 필요한 파일과 다운로드 가능한 산출물을 남겨라.
- 공개 발행·GitHub 변경·운영 서버 채택은 하지 마라. 모델 호출·백그라운드 실행·에이전트·
  실제 시청/청취·품질 개선을 하지 않았다면 했다고 쓰지 마라.
```

도구 구분의 공식 근거: [GitHub 연결](https://help.openai.com/en/articles/11145903-connecting-github-to-chatgpt), [데이터 분석의 외부 요청 제한](https://help.openai.com/en/articles/8437071-data-analysis-with-chatgpt), [Work code/shell·network 경계](https://learn.chatgpt.com/docs/enterprise/chatgpt-work-overview), [Work subagents의 제공 조건과 추가 사용량](https://learn.chatgpt.com/docs/agent-configuration/subagents).
