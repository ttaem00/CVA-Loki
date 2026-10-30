    let cafeState = null;
    let cafeDraftKey = '';
    let cafeRequestOutcome = null;
    let cafeActionPending = false;
    let cafeReading = false;
    let cafeDialogSnapshot = null;
    let cafeDialogOpener = null;
    const cafeDialog = document.getElementById('naverCafeConfirm');
    const cafeConfirmSubmit = document.getElementById('naverCafeConfirmSubmit');
    const cafeConfirmCancel = document.getElementById('naverCafeConfirmCancel');
    const cafeUnknownCheck = document.getElementById('naverCafeConfirmUnknownCheck');
    const cafeSubmissionEl = document.getElementById('naverCafeSubmission');
    const cafeReadCurrentBtn = document.getElementById('naverCafeReadCurrentBtn');
    const cafeNewIntentBtn = document.getElementById('naverCafeNewIntentBtn');
    function cafeContentKey() {
      return JSON.stringify([base, mdEditor.value, !ctEnabledCheckbox.checked]);
    }

    function setNaverCafeStatus(msg, kind = 'info') {
      if (!naverCafeStatusEl) return;
      naverCafeStatusEl.textContent = msg || '';
      naverCafeStatusEl.classList.remove('ok', 'warn', 'err');
      if (kind === 'ok') naverCafeStatusEl.classList.add('ok');
      else if (kind === 'warn') naverCafeStatusEl.classList.add('warn');
      else if (kind === 'err') naverCafeStatusEl.classList.add('err');
    }

    function setChzzkCommentStatus(msg, kind = 'info') {
      if (!chzzkCommentStatusEl) return;
      chzzkCommentStatusEl.textContent = msg || '';
      chzzkCommentStatusEl.classList.remove('ok', 'warn', 'err');
      if (kind === 'ok') chzzkCommentStatusEl.classList.add('ok');
      else if (kind === 'warn') chzzkCommentStatusEl.classList.add('warn');
      else if (kind === 'err') chzzkCommentStatusEl.classList.add('err');
    }

    function resetNaverCafeDraft(message = '본문이 바뀌었습니다. 현재 내용으로 초안을 다시 확인하세요.') {
      naverCafeDraft = null;
      cafeDraftKey = '';
      const summary = naverCafePreviewBoxEl?.querySelector('summary');
      if (summary && naverCafePreviewBoxEl.open) summary.textContent = '이전 카페 초안 · 본문 변경됨';
      setNaverCafeStatus(message, 'warn');
      refreshCafeEligibility();
    }

    function resetChzzkCommentDraft(message = '본문이 바뀌었습니다. CHZZK 댓글을 만들려면 다시 초안을 확인하거나 채우기 버튼을 누르세요.') {
      chzzkCommentDraft = null;
      if (chzzkCommentPreviewEl) {
        chzzkCommentPreviewEl.textContent = '';
        chzzkCommentPreviewEl.hidden = true;
      }
      setChzzkCommentStatus(message, 'warn');
    }

    function renderNaverCafeDestination(destination) {
      naverCafeDestination = destination || null;
      if (!naverCafeDestinationEl) return;
      const ok = !!(destination && destination.ok);
      naverCafeDestinationEl.classList.toggle('warn', !ok);
      const label = destination && destination.manager_label || '카페 게시 설정 없음';
      const message = destination && destination.manager_message || '카페 게시 설정에서 먼저 저장하세요.';
      const writeUrl = destination && destination.write_url || '';
      const editLink = '<a href="/naver-cafe-settings" target="_blank" rel="noopener">카페 게시 설정</a>';
      const link = writeUrl
        ? `<span>${escapeHtml(writeUrl)}</span>`
        : '<span>게시 URL 없음</span>';
      naverCafeDestinationEl.innerHTML = `
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(message)} · ${editLink}</span>
        ${link}
      `;
    }

    function htmlToPlainText(html) {
      const box = document.createElement('div');
      box.innerHTML = String(html || '');
      return (box.textContent || '')
        .replace(/\u00a0/g, ' ')
        .replace(/[ \t]+\n/g, '\n')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
    }

    async function copyHtmlFragment(html) {
      const text = htmlToPlainText(html);
      if (navigator.clipboard && window.ClipboardItem) {
        await navigator.clipboard.write([new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([text], { type: 'text/plain' }),
        })]);
        return;
      }
      if (!naverCafeClipboardSandboxEl) {
        await navigator.clipboard.writeText(text);
        return;
      }
      naverCafeClipboardSandboxEl.innerHTML = html;
      const range = document.createRange();
      range.selectNodeContents(naverCafeClipboardSandboxEl);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      const ok = document.execCommand('copy');
      selection.removeAllRanges();
      naverCafeClipboardSandboxEl.innerHTML = '';
      if (!ok) {
        await navigator.clipboard.writeText(text);
      }
    }

    async function copyPlainText(text) {
      const value = String(text || '').trim();
      if (!value) throw new Error('복사할 텍스트가 없습니다.');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(value);
        return;
      }
      const temp = document.createElement('textarea');
      temp.value = value;
      temp.setAttribute('readonly', '');
      temp.style.position = 'fixed';
      temp.style.left = '-10000px';
      document.body.appendChild(temp);
      temp.select();
      const ok = document.execCommand('copy');
      temp.remove();
      if (!ok) throw new Error('브라우저가 복사를 막았습니다.');
    }

    async function fetchNaverCafeDraft() {
      if (!base) throw new Error('base 파라미터가 없습니다.');
      const key = cafeContentKey();
      const data = await api('/api/naver-cafe-draft', {method:'POST', body:JSON.stringify({
        base, md:mdEditor.value, ct_disabled:!ctEnabledCheckbox.checked,
      })});
      if (key !== cafeContentKey()) throw new Error('응답을 기다리는 동안 본문이 바뀌었습니다. 현재 내용으로 다시 확인하세요.');
      if (data.ok !== true) throw new Error(data.manager_message || '카페 초안을 만들지 못했습니다.');
      naverCafeDraft = data;
      cafeDraftKey = key;
      if (data.destination) renderNaverCafeDestination(data.destination);
      if (data.cafe_state) applyNaverCafePostState(data.cafe_state.post, data.cafe_state);
      return data;
    }

    async function previewNaverCafeDraft() {
      const data = await fetchNaverCafeDraft();
      if (naverCafePreviewBodyEl) naverCafePreviewBodyEl.innerHTML = data.html || '표시할 카페 초안이 없습니다.';
      if (naverCafePreviewBoxEl) { naverCafePreviewBoxEl.open = true; naverCafePreviewBoxEl.querySelector('summary').textContent = '카페 글 초안 보기'; }
      setNaverCafeStatus('현재 편집안으로 카페 초안을 만들었습니다. 아래 내용과 게시 대상을 확인하세요.', 'ok');
      return data;
    }

    async function copyNaverCafeDraft() {
      const data = naverCafeDraft && cafeDraftKey === cafeContentKey() && naverCafeDraft.html ? naverCafeDraft : await fetchNaverCafeDraft();
      await copyHtmlFragment(data.html || '');
      setNaverCafeStatus('표와 초록 배경 없이 붙여넣는 카페용 HTML을 복사했습니다. 요약/클립 URL은 필요하면 카페 화면에서 해당 줄 뒤에 Enter를 눌러 링크 카드로 바꾸세요.', 'ok');
    }

    async function fetchChzzkCommentDraft() {
      if (!base) throw new Error('base 파라미터가 없습니다.');
      chzzkCommentDraft = await api('/api/chzzk-comment-draft', {
        method: 'POST',
        body: JSON.stringify({
          base,
          md: mdEditor.value,
          ct_disabled: !ctEnabledCheckbox.checked,
          ...(managerOutlineContext ? {
            structured_outline: collectManagerOutline(),
            editorial_review_packet: collectEditorialReviewPacket(),
          } : {}),
        }),
      });
      return chzzkCommentDraft;
    }

    function renderChzzkCommentDraft(data) {
      const drafts = Array.isArray(data && data.drafts) ? data.drafts : [];
      if (drafts.length && chzzkCommentDraftListEl) {
        if (chzzkCommentPreviewEl) {
          chzzkCommentPreviewEl.textContent = '';
          chzzkCommentPreviewEl.hidden = true;
        }
        chzzkCommentDraftListEl.innerHTML = drafts.map((draft, index) => `
          <div class="tool-row" data-chzzk-draft-card="${escapeHtml(draft.video_no || '')}">
            <strong>VOD ${Number(draft.vod_index || index + 1)}/${Number(draft.vod_count || drafts.length)} · ${escapeHtml(draft.title || draft.video_no || '')}</strong><br>
            <span class="mono">${escapeHtml(draft.video_no || '')}</span>
            <div class="tool-actions">
              <button type="button" class="ghost" data-copy-chzzk-draft="${index}">이 VOD 댓글 복사</button>
              <button type="button" class="ghost" data-open-chzzk-draft="${escapeHtml(draft.video_no || '')}">이 VOD 댓글창에 채우기</button>
            </div>
            <pre class="chzzk-comment-admin-preview">${escapeHtml(draft.text || draft.comment_text || '')}</pre>
          </div>
        `).join('');
        chzzkCommentDraftListEl.querySelectorAll('[data-copy-chzzk-draft]').forEach((btn) => {
          btn.addEventListener('click', async () => {
            const draft = drafts[Number(btn.dataset.copyChzzkDraft || 0)] || {};
            await copyPlainText(draft.text || draft.comment_text || '');
            setChzzkCommentStatus(`VOD ${draft.vod_index || ''} 댓글 텍스트를 복사했습니다.`, 'ok');
          });
        });
        chzzkCommentDraftListEl.querySelectorAll('[data-open-chzzk-draft]').forEach((btn) => {
          btn.addEventListener('click', () => openChzzkCommentWriter(btn.dataset.openChzzkDraft || ''));
        });
        setChzzkCommentStatus(data.manager_message || `CHZZK 댓글 초안 ${drafts.length}개를 만들었습니다.`, 'ok');
        return;
      }
      if (chzzkCommentDraftListEl) chzzkCommentDraftListEl.innerHTML = '';
      const text = data && (data.text || data.comment_text) || '';
      if (chzzkCommentPreviewEl) {
        chzzkCommentPreviewEl.textContent = text || '표시할 댓글 초안이 없습니다.';
        chzzkCommentPreviewEl.hidden = false;
      }
      setChzzkCommentStatus(text ? (data.manager_message || 'CHZZK 댓글 초안을 만들었습니다.') : 'CHZZK 댓글로 쓸 타임라인 텍스트가 없습니다.', text ? 'ok' : 'warn');
    }

    async function previewChzzkCommentDraft() {
      const data = await fetchChzzkCommentDraft();
      renderChzzkCommentDraft(data);
      return data;
    }

    async function copyChzzkCommentDraft() {
      const data = await fetchChzzkCommentDraft();
      await copyPlainText(data.text || data.comment_text || '');
      renderChzzkCommentDraft(data);
      setChzzkCommentStatus('댓글 텍스트를 복사했습니다. CHZZK 댓글창에 붙여넣은 뒤 등록하세요.', 'ok');
    }

    function refreshCafeEligibility() {
      const phase = cafeRequestOutcome?.phase || cafeState?.submission?.phase || 'idle';
      const uncertain = phase === 'unknown';
      const running = cafeActionPending || cafeState?.submission?.phase === 'running';
      const destinationOk = naverCafeDestination?.ok === true;
      const capability = cafeState?.capability;
      if (naverCafePostBtn) {
        naverCafePostBtn.textContent = naverCafePost?.article_url ? '반영 내용 확인' : '게시 내용 확인';
        naverCafePostBtn.disabled = !destinationOk || !cafeState?.ok || !capability?.can_publish || running || uncertain;
      }
      if (naverCafeFillDraftBtn) naverCafeFillDraftBtn.disabled = !destinationOk || !capability?.can_fill || running;
      if (cafeReadCurrentBtn) cafeReadCurrentBtn.disabled = cafeReading || cafeActionPending;
      if (cafeNewIntentBtn) {
        cafeNewIntentBtn.hidden = !uncertain;
        cafeNewIntentBtn.disabled = running || !cafeState?.ok || !capability?.can_publish || !destinationOk;
      }
      const capabilityEl = document.getElementById('naverCafeCapability');
      if (capabilityEl) capabilityEl.textContent = capability?.manager_message || '실행 가능 여부를 확인합니다.';
      if (cafeSubmissionEl) {
        const messages = {idle:'아직 확인된 게시 요청이 없습니다.', running:'확인한 내용을 제출하는 중입니다. 같은 요청을 다시 보내지 않습니다.',
          pending:'이전 제출 결과를 확인해야 합니다.', unknown:'제출 결과가 미확정입니다. 카페 글과 현재 기록을 확인하세요. 자동으로 다시 게시하지 않습니다.',
          confirmed:'이번 게시 요청의 완료 응답을 확인했습니다.', recovered:'현재 기록에서 확인한 내용의 반영을 확인했습니다. 다시 게시하지 않아도 됩니다.', not_started:'게시를 시작하지 않았습니다. 현재 편집 내용은 유지됩니다.'};
        cafeSubmissionEl.dataset.outcome = phase;
        cafeSubmissionEl.textContent = !cafeState?.ok && !cafeRequestOutcome ? (cafeState?.manager_message || '게시 기록을 아직 확인하지 못했습니다. 초안 도구는 계속 사용할 수 있습니다.')
          : ['confirmed','recovered'].includes(phase) && cafeRequestOutcome?.inputKey && cafeRequestOutcome.inputKey !== cafeContentKey()
            ? '앞서 확인한 내용은 게시됐습니다. 이후 편집 변경은 아직 카페에 반영하지 않았습니다.'
            : cafeRequestOutcome?.message || (phase === 'confirmed' && !cafeRequestOutcome ? '마지막으로 확인된 게시 기록이 있습니다. 새 요청의 완료 응답과 구분해 확인하세요.' : messages[phase] || messages.idle);
      }
      const link = document.getElementById('naverCafeLastPostLink');
      if (link) {
        let url = null;
        try { const candidate = new URL(naverCafePost?.article_url || ''); if (candidate.protocol === 'https:' && candidate.host === 'cafe.naver.com') url = candidate.href; } catch {}
        link.hidden = !url;
        if (url) link.href = url; else link.removeAttribute('href');
      }
      const history = Array.isArray(cafeState?.history) ? cafeState.history : [];
      const historyEl = document.getElementById('naverCafeHistory');
      if (historyEl) {
        historyEl.hidden = history.length === 0;
        const effectivePhase = item => item.phase === 'pending' ? (cafeState?.submission?.id === item.id && cafeState.submission.phase === 'running' ? 'running' : 'unknown') : item.phase;
        const unknownCount = history.filter(item => effectivePhase(item) === 'unknown').length;
        document.getElementById('naverCafeHistorySummary').textContent = `제출 기록 ${history.length}건${unknownCount ? ` · 미확정 ${unknownCount}건` : ''}`;
        document.getElementById('naverCafeHistoryList').innerHTML = history.map(item => {
          const labels = {unknown:'결과 미확정',confirmed:'완료 응답 확인',running:'제출 중',not_started:'제출 미시작'};
          const date = new Date(item.started_at);
          const when = Number.isNaN(date.getTime()) ? '시각 기록 없음' : date.toLocaleString('ko-KR');
          return `<li><strong>${escapeHtml(labels[effectivePhase(item)] || '확인 필요')}</strong> <span>${escapeHtml(when)}${item.prior_unknown_id ? ' · 별도 새 요청' : ''}</span></li>`;
        }).join('');
      }
    }

    function applyNaverCafePostState(post, state = null) {
      if (state) cafeState = state.ok === false ? {...state,history:cafeState?.history || []} : state;
      if (state?.ok === false) {
        const observation = document.getElementById('naverCafeObservation');
        if (observation) observation.textContent = state.manager_message || '현재 게시 기록을 읽지 못했습니다. 이전 결과는 유지합니다.';
      } else if (!['confirmed','recovered'].includes(cafeRequestOutcome?.phase) || state?.submission?.id === cafeRequestOutcome.id) naverCafePost = post || null;
      refreshCafeEligibility();
    }

    function matchingCafeReceipt(state, confirmation) {
      const attempt = state?.submission;
      return typeof confirmation === 'string' && /^[a-f0-9]{64}$/.test(confirmation)
        && state?.ok === true && state.base === base && state.post?.base === base
        && typeof state.post.article_url === 'string' && !!state.post.article_url
        && attempt?.phase === 'confirmed' && /^[a-f0-9]{32}$/.test(attempt.id || '')
        && attempt.confirmed_snapshot_sha256 === confirmation;
    }

    function validCafePublishAck(data, snapshot) {
      return data?.ok === true && data.schema_version === 'naver_cafe_publish_result.v1'
        && data.base === base && data.post?.base === base
        && data.post.article_url === data.cafe_state?.post?.article_url
        && matchingCafeReceipt(data.cafe_state,snapshot.confirmation);
    }

    async function readCurrentCafeState() {
      cafeReading = true; refreshCafeEligibility();
      try {
        const data = await api(`/api/naver-cafe/status?base=${encodeURIComponent(base)}`);
        if (!data.cafe_state || data.cafe_state.base !== base || typeof data.cafe_state.ok !== 'boolean') throw new Error('현재 기록 응답을 확인할 수 없습니다.');
        if (cafeRequestOutcome?.phase === 'unknown' && matchingCafeReceipt(data.cafe_state,cafeRequestOutcome.confirmation)) {
          cafeRequestOutcome = {...cafeRequestOutcome,phase:'recovered',id:data.cafe_state.submission.id,
            message:'현재 기록에서 확인한 내용의 반영을 확인했습니다. 다시 게시하지 않아도 됩니다.'};
        }
        applyNaverCafePostState(data.cafe_state.post, data.cafe_state);
        const observation = document.getElementById('naverCafeObservation');
        if (observation) observation.textContent = data.cafe_state.ok
          ? '현재 기록을 읽었습니다. 이 조회는 이전 요청의 완료 응답이나 미실행 증명이 아닙니다.'
          : data.cafe_state.manager_message;
      } catch (error) {
        const observation = document.getElementById('naverCafeObservation');
        if (observation) observation.textContent = `현재 기록을 읽지 못했습니다. 이전 결과와 편집 내용은 유지합니다. ${error.message}`;
      } finally { cafeReading = false; refreshCafeEligibility(); }
    }

    async function postToNaverCafe(separateIntent = false) {
      if (cafeActionPending || !cafeDialog) return;
      cafeActionPending = true; refreshCafeEligibility();
      try {
        const data = await fetchNaverCafeDraft();
        if (!data.confirmation || !data.cafe_state?.ok || !data.cafe_state.capability?.can_publish
            || data.cafe_state.submission?.phase === 'running') throw new Error(data.cafe_state?.capability?.manager_message || '현재 게시 상태를 확인한 뒤 진행하세요.');
        const uncertain = cafeRequestOutcome?.phase === 'unknown' || data.cafe_state.submission?.phase === 'unknown';
        if (uncertain && !separateIntent) throw new Error('이전 제출은 미확정입니다. 카페 글을 확인한 뒤 별도 새 요청을 준비하세요.');
        cafeDialogOpener = separateIntent ? cafeNewIntentBtn : naverCafePostBtn;
        cafeDialogSnapshot = {key:cafeContentKey(), md:mdEditor.value, ct_disabled:!ctEnabledCheckbox.checked,
          confirmation:data.confirmation, prior_unknown_id:uncertain ? data.cafe_state.submission?.id || '' : '', uncertain};
        document.getElementById('naverCafeConfirmDestination').textContent = data.destination?.manager_label || '현재 카페 설정';
        document.getElementById('naverCafeConfirmContent').textContent = data.title || '현재 편집안';
        document.getElementById('naverCafeConfirmPreview').open = false;
        document.getElementById('naverCafeConfirmPreviewBody').innerHTML = data.html || '초안 내용을 표시하지 못했습니다.';
        document.getElementById('naverCafeConfirmMode').textContent = data.posting_mode === 'modify' ? '확인된 기존 글 수정' : '확인한 카페에 새 글 게시';
        document.getElementById('naverCafeConfirmUnknown').hidden = !uncertain;
        if (cafeUnknownCheck) cafeUnknownCheck.checked = false;
        cafeConfirmSubmit.textContent = data.posting_mode === 'modify' ? '기존 글 반영' : '카페에 게시';
        cafeConfirmSubmit.disabled = uncertain;
        cafeDialog.showModal();
        cafeConfirmCancel.focus();
      } finally { cafeActionPending = false; refreshCafeEligibility(); }
    }

    async function submitConfirmedCafe() {
      const snapshot = cafeDialogSnapshot;
      if (!snapshot || cafeActionPending || (snapshot.uncertain && !cafeUnknownCheck.checked)) return;
      if (snapshot.key !== cafeContentKey()) { cafeDialog.close(); resetNaverCafeDraft('본문이 달라졌습니다. 현재 내용으로 다시 확인하세요.'); return; }
      const previousOutcome = cafeRequestOutcome;
      cafeDialog.close(); cafeActionPending = true;
      cafeRequestOutcome = {phase:'running'}; refreshCafeEligibility();
      try {
        await runBusyTask({title:'카페에 확인한 내용 반영 중', message:'현재 확인한 내용을 한 번 제출합니다. 결과가 불명확하면 자동 재전송하지 않습니다.',
          controls:[naverCafePostBtn,naverCafeFillDraftBtn,naverCafeCopyBtn,naverCafePreviewBtn,document.getElementById('saveBtn'),document.getElementById('previewBtn'),document.getElementById('reloadBtn'),publishBtn,ctEnabledCheckbox],
          returnFocusEl:cafeSubmissionEl}, async () => {
          try {
            const data = await api('/api/naver-cafe/publish', {method:'POST',body:JSON.stringify({base,md:snapshot.md,ct_disabled:snapshot.ct_disabled,
              confirmation:snapshot.confirmation,prior_unknown_id:snapshot.prior_unknown_id})});
            if (validCafePublishAck(data,snapshot)) {
              cafeRequestOutcome = {phase:'confirmed',id:data.cafe_state.submission.id,message:data.manager_message,inputKey:snapshot.key};
              applyNaverCafePostState(data.post,data.cafe_state);
              if (data.record_warning) setNaverCafeStatus(data.record_warning,'warn');
            } else {
              if (data.cafe_state?.base === base) cafeState = data.cafe_state;
              cafeRequestOutcome = data.base === base && data.status === 'not_started' ? previousOutcome?.phase === 'unknown' ? previousOutcome : {phase:'not_started',message:data.manager_message}
                : {phase:'unknown',message:data.manager_message || '게시 완료 응답을 확인하지 못했습니다. 현재 기록을 확인하세요.',confirmation:snapshot.confirmation,inputKey:snapshot.key};
            }
          } catch {
            cafeRequestOutcome = {phase:'unknown',message:'연결이 끊겨 제출 결과가 미확정입니다. 현재 기록과 카페 글을 확인하세요. 자동으로 다시 게시하지 않습니다.',confirmation:snapshot.confirmation,inputKey:snapshot.key};
          }
        });
      } finally { cafeActionPending = false; refreshCafeEligibility(); cafeSubmissionEl?.focus(); }
    }

    if (cafeReadCurrentBtn) cafeReadCurrentBtn.addEventListener('click', readCurrentCafeState);
    if (cafeNewIntentBtn) cafeNewIntentBtn.addEventListener('click', () => postToNaverCafe(true).catch(error => setNaverCafeStatus(error.message,'warn')));
    if (cafeConfirmSubmit) cafeConfirmSubmit.addEventListener('click', submitConfirmedCafe);
    if (cafeUnknownCheck) cafeUnknownCheck.addEventListener('change', () => { cafeConfirmSubmit.disabled = !cafeUnknownCheck.checked; });
    if (cafeConfirmCancel) cafeConfirmCancel.addEventListener('click', () => cafeDialog.close());
    if (cafeDialog) cafeDialog.addEventListener('close', () => { cafeDialogSnapshot = null; if (!cafeDialogOpener?.disabled) cafeDialogOpener?.focus(); });

    async function fillNaverCafeDraft() {
      if (!base || cafeActionPending) return;
      if (!naverCafeDestination?.ok || !cafeState?.capability?.can_fill) {
        setNaverCafeStatus(cafeState?.capability?.manager_message || '카페 게시 대상을 먼저 확인하세요.','warn'); return;
      }
      cafeActionPending = true; refreshCafeEligibility();
      const snapshot = {base,md:mdEditor.value,ct_disabled:!ctEnabledCheckbox.checked};
      try {
        await runBusyTask({title:'카페 작성창에 초안 채우는 중',message:'현재 본문을 채웁니다. 등록 버튼은 누르지 않습니다.',
          controls:[naverCafePostBtn,naverCafeFillDraftBtn,naverCafeCopyBtn,naverCafePreviewBtn,document.getElementById('saveBtn'),document.getElementById('previewBtn'),document.getElementById('reloadBtn'),publishBtn,ctEnabledCheckbox],
          returnFocusEl:naverCafeFillDraftBtn},async () => {
          const data = await api('/api/naver-cafe/fill-draft',{method:'POST',body:JSON.stringify(snapshot)});
          if (data.cafe_state) applyNaverCafePostState(data.cafe_state.post,data.cafe_state);
          setNaverCafeStatus(data.manager_message || '작성창의 초안 상태를 확인하세요.',data.ok === true ? 'ok' : 'warn');
        });
      } finally { cafeActionPending = false; refreshCafeEligibility(); }
    }

    async function openChzzkCommentWriter(targetVideoNo = '') {
      if (!base) throw new Error('base 파라미터가 없습니다.');
      const saveBtnEl = document.getElementById('saveBtn');
      const previewBtnEl = document.getElementById('previewBtn');
      const reloadBtnEl = document.getElementById('reloadBtn');
      return runBusyTask({
        title: 'CHZZK 댓글창 여는 중',
        message: '네이버 로그인 크롬에서 VOD를 열고 댓글 텍스트를 채웁니다. 등록 버튼은 누르지 않습니다.',
        controls: [chzzkCommentFillBtn, chzzkCommentCopyBtn, chzzkCommentPreviewBtn, saveBtnEl, previewBtnEl, reloadBtnEl, publishBtn, ctEnabledCheckbox],
        returnFocusEl: chzzkCommentFillBtn,
      }, async () => {
        setChzzkCommentStatus('CHZZK VOD를 열고 댓글창에 텍스트를 채우는 중입니다...');
        const data = await api('/api/chzzk-comment/open', {
          method: 'POST',
          body: JSON.stringify({
            base,
            md: mdEditor.value,
            ct_disabled: !ctEnabledCheckbox.checked,
            target_video_no: targetVideoNo || '',
          }),
        });
        const result = data.result || {};
        const inserted = !!result.comment_inserted;
        chzzkCommentDraft = {
          text: result.comment_text || (chzzkCommentDraft && chzzkCommentDraft.text) || '',
          vod_url: data.vod_url,
        };
        setChzzkCommentStatus(
          inserted
            ? 'CHZZK 댓글창에 초안을 채웠습니다. CHZZK 화면에서 확인한 뒤 등록하세요.'
            : 'CHZZK VOD를 열고 텍스트를 클립보드에 복사했습니다. 댓글창에 붙여넣은 뒤 등록하세요.',
          inserted ? 'ok' : 'warn'
        );
      });
    }

    async function previewYoutube() {
      if (!base) return;
      setStatus('유튜브 댓글 미리보기 생성 중...');
      const data = await api('/api/youtube-preview', {
        method: 'POST',
        body: JSON.stringify({
          base,
          md: mdEditor.value,
          youtube_url: youtubeUrlEl.value,
          anchors: collectAnchorPayload(),
        }),
      });
      applyYoutubePayload(data);
      setStatus('유튜브 댓글 미리보기 생성 완료', 'ok');
    }

    async function saveYoutubeAlignment() {
      if (!base) return;
      setStatus('유튜브 정렬 저장 중...');
      const data = await api('/api/youtube-save', {
        method: 'POST',
        body: JSON.stringify({
          base,
          md: mdEditor.value,
          youtube_url: youtubeUrlEl.value,
          anchors: collectAnchorPayload(),
        }),
      });
      applyYoutubePayload(data);
      setStatus(data.message || '유튜브 정렬 저장 완료', 'ok');
    }

    async function copyYoutubeText() {
      const text = (youtubeState && youtubeState.text || '').trim();
      if (!text) {
        setStatus('먼저 [유튜브 미리보기 생성] 을 눌러 텍스트를 만들어 주세요.', 'err');
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        setStatus('유튜브 댓글 텍스트 복사 완료', 'ok');
      } catch (err) {
        setStatus('브라우저가 복사를 막았습니다. 미리보기 영역을 직접 복사해 주세요.', 'err');
      }
    }
