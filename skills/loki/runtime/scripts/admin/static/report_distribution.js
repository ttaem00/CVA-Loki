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

    function resetNaverCafeDraft(message = '본문이 바뀌었습니다. 카페에 올리려면 다시 게시 버튼을 누르세요.') {
      naverCafeDraft = null;
      setNaverCafeStatus(message, 'warn');
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
      naverCafeDraft = await api('/api/naver-cafe-draft', {
        method: 'POST',
        body: JSON.stringify({
          base,
          md: mdEditor.value,
          ct_disabled: !ctEnabledCheckbox.checked,
        }),
      });
      return naverCafeDraft;
    }

    async function previewNaverCafeDraft() {
      const data = await fetchNaverCafeDraft();
      if (naverCafePreviewBodyEl) naverCafePreviewBodyEl.innerHTML = data.html || '표시할 카페 초안이 없습니다.';
      if (naverCafePreviewBoxEl) naverCafePreviewBoxEl.open = true;
      setNaverCafeStatus(data.manager_message || '카페 글 초안을 만들었습니다.', 'ok');
      return data;
    }

    async function copyNaverCafeDraft() {
      const data = naverCafeDraft && naverCafeDraft.html ? naverCafeDraft : await fetchNaverCafeDraft();
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

    function applyNaverCafePostState(post) {
      naverCafePost = post || null;
      const destinationOk = !naverCafeDestination || !!naverCafeDestination.ok;
      if (!naverCafePost || !naverCafePost.article_url) {
        if (naverCafePostBtn) {
          naverCafePostBtn.textContent = '네이버 카페에 게시하기';
          naverCafePostBtn.disabled = !destinationOk;
        }
        if (naverCafeFillDraftBtn) {
          naverCafeFillDraftBtn.disabled = !destinationOk;
        }
        if (!destinationOk) {
          setNaverCafeStatus(naverCafeDestination.manager_message || '카페 게시 설정이 없어 게시할 수 없습니다.', 'warn');
          return;
        }
        setNaverCafeStatus('현재 본문 기준으로 네이버 카페에 자동 게시할 수 있습니다.', 'info');
        return;
      }
      if (naverCafePostBtn) {
        naverCafePostBtn.textContent = '네이버 카페 글 수정/다시 게시';
        naverCafePostBtn.disabled = false;
      }
      if (naverCafeFillDraftBtn) {
        naverCafeFillDraftBtn.disabled = false;
      }
      setNaverCafeStatus('이미 등록된 글이 있습니다. 버튼을 누르면 현재 본문으로 수정하고, 기존 글이 삭제됐으면 새 글로 다시 게시합니다.', 'ok');
    }

    async function postToNaverCafe() {
      if (!base) throw new Error('base 파라미터가 없습니다.');
      if (naverCafeDestination && !naverCafeDestination.ok) {
        setNaverCafeStatus(naverCafeDestination.manager_message || '카페 게시 설정을 먼저 저장하세요.', 'warn');
        return;
      }
      const saveBtnEl = document.getElementById('saveBtn');
      const previewBtnEl = document.getElementById('previewBtn');
      const reloadBtnEl = document.getElementById('reloadBtn');
      return runBusyTask({
        title: naverCafePost && naverCafePost.article_url ? '네이버 카페 글 수정 중' : '네이버 카페 게시 중',
        message: '전용 크롬에서 현재 본문을 카페 글에 반영합니다. 기존 글이 삭제됐으면 새 글로 다시 게시합니다.',
        controls: [naverCafePostBtn, naverCafeFillDraftBtn, naverCafeCopyBtn, naverCafePreviewBtn, saveBtnEl, previewBtnEl, reloadBtnEl, publishBtn, ctEnabledCheckbox],
        returnFocusEl: naverCafePostBtn,
      }, async () => {
        setNaverCafeStatus('전용 크롬에서 현재 본문 기준으로 네이버 카페 글을 반영하는 중입니다...');
        const data = await api('/api/naver-cafe/publish', {
          method: 'POST',
          body: JSON.stringify({
            base,
            md: mdEditor.value,
            ct_disabled: !ctEnabledCheckbox.checked,
          }),
        });
        applyNaverCafePostState(data.post || null);
        const url = data.post && data.post.article_url;
        if (url) window.open(url, '_blank', 'noopener');
        setNaverCafeStatus(data.manager_message || '네이버 카페 글 반영 완료. 작성된 글을 열었습니다.', 'ok');
      });
    }

    async function fillNaverCafeDraft() {
      if (!base) throw new Error('base 파라미터가 없습니다.');
      if (naverCafeDestination && !naverCafeDestination.ok) {
        setNaverCafeStatus(naverCafeDestination.manager_message || '카페 게시 설정을 먼저 저장하세요.', 'warn');
        return;
      }
      const saveBtnEl = document.getElementById('saveBtn');
      const previewBtnEl = document.getElementById('previewBtn');
      const reloadBtnEl = document.getElementById('reloadBtn');
      return runBusyTask({
        title: '네이버 카페 글쓰기 창에 채우는 중',
        message: '전용 크롬에서 현재 본문을 SmartEditor 구조로 채웁니다. 등록 버튼은 누르지 않습니다.',
        controls: [naverCafePostBtn, naverCafeFillDraftBtn, naverCafeCopyBtn, naverCafePreviewBtn, saveBtnEl, previewBtnEl, reloadBtnEl, publishBtn, ctEnabledCheckbox],
        returnFocusEl: naverCafeFillDraftBtn,
      }, async () => {
        setNaverCafeStatus('전용 크롬에서 현재 본문 기준의 카페 글 초안을 채우는 중입니다...');
        const data = await api('/api/naver-cafe/fill-draft', {
          method: 'POST',
          body: JSON.stringify({
            base,
            md: mdEditor.value,
            ct_disabled: !ctEnabledCheckbox.checked,
          }),
        });
        setNaverCafeStatus(data.manager_message || '네이버 카페 글쓰기 창에 초안을 채웠습니다. 등록은 카페 화면에서 직접 확인한 뒤 누르세요.', 'ok');
      });
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
