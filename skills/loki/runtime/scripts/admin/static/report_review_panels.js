    function renderDiagnostics(data) {
      if (!diagnosticsSectionEl || !diagnosticsBodyEl) return;
      const diagnostics = data && data.boundary_diagnostics || {};
      const verifier = data && data.evidence_verifier || {};
      const chapter = data && data.youtube_chapter_readiness || {};
      const retry = data && data.failed_stage_retry || {};
      const timelineDiag = data && data.comment_timeline_diagnostics || {};
      const inputDiag = data && data.summary_input_diagnostics || {};
      const items = Array.isArray(diagnostics.items) ? diagnostics.items : [];
      const findings = Array.isArray(verifier.findings) ? verifier.findings : [];
      const chapterFailures = Array.isArray(chapter.failures) ? chapter.failures : [];
      const inputItems = Array.isArray(inputDiag.items) ? inputDiag.items : [];
      const hasChapter = chapter && typeof chapter === 'object' && Object.keys(chapter).length > 0;
      const hasRetry = retry && retry.visible;
      const hasTimelineDiag = timelineDiag && typeof timelineDiag === 'object' && Object.keys(timelineDiag).length > 0;
      const hasInputDiag = inputDiag && typeof inputDiag === 'object' && (Object.keys(inputDiag).length > 0 || inputItems.length > 0);
      diagnosticsSectionEl.style.display = (items.length || findings.length || diagnostics.status || verifier.status || hasChapter || hasRetry || hasTimelineDiag || hasInputDiag) ? '' : 'none';
      const sourceLabel = {
        chzzk_comment_timeline: '챕터 인덱스',
        timeline: '타임라인',
      }[chapter.source] || chapter.source || 'unknown';
      const chapterStatus = hasChapter
        ? (chapter.passed ? 'PASS' : 'FAIL')
        : 'none';
      const verifierSummary = verifier && verifier.summary && typeof verifier.summary === 'object' ? verifier.summary : {};
      const checkedSegments = Number(verifierSummary.checked_segments || 0);
      const verifierMeaning = (() => {
        const status = String(verifier.status || '').toLowerCase();
        if (!status || (!checkedSegments && !findings.length)) {
          return { label: '데이터 부족', action: '이 리포트에는 근거 점검 결과가 아직 없습니다.' };
        }
        if (status === 'pass') {
          return { label: '근거 있음', action: '표시된 요약 근거 구조에서 바로 막히는 항목은 없습니다.' };
        }
        return { label: '확인 필요', action: '아래 항목은 저장/공개 전 사람이 한 번 더 확인해야 합니다.' };
      })();
      const findingMeaning = (finding) => {
        const code = String(finding && finding.code || '');
        const labels = {
          MISSING_SEGMENT_ID: ['위치 연결 없음', '요약 항목이 안정적인 장면 ID와 연결되지 않았습니다.'],
          MISSING_SOURCES: ['근거 데이터 부족', '자막/채팅/맥락 같은 구조화 근거가 비어 있습니다.'],
          RISKY_SOURCE_ONLY_CLAIM: ['보조 신호만 있음', '채팅/오디오/커뮤니티 신호만으로 사실처럼 보이는 문장입니다.'],
          CHAPTER_READINESS_FAIL: ['댓글 타임라인 확인 필요', '댓글용 타임라인 형식이 바로 복사하기엔 부족합니다.'],
          UNMATCHED_MANAGER_EDIT_DIFF: ['관리자 수정 연결 확인', '관리자 수정 기록이 요약 장면과 연결되지 않았습니다.'],
        };
        const [label, action] = labels[code] || ['확인 필요', '근거 점검에서 사람이 확인할 항목으로 표시했습니다.'];
        const ref = finding && finding.segment_ref && typeof finding.segment_ref === 'object' ? finding.segment_ref : {};
        const where = [ref.timecode, ref.title].map((value) => String(value || '').trim()).filter(Boolean).join(' · ');
        return { label, action, where };
      };
      const chapterRows = hasChapter ? `
        <div class="tool-row">
          <strong>YouTube 챕터 준비도: ${escapeHtml(chapterStatus)}</strong><br>
          source=${escapeHtml(sourceLabel)} · chapters=${Number(chapter.chapter_count || 0)}
        </div>
        ${chapterFailures.slice(0, 6).map((failure) => `
          <div class="tool-row">
            <strong>${escapeHtml(failure.code || 'chapter_failure')}</strong><br>
            ${escapeHtml(failure.message || '')}
          </div>
        `).join('')}
      ` : '';
      const itemRows = items.slice(0, 12).map((item) => `
        <div class="tool-row">
          <strong>${escapeHtml(item.segment_id || item.kind || 'segment')}</strong><br>
          ${escapeHtml(item.boundary || item.marker_type || item.type || 'marker')} · ${escapeHtml(item.reason || item.label || '')}
        </div>
      `).join('');
      const findingRows = findings.slice(0, 8).map((finding) => `
        <div class="tool-row">
          ${(() => {
            const meaning = findingMeaning(finding);
            return `
              <strong>${escapeHtml(meaning.label)}</strong><br>
              ${meaning.where ? `<span>${escapeHtml(meaning.where)}</span><br>` : ''}
              <span class="meta-note">${escapeHtml(meaning.action)}</span>
            `;
          })()}
        </div>
      `).join('');
      const retryRows = hasRetry ? `
        <div class="tool-row">
          <strong>재실행 미리보기: ${escapeHtml(retry.title || retry.failed_stage || '')}</strong><br>
          ${escapeHtml(retry.manager_message || '')}<br>
          <span class="meta-note">${escapeHtml(retry.cost_risk || '')}</span>
        </div>
      ` : '';
      const timelineFailures = hasTimelineDiag && timelineDiag.failure_digest && Array.isArray(timelineDiag.failure_digest.failures)
        ? timelineDiag.failure_digest.failures
        : [];
      const timelineRows = hasTimelineDiag ? `
        <div class="tool-row">
          <strong>댓글 타임라인 ${escapeHtml(timelineDiag.manager_label || '확인 필요')}</strong><br>
          ${escapeHtml(timelineDiag.manager_message || '자동 생성 결과를 리포트에 넣지 않았습니다.')}<br>
          <span class="meta-note">원문은 저장하지 않고 형식 오류 코드만 표시합니다.</span>
        </div>
        ${timelineFailures.slice(0, 6).map((failure) => `
          <div class="tool-row">
            <strong>${escapeHtml(failure.code || 'structured_output')}</strong><br>
            ${escapeHtml(failure.path || '')}${failure.message ? ` · ${escapeHtml(failure.message)}` : ''}
          </div>
        `).join('')}
      ` : '';
      const inputRows = hasInputDiag ? `
        <div class="tool-row">
          <strong>요약 입력 ${escapeHtml(inputDiag.manager_label || '확인 필요')}</strong><br>
          ${escapeHtml(inputDiag.manager_message || '요약 입력 상태를 확인합니다.')}<br>
          <span class="meta-note">원문 자막, 채팅, 커뮤니티 글은 저장하지 않고 상태와 개수만 표시합니다.</span>
        </div>
        ${inputItems.slice(0, 8).map((item) => `
          <div class="tool-row">
            <strong>${escapeHtml(item.message || item.name || item.key || '입력 확인')}</strong><br>
            ${item.detail_label ? `${escapeHtml(item.detail_label)}<br>` : ''}
            <span class="meta-note">${escapeHtml(item.retry_condition || item.status || '')}</span>
          </div>
        `).join('')}
      ` : '';
      diagnosticsBodyEl.innerHTML = `
        <div class="tool-summary">
          <div class="tool-stat"><span class="tool-label">경계 점검</span><span class="tool-value">${escapeHtml(diagnostics.status || 'none')}</span></div>
          <div class="tool-stat"><span class="tool-label">표식 수</span><span class="tool-value">${items.length}</span></div>
          <div class="tool-stat"><span class="tool-label">요약 근거</span><span class="tool-value">${escapeHtml(verifierMeaning.label)}</span></div>
          <div class="tool-stat"><span class="tool-label">요약 입력</span><span class="tool-value">${escapeHtml(hasInputDiag ? (inputDiag.manager_label || '확인 필요') : 'none')}</span></div>
          <div class="tool-stat"><span class="tool-label">챕터 준비</span><span class="tool-value">${escapeHtml(chapterStatus)}</span></div>
          <div class="tool-stat"><span class="tool-label">댓글 타임라인</span><span class="tool-value">${escapeHtml(hasTimelineDiag ? (timelineDiag.manager_label || '확인 필요') : 'none')}</span></div>
        </div>
        <div class="meta-note">${escapeHtml(verifierMeaning.action)} 이 영역은 자동 경고판이고, 요약을 대신 고치거나 품질 통과를 결정하지 않습니다.</div>
        <div class="tool-list">${inputRows || timelineRows || retryRows || chapterRows || itemRows || findingRows || '<div class="meta-note">표시할 점검 항목이 없습니다.</div>'}${inputRows && timelineRows ? timelineRows : ''}${(inputRows || timelineRows) && retryRows ? retryRows : ''}${(inputRows || timelineRows || retryRows) && chapterRows ? chapterRows : ''}${(inputRows || timelineRows || retryRows || chapterRows) && itemRows ? itemRows : ''}${(inputRows || timelineRows || retryRows || chapterRows || itemRows) && findingRows ? findingRows : ''}</div>
      `;
    }

    function renderContextVocabSuggestions(payload) {
      if (!contextVocabSectionEl || !contextVocabBodyEl) return;
      payload = payload && typeof payload === 'object' ? payload : {};
      const channelId = String(payload.channel_id || '');
      const suggestions = Array.isArray(payload.suggestions) ? payload.suggestions : [];
      const vocabStatus = payload.vocab_status && typeof payload.vocab_status === 'object'
        ? payload.vocab_status
        : {};
      const sourceStatus = vocabStatus.source && typeof vocabStatus.source === 'object'
        ? vocabStatus.source
        : {};
      const applicationStatus = vocabStatus.application && typeof vocabStatus.application === 'object'
        ? vocabStatus.application
        : {};
      if (contextVocabState.channel_id !== channelId) {
        contextVocabState.staged_vocab = null;
        contextVocabState.dismissed = new Set();
        contextVocabState.approval_states = {};
      }
      contextVocabState.channel_id = channelId;
      contextVocabState.channel_name = String(payload.channel_name || '');
      contextVocabState.video_count = Number(payload.video_count || 0);
      contextVocabState.suggestions = suggestions;
      contextVocabSectionEl.style.display = channelId ? '' : 'none';
      if (!channelId) {
        contextVocabBodyEl.innerHTML = '<div class="meta-note">채널 정보가 없어 반복 후보를 계산하지 않습니다.</div>';
        return;
      }
      const staged = contextVocabState.staged_vocab && Array.isArray(contextVocabState.staged_vocab.whitelist)
        ? new Set(contextVocabState.staged_vocab.whitelist.map((row) => String(row.term || '').trim()).filter(Boolean))
        : new Set();
      const visible = suggestions.filter((row) => !contextVocabState.dismissed.has(String(row.term || '')));
      const statusSummary = `
        <div class="tool-summary" data-testid="report-vocab-status">
          <div class="tool-stat"><span class="tool-label">사용 단어장</span><span class="tool-value">${escapeHtml(sourceStatus.selected_source_label || '확인 전')}</span></div>
          <div class="tool-stat"><span class="tool-label">승인 단어</span><span class="tool-value">${Number(sourceStatus.approved_term_count || 0)}개</span></div>
          <div class="tool-stat"><span class="tool-label">문맥 별칭</span><span class="tool-value">${Number(sourceStatus.context_alias_count || 0)}개</span></div>
          <div class="tool-stat"><span class="tool-label">이번 VOD</span><span class="tool-value">${escapeHtml(applicationStatus.label || '적용 기록 확인 전')}</span></div>
        </div>
        <div class="meta-note">${escapeHtml(sourceStatus.canonical_state_label || '')}${sourceStatus.compatibility_state_label ? ` · ${escapeHtml(sourceStatus.compatibility_state_label)}` : ''}${sourceStatus.conflict_label ? ` · ${escapeHtml(sourceStatus.conflict_label)}` : ''}</div>
      `;
      const saveBar = `
        <div class="tool-actions">
          <a class="ghost" href="/streamer-vocab" target="_blank" rel="noopener">단어 사전 열기</a>
          <a class="ghost" href="/manager-approvals" target="_blank" rel="noopener">승인함 열기</a>
        </div>
        <div class="meta-note">단어 후보는 승인함에 보낸 뒤 승인할 때만 채널 단어 사전에 저장됩니다. 저장된 단어는 다음 요약 생성/재생성부터 표기 힌트로 반영됩니다.</div>
      `;
      if (!visible.length) {
        contextVocabBodyEl.innerHTML = `
          ${statusSummary}
          <div class="meta-note">${escapeHtml(payload.message || '같은 채널의 여러 context.md에 반복 등장한 새 후보가 없습니다.')}</div>
          ${saveBar}
        `;
      } else {
        const rows = visible.slice(0, 8).map((c) => {
          const term = String(c.term || '').trim();
          const already = staged.has(term);
          const approvalState = contextVocabState.approval_states[term] || {};
          const stateName = String(approvalState.state || '');
          const stateMessage = String(approvalState.message || '');
          const addDisabled = already || stateName === 'pending' || stateName === 'saved';
          const addLabel = already
            ? '단어 사전에 등록됨'
            : stateName === 'pending'
              ? '저장 중'
              : stateName === 'saved'
                ? '승인함에 있음'
                : '승인';
          const sourceRows = Array.isArray(c.sources) ? c.sources.slice(0, 2).map((s) => {
            const label = `${s.video_no || '-'} · ${s.occurrences || 1}회`;
            return `${escapeHtml(label)}${s.snippet ? ` · ${escapeHtml(s.snippet)}` : ''}`;
          }).join('<br>') : '';
          const src = Array.isArray(c.source_videos) ? c.source_videos.slice(0, 5).join(', ') : '';
          return `
            <div class="context-vocab-card">
              <div>
                <div class="context-vocab-term">${escapeHtml(term)}</div>
                <div class="context-vocab-evidence">context.md ${Number(c.source_count || 0)}개 VOD · 총 ${Number(c.occurrences || 0)}회${src ? ` · VOD ${escapeHtml(src)}` : ''} · 점수 ${Number(c.quality_score || 0)}</div>
                ${sourceRows ? `<div class="context-vocab-evidence">${sourceRows}</div>` : ''}
              </div>
              <div class="context-vocab-actions">
                <button type="button" data-context-vocab-add="${escapeHtml(term)}" ${addDisabled ? 'disabled' : ''}>${escapeHtml(addLabel)}</button>
                <button type="button" class="ghost" data-context-vocab-demote="${escapeHtml(term)}">낮은 점수로 보내기</button>
                <button type="button" class="ghost" data-context-vocab-hide="${escapeHtml(term)}">이 후보 숨김</button>
                <div class="context-vocab-state ${escapeHtml(stateName)}" role="status" aria-live="polite">${escapeHtml(stateMessage)}</div>
              </div>
            </div>
          `;
        }).join('');
        const more = visible.length > 8 ? `<div class="meta-note">외 ${visible.length - 8}개 후보는 단어 사전 화면에서 모두 볼 수 있습니다.</div>` : '';
        contextVocabBodyEl.innerHTML = `
          ${statusSummary}
          <div class="tool-summary">
            <div class="tool-stat"><span class="tool-label">채널</span><span class="tool-value">${escapeHtml(payload.channel_name || channelId.slice(0, 12))}</span></div>
            <div class="tool-stat"><span class="tool-label">후보</span><span class="tool-value">${visible.length}</span></div>
            <div class="tool-stat"><span class="tool-label">범위</span><span class="tool-value">${Number(payload.video_count || 0)} VOD</span></div>
          </div>
          <div class="context-vocab-list">${rows}</div>
          ${more}
          ${saveBar}
        `;
      }
      contextVocabBodyEl.querySelectorAll('[data-context-vocab-add]').forEach((btn) => {
        btn.addEventListener('click', () => registerContextVocabSuggestion(btn.dataset.contextVocabAdd || ''));
      });
      contextVocabBodyEl.querySelectorAll('[data-context-vocab-demote]').forEach((btn) => {
        btn.addEventListener('click', () => saveContextVocabFeedback(btn.dataset.contextVocabDemote || '', 'demoted'));
      });
      contextVocabBodyEl.querySelectorAll('[data-context-vocab-hide]').forEach((btn) => {
        btn.addEventListener('click', () => saveContextVocabFeedback(btn.dataset.contextVocabHide || '', 'hidden'));
      });
      const saveBtn = document.getElementById('saveContextVocabBtn');
      if (saveBtn) saveBtn.addEventListener('click', saveContextVocabDraft);
      const resetBtn = document.getElementById('resetContextVocabBtn');
      if (resetBtn) resetBtn.addEventListener('click', () => {
        contextVocabState.staged_vocab = null;
        renderContextVocabSuggestions({
          ...payload,
          suggestions: contextVocabState.suggestions,
        });
        setStatus('단어 후보 초안을 되돌렸습니다. 저장된 사전은 바뀌지 않았습니다.', 'ok');
      });
    }

    async function ensureContextVocabDraft() {
      if (contextVocabState.staged_vocab) return contextVocabState.staged_vocab;
      const channelId = contextVocabState.channel_id;
      if (!channelId) throw new Error('channel_id가 없습니다.');
      const data = await api(`/api/streamer-vocab/get?channel_id=${encodeURIComponent(channelId)}`);
      const vocab = data.vocab || {};
      vocab.whitelist = Array.isArray(vocab.whitelist) ? vocab.whitelist.slice() : [];
      vocab.blacklist = Array.isArray(vocab.blacklist) ? vocab.blacklist.slice() : [];
      if (!vocab.channel_id) vocab.channel_id = channelId;
      if (!vocab.channel_name && contextVocabState.channel_name) vocab.channel_name = contextVocabState.channel_name;
      contextVocabState.staged_vocab = vocab;
      return vocab;
    }

    function appendVocabFeedback(vocab, bucketName, term, eventType, source) {
      vocab.feedback = vocab.feedback && typeof vocab.feedback === 'object'
        ? vocab.feedback
        : { candidates: {}, terms: {} };
      const bucket = bucketName === 'terms' ? 'terms' : 'candidates';
      vocab.feedback[bucket] = vocab.feedback[bucket] && typeof vocab.feedback[bucket] === 'object'
        ? vocab.feedback[bucket]
        : {};
      const now = new Date().toISOString().replace(/\.\d{3}Z$/, '+00:00');
      const meta = vocab.feedback[bucket][term] || { state: '', count: 0, updated_at: '', events: [] };
      meta.state = eventType;
      meta.count = Number(meta.count || 0) + 1;
      meta.updated_at = now;
      meta.events = Array.isArray(meta.events) ? meta.events.slice(-19) : [];
      meta.events.push({ type: eventType, at: now, source: source || 'admin', note: '' });
      vocab.feedback[bucket][term] = meta;
    }

    async function registerContextVocabSuggestion(termValue) {
      const term = String(termValue || '').trim();
      if (!term) return;
      try {
        contextVocabState.approval_states[term] = { state: 'pending', message: '승인함에 저장 중입니다.' };
        renderContextVocabSuggestions({
          channel_id: contextVocabState.channel_id,
          channel_name: contextVocabState.channel_name,
          suggestions: contextVocabState.suggestions,
          video_count: contextVocabState.video_count || 0,
        });
        const vocab = await ensureContextVocabDraft();
        vocab.whitelist = Array.isArray(vocab.whitelist) ? vocab.whitelist : [];
        if (vocab.whitelist.some((row) => String(row.term || '').trim() === term)) {
          contextVocabState.approval_states[term] = { state: 'saved', message: '이미 단어 사전에 있습니다.' };
          setStatus('이미 단어 사전에 등록되어 있습니다.', 'warn');
          renderContextVocabSuggestions({
            channel_id: contextVocabState.channel_id,
            channel_name: contextVocabState.channel_name,
            suggestions: contextVocabState.suggestions,
            video_count: contextVocabState.video_count || 0,
          });
          return;
        }
        const suggestion = contextVocabState.suggestions.find((row) => String(row.term || '').trim() === term) || {};
        const approvalPayload = {
          request_text: `단어 후보 "${term}"을(를) 채널 단어 사전에 저장할까요?`,
          risk: '승인하면 다음 요약 생성/재생성부터 표기 힌트로 사용됩니다.',
          expected_effect: '반복 등장한 이름/용어의 오인식과 표기 흔들림을 줄입니다.',
          source_artifact: `report_editor_context_vocab:${contextVocabState.channel_id}:${term}`,
          proposed_action: {
            type: 'vocab_candidate_save',
            payload: {
              channel_id: contextVocabState.channel_id,
              channel_name: contextVocabState.channel_name,
              term,
              aliases: [],
              note: suggestion.note || 'context.md recurring term',
              source_ui: 'report_editor_context_vocab',
            },
          },
        };
        const data = await api('/api/manager-approvals/stage', {
          method: 'POST',
          body: JSON.stringify(approvalPayload),
        });
        if (data?.ok !== true) throw new Error('승인함 저장 응답을 확인하지 못했습니다.');
        const [staged] = window.ReportAdminApprovalState.checkedList({approvals: [data.approval]});
        reportApprovalRead++;
        reportApprovalItems = [staged, ...reportApprovalItems.filter((item) => item.approval_id !== staged.approval_id)];
        renderApprovalInbox({approvals: reportApprovalItems});
        contextVocabState.approval_states[term] = { state: 'saved', message: '승인함에 저장됨. 승인하면 단어 사전에 반영됩니다.' };
        renderContextVocabSuggestions({
          channel_id: contextVocabState.channel_id,
          channel_name: contextVocabState.channel_name,
          suggestions: contextVocabState.suggestions,
          video_count: contextVocabState.video_count || 0,
        });
        await refreshApprovalInbox();
        setStatus('승인함에 보냈습니다. 승인하면 단어 사전에 저장됩니다.', 'ok');
      } catch (err) {
        contextVocabState.approval_states[term] = { state: 'error', message: '저장 실패: ' + err.message };
        renderContextVocabSuggestions({
          channel_id: contextVocabState.channel_id,
          channel_name: contextVocabState.channel_name,
          suggestions: contextVocabState.suggestions,
          video_count: contextVocabState.video_count || 0,
        });
        setStatus('단어 후보 승인 요청을 만들지 못했습니다: ' + err.message, 'err');
      }
    }

    async function saveContextVocabFeedback(termValue, eventType) {
      const term = String(termValue || '').trim();
      if (!term || !contextVocabState.channel_id) return;
      const actionText = eventType === 'hidden' ? '후보를 숨겼습니다' : '후보를 낮은 점수로 보냈습니다';
      try {
        await api('/api/streamer-vocab/feedback', {
          method: 'POST',
          body: JSON.stringify({
            channel_id: contextVocabState.channel_id,
            target: 'candidate',
            term,
            event_type: eventType,
            source: 'report_editor',
          }),
        });
        contextVocabState.dismissed.add(term);
        setStatus(actionText + '. 단어 사전 화면에서 복구할 수 있습니다.', 'ok');
        await loadReport();
      } catch (err) {
        setStatus('후보 피드백 저장 실패: ' + err.message, 'err');
      }
    }

    async function saveContextVocabDraft() {
      if (!contextVocabState.staged_vocab || !contextVocabState.channel_id) return;
      try {
        const data = await api('/api/streamer-vocab/save', {
          method: 'POST',
          body: JSON.stringify({
            channel_id: contextVocabState.channel_id,
            vocab: contextVocabState.staged_vocab,
          }),
        });
        contextVocabState.staged_vocab = null;
        setStatus('단어 사전에 저장했습니다. 다음 처리부터 표기 힌트로 반영됩니다.', 'ok');
        await loadReport();
      } catch (err) {
        setStatus('단어 사전 저장 실패: ' + err.message, 'err');
      }
    }

    let reportApprovalItems = [];
    let reportApprovalRead = 0;
    let reportApprovalReadError = '';
    const reportApprovalBusy = new Set();
    const reportApprovalUnknown = new Set();

    function approvalActionLabel(item) {
      const action = item && item.proposed_action || {};
      const payload = action.payload || {};
      if (action.type === 'vocab_candidate_save') {
        return `단어 사전 저장 · ${payload.term || ''}`;
      }
      return action.type || '승인 요청';
    }

    function renderApprovalInbox(payload) {
      if (!approvalInboxSectionEl || !approvalInboxBodyEl) return;
      const focused = document.activeElement;
      const focusKeys = ['approvalId', 'approvalCheck', 'approvalApply', 'approvalResult'];
      const focusKey = focusKeys.find((key) => focused && focused.dataset && focused.dataset[key]);
      const focusId = focusKey ? focused.dataset[focusKey] : '';
      const focusedDecision = focused && focused.dataset && focused.dataset.approvalDecision;
      const approvals = Array.isArray(payload && payload.approvals) ? payload.approvals : [];
      reportApprovalItems = approvals;
      const pending = approvals.filter((item) => item.status === 'pending');
      approvalInboxSectionEl.style.display = '';
      if (!approvals.length) {
        approvalInboxBodyEl.innerHTML = `<div class="meta-note">${escapeHtml(reportApprovalReadError || '지금 승인할 요청이 없습니다.')}</div>`;
        return;
      }
      const rows = approvals.slice(0, 5).map((item) => {
        const status = item.status || 'pending';
        const busy = reportApprovalBusy.has(item.approval_id);
        const unknown = reportApprovalUnknown.has(item.approval_id);
        const application = window.ReportAdminApprovalState.applicationView(item);
        const decided = item.decision && item.decision.decided_at ? ` · 결정 ${escapeHtml(item.decision.decided_at)}` : '';
        const actionLabel = approvalActionLabel(item);
        const buttons = status === 'pending' ? `
          <div class="approval-actions">
            <button type="button" class="primary" data-approval-decision="approved" data-approval-id="${escapeHtml(item.approval_id)}" ${busy || unknown ? 'disabled' : ''}>승인</button>
            <button type="button" class="ghost" data-approval-decision="rejected" data-approval-id="${escapeHtml(item.approval_id)}" ${busy || unknown ? 'disabled' : ''}>거절</button>
          </div>
        ` : '';
        const recovery = unknown || application.canApply ? `
          <div class="approval-actions">
            <button type="button" class="ghost" data-approval-check="${escapeHtml(item.approval_id)}" ${busy ? 'disabled' : ''}>현재 결정·적용 결과 확인</button>
            ${application.canApply && !unknown ? `<button type="button" class="primary" data-approval-apply="${escapeHtml(item.approval_id)}" ${busy ? 'disabled' : ''}>이 승인 항목의 적용 다시 시도</button>` : ''}
          </div>` : '';
        return `
          <div class="approval-card ${escapeHtml(status)}" aria-busy="${busy}" data-approval-result="${escapeHtml(item.approval_id)}" tabindex="-1">
            <strong>${escapeHtml(item.request_text || actionLabel)}</strong>
            <div class="approval-meta">${escapeHtml(actionLabel)}${decided}</div>
            <div class="approval-meta">위험: ${escapeHtml(item.risk || '기록 없음')}</div>
            <div class="approval-meta">효과: ${escapeHtml(item.expected_effect || '기록 없음')}</div>
            <div class="approval-meta">출처: ${escapeHtml(item.source_artifact || 'admin')}</div>
            <div class="approval-meta approval-decision">결정: ${unknown ? '현재 결과 확인 필요 · 마지막 확인: ' : busy ? '처리 결과 확인 중 · 마지막 확인: ' : ''}${escapeHtml(window.ReportAdminApprovalState.decisionLabel(item))}</div>
            <div class="approval-meta approval-application application-${escapeHtml(unknown || busy?'warn':application.tone)}" role="status" aria-live="polite">사전 적용: ${escapeHtml(unknown?'현재 결과 확인 필요':busy?'처리 결과를 확인하고 있습니다.':application.text)}</div>
            ${busy ? '<div class="approval-meta" role="status">처리 중입니다. 같은 요청을 다시 보내지 않습니다.</div>' : ''}
            ${unknown ? '<div class="approval-meta" role="status">처리 응답을 확인하지 못했습니다. 결정은 저장됐을 수 있습니다. 현재 결과를 읽은 뒤 판단하세요.</div>' : ''}
            ${buttons}${recovery}
          </div>
        `;
      }).join('');
      const more = approvals.length > 5 ? `<div class="meta-note">외 ${approvals.length - 5}개는 <a href="/manager-approvals" target="_blank" rel="noopener">승인함 전체 화면</a>에서 볼 수 있습니다.</div>` : '';
      approvalInboxBodyEl.innerHTML = `
        <div class="tool-summary">
          <div class="tool-stat"><span class="tool-label">대기</span><span class="tool-value">${pending.length}</span></div>
          <div class="tool-stat"><span class="tool-label">전체</span><span class="tool-value">${approvals.length}</span></div>
          <div class="tool-stat"><span class="tool-label">저장 방식</span><span class="tool-value">로컬</span></div>
        </div>
        ${reportApprovalReadError ? `<div class="meta-note" role="status">${escapeHtml(reportApprovalReadError)} 확인한 카드와 편집 본문은 유지했습니다.</div>` : ''}
        <div class="approval-card-list">${rows}</div>
        ${more}
      `;
      approvalInboxBodyEl.querySelectorAll('[data-approval-decision]').forEach((btn) => {
        btn.addEventListener('click', () => decideApproval(btn.dataset.approvalId || '', btn.dataset.approvalDecision || ''));
      });
      approvalInboxBodyEl.querySelectorAll('[data-approval-check]').forEach((btn) => {
        btn.addEventListener('click', () => refreshApprovalInbox(btn.dataset.approvalCheck || ''));
      });
      approvalInboxBodyEl.querySelectorAll('[data-approval-apply]').forEach((btn) => {
        btn.addEventListener('click', () => applyApproval(btn.dataset.approvalApply || ''));
      });
      if (focusId) {
        const controls = Array.from(approvalInboxBodyEl.querySelectorAll('[data-approval-id], [data-approval-check], [data-approval-apply], [data-approval-result]'));
        const sameControl = controls.find((node) => node.dataset[focusKey] === focusId && !node.disabled
          && (!focusedDecision || node.dataset.approvalDecision === focusedDecision));
        const result = controls.find((node) => node.dataset.approvalResult === focusId);
        (sameControl || result)?.focus({preventScroll: true});
      }
    }

    async function refreshApprovalInbox(approvalId = '') {
      if (!approvalInboxBodyEl) return;
      const request = ++reportApprovalRead;
      try {
        const data = await api('/api/manager-approvals');
        if (request !== reportApprovalRead) return;
        const approvals = window.ReportAdminApprovalState.checkedList(data);
        if (approvalId && !approvals.some((item) => item.approval_id === approvalId)) throw new Error('현재 승인 항목을 찾지 못했습니다.');
        if (approvalId) reportApprovalUnknown.delete(approvalId);
        reportApprovalReadError = '';
        const retained = new Set([...reportApprovalBusy, ...reportApprovalUnknown]);
        renderApprovalInbox({approvals: window.ReportAdminApprovalState.mergeList(reportApprovalItems, approvals, retained)});
        if (approvalId) setStatus('현재 서버의 결정·적용 결과를 확인했습니다. 이전 응답 단절의 원인을 단정하지 않으며 자동으로 재전송하지 않았습니다.', 'ok');
      } catch (err) {
        if (request !== reportApprovalRead) return;
        reportApprovalReadError = `승인함 조회 실패: ${err.message}`;
        renderApprovalInbox({approvals: reportApprovalItems});
      }
    }

    async function decideApproval(approvalId, decision) {
      const item = reportApprovalItems.find((row) => row.approval_id === approvalId);
      if (!item || item.status !== 'pending' || !['approved', 'rejected'].includes(decision)) return;
      return mutateApproval(approvalId, decision);
    }

    async function applyApproval(approvalId) {
      const item = reportApprovalItems.find((row) => row.approval_id === approvalId);
      if (!item || !window.ReportAdminApprovalState.applicationView(item).canApply) return;
      return mutateApproval(approvalId, 'apply');
    }

    async function mutateApproval(approvalId, operation) {
      if (reportApprovalBusy.has(approvalId) || reportApprovalUnknown.has(approvalId)) return;
      reportApprovalBusy.add(approvalId);
      reportApprovalRead++;
      renderApprovalInbox({approvals: reportApprovalItems});
      let item;
      try {
        const data = await api(operation === 'apply' ? '/api/manager-approvals/apply' : '/api/manager-approvals/decide', {
          method: 'POST',
          body: JSON.stringify(operation === 'apply' ? {approval_id: approvalId} : {approval_id: approvalId, decision: operation}),
        });
        item = window.ReportAdminApprovalState.checkedReply(data, approvalId, operation === 'apply' ? 'approved' : operation);
      } catch {
        reportApprovalBusy.delete(approvalId);
        reportApprovalUnknown.add(approvalId);
        renderApprovalInbox({approvals: reportApprovalItems});
        setStatus('승인함 처리 응답을 확인하지 못했습니다. 현재 결정·적용 결과 확인을 누르세요. 편집 본문은 유지했고 같은 요청을 다시 보내지 않았습니다.', 'err');
        return;
      }
      reportApprovalRead++;
      reportApprovalBusy.delete(approvalId);
      reportApprovalUnknown.delete(approvalId);
      reportApprovalItems = reportApprovalItems.map((row) => row.approval_id === approvalId ? item : row);
      renderApprovalInbox({approvals: reportApprovalItems});
      const application = window.ReportAdminApprovalState.applicationView(item);
      setStatus(`결정: ${window.ReportAdminApprovalState.decisionLabel(item)} · 사전 적용: ${application.text}`, item.application_result.status === 'failed' ? 'err' : 'ok');
      // Refresh only this independent projection. The report's unsaved Markdown,
      // selection, preview and dirty state belong to the editor lifecycle.
      await refreshApprovalInbox();
    }

    function renderManagerNoteActions(payload) {
      if (!managerNotePreviewEl) return;
      const draft = payload && payload.draft_actions || payload || {};
      const actions = Array.isArray(draft.actions) ? draft.actions : [];
      if (!actions.length) {
        managerNotePreviewEl.innerHTML = '<div class="meta-note">초안 카드가 없습니다. 메모를 조금 더 구체적으로 적어주세요.</div>';
        return;
      }
      const cards = actions.map((action) => {
        const type = String(action.action_type || '');
        const reviewNeeded = type === 'needs_agent_review' || String(action.risk || '') !== 'low';
        const label = action.manager_label || (reviewNeeded ? '직접 확인 필요' : '작업 초안');
        const confidence = action.confidence ? `신뢰도 ${escapeHtml(action.confidence)}` : '';
        const target = action.target ? `대상 ${escapeHtml(action.target)}` : '';
        const risk = action.risk ? `위험도 ${escapeHtml(action.risk)}` : '';
        const meta = [target, confidence, risk, '저장 안 됨', '확정 전 확인 필요'].filter(Boolean).join(' · ');
        return `
          <div class="manager-note-card ${reviewNeeded ? 'review-needed' : ''}">
            <strong>${escapeHtml(label)}</strong>
            <div>${escapeHtml(action.suggested_change || '')}</div>
            <div class="manager-note-meta">${meta}</div>
          </div>
        `;
      }).join('');
      managerNotePreviewEl.innerHTML = `<div class="manager-note-actions">${cards}</div>`;
    }

    async function previewManagerNoteActions() {
      if (!managerNoteInputEl) return;
      const note = String(managerNoteInputEl.value || '').trim();
      if (!note) {
        renderManagerNoteActions({ draft_actions: { actions: [] } });
        setStatus('메모를 입력해주세요.', 'warn');
        return;
      }
      setStatus('메모 초안 생성 중...');
      const data = await api('/api/manager-note-actions', {
        method: 'POST',
        body: JSON.stringify({
          base,
          note,
          selected_timecode: (cockpitTimecodeEl && cockpitTimecodeEl.value || '').trim(),
        }),
      });
      renderManagerNoteActions(data);
      openCockpitSection('managerNoteSection');
      updateCockpitSectionStates();
      const count = Number(data.draft_actions && data.draft_actions.action_count || 0);
      setStatus(`메모 초안 ${count}개를 만들었습니다. 아직 저장된 것은 없습니다.`, 'ok');
    }

    function renderAdminOnlyPanel(data) {
      const admin = data && data.admin_only || {};
      const summary = admin.summary || {};
      if (adminPanelSummaryEl) {
        adminPanelSummaryEl.innerHTML = `
          <div class="tool-summary">
            <div class="tool-stat"><span class="tool-label">세그먼트</span><span class="tool-value">${Number(summary.segment_count || 0)}</span></div>
            <div class="tool-stat"><span class="tool-label">컷 목록</span><span class="tool-value">${Number(summary.export_clip_count || 0)}</span></div>
            <div class="tool-stat"><span class="tool-label">챕터 준비</span><span class="tool-value">${escapeHtml(summary.youtube_chapter_readiness_status || 'none')}</span></div>
            <div class="tool-stat"><span class="tool-label">공개 제외</span><span class="tool-value">관리자만</span></div>
          </div>
        `;
      }
      renderContextVocabSuggestions(admin.streamer_vocab_suggestions || null);
      renderBundleAlignment(admin.bundle_alignment || null);
      renderBundleMembership(admin.bundle_membership || null);
      renderEditorTools(admin.edit_export_preview || null);
      renderSignalTimeline(
        admin.signal_timeline_workbench || null,
        admin.manager_scene_finder_html || '',
      );
      renderDiagnostics(admin);
      refreshApprovalInbox();
      updateCockpitSectionStates();
    }

    function renderBundleMembership(payload) {
      if (!bundleMembershipSectionEl || !bundleMembershipInputEl) return;
      const members = payload && Array.isArray(payload.current_members) ? payload.current_members : [];
      if (!payload || payload.kind !== 'bundle') {
        bundleMembershipSectionEl.style.display = 'none';
        bundleOriginalMembers = [];
        bundleMembershipPreview = null;
        return;
      }
      bundleMembershipSectionEl.style.display = '';
      bundleOriginalMembers = members.map(String);
      bundleMembershipInputEl.value = bundleOriginalMembers.join('\\n');
      bundleMembershipPreview = null;
      if (bundleMembershipSaveBtn) bundleMembershipSaveBtn.disabled = true;
      if (bundleMembershipPreviewEl) {
        bundleMembershipPreviewEl.textContent = members.length
          ? `현재 구성: ${members.join(', ')}`
          : '현재 번들 구성원이 없습니다. VOD 번호를 추가한 뒤 미리보기를 누르세요.';
      }
    }

    function bundleMembershipProposal() {
      if (!bundleMembershipInputEl) return [];
      return String(bundleMembershipInputEl.value || '')
        .split(/[\\n,]+/)
        .map(v => v.trim())
        .filter(Boolean);
    }

    function renderBundleMembershipPreview(payload) {
      if (!bundleMembershipPreviewEl) return;
      const summary = payload.summary || {};
      const affected = (payload.affected_artifacts || []).map(item => `<li>${escapeHtml(item.label || '')}: ${escapeHtml(item.status || '')}</li>`).join('');
      const warnings = (payload.warnings || []).map(w => `<li>${escapeHtml(w)}</li>`).join('');
      bundleMembershipPreviewEl.innerHTML = `
        <div><strong>${escapeHtml(payload.manager_label || '변경 미리보기')}</strong></div>
        <div>추가 ${Number(summary.added_count || 0)} · 제거 ${Number(summary.removed_count || 0)} · 순서변경 ${summary.reordered ? '있음' : '없음'}</div>
        <div class="muted">저장하면 기존 report.md/html은 삭제하지 않고 재생성 필요 상태로 표시합니다.</div>
        ${affected ? `<ul>${affected}</ul>` : ''}
        ${warnings ? `<div class="meta-note warn"><strong>확인 필요</strong><ul>${warnings}</ul></div>` : ''}
      `;
    }

    async function previewBundleMembership() {
      if (!base) throw new Error('base 파라미터가 없습니다.');
      const data = await api('/api/bundle-membership/preview', {
        method: 'POST',
        body: JSON.stringify({ base, video_nos: bundleMembershipProposal() }),
      });
      bundleMembershipPreview = data;
      renderBundleMembershipPreview(data);
      if (bundleMembershipSaveBtn) bundleMembershipSaveBtn.disabled = !data.ok || !data.changed;
      setStatus(data.changed ? '번들 구성 변경 미리보기를 만들었습니다.' : '변경된 구성이 없습니다.', 'ok');
    }

    async function saveBundleMembership() {
      if (!bundleMembershipPreview || !bundleMembershipPreview.ok || !bundleMembershipPreview.changed) {
        setStatus('먼저 변경 미리보기를 확인하세요.', 'warn');
        return;
      }
      const data = await api('/api/bundle-membership/save', {
        method: 'POST',
        body: JSON.stringify({ base, video_nos: bundleMembershipProposal(), confirm_preview: true }),
      });
      renderBundleMembershipPreview(data.preview || {});
      if (bundleMembershipSaveBtn) bundleMembershipSaveBtn.disabled = true;
      setStatus(data.message || '번들 구성을 저장했습니다.', 'ok');
      await loadReport();
    }

    function resetBundleMembership() {
      if (!bundleMembershipInputEl) return;
      bundleMembershipInputEl.value = bundleOriginalMembers.join('\\n');
      bundleMembershipPreview = null;
      if (bundleMembershipSaveBtn) bundleMembershipSaveBtn.disabled = true;
      if (bundleMembershipPreviewEl) bundleMembershipPreviewEl.textContent = '되돌렸습니다. 변경하려면 다시 미리보기를 누르세요.';
    }

    function renderBundleAlignment(payload) {
      if (!bundleAlignmentSectionEl || !bundleAlignmentBodyEl) return;
      const rows = payload && Array.isArray(payload.records) ? payload.records : [];
      if (!payload || payload.kind !== 'bundle' || rows.length === 0) {
        bundleAlignmentSectionEl.style.display = 'none';
        bundleAlignmentBodyEl.innerHTML = '<div class="meta-note">번들 정렬 근거가 없습니다.</div>';
        return;
      }
      bundleAlignmentSectionEl.style.display = '';
      const summary = payload.summary || {};
      const stats = `
        <div class="tool-summary">
          <div class="tool-stat"><span class="tool-label">검토 완료</span><span class="tool-value">${Number(summary.accepted_count || 0)}</span></div>
          <div class="tool-stat"><span class="tool-label">검토 필요</span><span class="tool-value">${Number(summary.needs_review_count || 0)}</span></div>
          <div class="tool-stat"><span class="tool-label">제외/실패</span><span class="tool-value">${Number(summary.rejected_or_failed_count || 0)}</span></div>
        </div>`;
      const cards = rows.map((row) => {
        const cls = row.review_status === 'manager_accepted' ? 'ok' : (row.failure_reason || row.review_status === 'manager_rejected' ? 'warn' : '');
        const range = row.source_range_label ? `<span class="mono">${escapeHtml(row.source_range_label)}</span>` : '<span class="muted">시간 미정</span>';
        const edited = row.edited_url
          ? `<a href="${escapeHtml(row.edited_url)}" target="_blank" rel="noopener">${escapeHtml(row.edited_video_id || 'YouTube')}</a>`
          : escapeHtml(row.edited_video_id || 'YouTube 후보');
        const source = row.source_url
          ? `<a href="${escapeHtml(row.source_url)}" target="_blank" rel="noopener">${escapeHtml(row.source_video_id || '')}</a>`
          : escapeHtml(row.source_video_id || '');
        return `
          <div class="tool-item ${cls}">
            <div><strong>${escapeHtml(row.manager_label || '')}</strong> · VOD ${escapeHtml(row.vod_index_label || '?')} ${source}</div>
            <div>${range} · ${edited}</div>
            <div class="muted">${escapeHtml(row.manager_note || row.failure_reason || row.match_method || '')}</div>
          </div>`;
      }).join('');
      bundleAlignmentBodyEl.innerHTML = stats + `<div class="tool-list">${cards}</div>`;
    }

    function renderAnchorInputs(candidates) {
      const rows = (candidates || []).map((item, index) => `
        <div class="anchor-row" data-index="${index}">
          <div class="anchor-head">
            <span class="mono">${escapeHtml(item.src_tc || '')}</span> · ${escapeHtml(item.label || '')}
            ${item.summary ? `<div>${escapeHtml(item.summary)}</div>` : ''}
          </div>
          <input type="text" class="anchor-input" data-src-tc="${escapeHtml(item.src_tc || '')}" data-label="${escapeHtml(item.label || '')}" placeholder="유튜브 시각 입력 (예: 01:24:57)" value="${escapeHtml(item.dst_tc || '')}">
        </div>
      `).join('');
      anchorGridEl.innerHTML = rows || '<div class="meta-note">추천 앵커가 없습니다.</div>';
    }

    function collectAnchorPayload() {
      return Array.from(document.querySelectorAll('.anchor-input'))
        .map((input) => ({
          src_tc: input.dataset.srcTc || '',
          dst_tc: (input.value || '').trim(),
          label: input.dataset.label || '',
        }))
        .filter((row) => row.src_tc && row.dst_tc);
    }

    function applyYoutubePayload(data) {
      youtubeState = data || {};
      if (data.youtube_url) youtubeUrlEl.value = data.youtube_url;
      renderAnchorInputs(data.anchor_candidates || []);
      youtubePreviewEl.textContent = data.text || '결과 없음';
      const parts = [];
      if (data.youtube_title) parts.push(`YouTube: ${data.youtube_title}`);
      if (data.youtube_duration_hms) parts.push(`길이 ${data.youtube_duration_hms}`);
      if (data.mode) parts.push(`매칭 ${data.mode}`);
      if (typeof data.confidence === 'number') parts.push(`신뢰도 ${data.confidence.toFixed(2)}`);
      if (typeof data.offset_sec === 'number') parts.push(`자동 보정 ${data.offset_sec}초`);
      youtubeMetaEl.textContent = parts.join(' · ') || '아직 생성되지 않음';
      updateCopyBtnState();
    }
