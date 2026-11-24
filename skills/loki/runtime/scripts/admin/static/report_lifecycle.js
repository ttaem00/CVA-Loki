    let savedMd = '';
    let editorialReviewReadError = null;
    let reportLoadSequence = 0;
    // A structured Report Admin shows BroadcastMap Markdown in the editor,
    // but Publish must keep using the complete report Markdown loaded from /api/report.
    let normalReportMarkdown = '';
    function workspaceRevisionHistoryUrl() {
      const query = new URLSearchParams(location.search);
      query.set('history', '1');
      return '/report-workspace?' + query;
    }

    function encodeFrameParam(value) {
      return encodeURIComponent(value).replace(/'/g, '%27');
    }

    async function loadPreviewFrameHtml(url) {
      try {
        const response = await fetch(url, { cache: 'no-store' });
        if (!response.ok) throw new Error(`preview html ${response.status}`);
        previewFrame.removeAttribute('src');
        previewFrame.srcdoc = await response.text();
      } catch (_err) {
        previewFrame.removeAttribute('srcdoc');
        previewFrame.src = url;
      }
    }

    async function loadReportReviewTools(data, loadSequence) {
      try {
        const tools = await api(`/api/report?base=${encodeURIComponent(base)}&view=review-tools`);
        if (loadSequence !== reportLoadSequence) return;
        if (tools.base !== data.base || tools.manager_edit_revision !== data.manager_edit_revision) {
          if (signalTimelineBodyEl) signalTimelineBodyEl.textContent = '검토 자료를 읽는 동안 저장본이 바뀌었습니다. 현재 편집 내용은 보존됩니다. 원본 다시 불러오기로 최신 자료를 확인하세요.';
          return;
        }
        // Supplemental panels never replace the current Markdown, outline or
        // complete edit packet, including edits made while this loaded.
        renderAdminOnlyPanel(tools);
        if (signalDraftControlsEl) signalDraftControlsEl.hidden = false;
      } catch (_) {
        if (loadSequence !== reportLoadSequence) return;
        if (signalTimelineBodyEl) signalTimelineBodyEl.textContent = '장면 찾기·검토 자료를 불러오지 못했습니다. 본문 검토와 저장은 계속할 수 있습니다. 원본 다시 불러오기로 다시 확인하세요.';
      }
    }

    async function loadReport() {
      if (!base) throw new Error('base 파라미터가 없습니다.');
      if (new URLSearchParams(location.search).get('history') === '1') {
        location.replace(workspaceRevisionHistoryUrl());
        return false;
      }
      const loadSequence = ++reportLoadSequence;
      const previewReady = loadPreviewFrameHtml(`/api/saved-html?base=${encodeFrameParam(base)}&format=latest&t=${Date.now()}`);
      const slowTimer = setTimeout(() => {
        setStatus('서버 응답 대기 중… (리포트 수가 많으면 첫 로드는 느릴 수 있어요)', 'info');
      }, 5000);
      let data;
      try {
        data = await api(`/api/report?base=${encodeURIComponent(base)}&view=editor`);
      } finally {
        clearTimeout(slowTimer);
      }
      editorialReviewReadError = data.editorial_review_error || null;
      titleTextEl.textContent = data.title;
      document.title = `${data.title} — Report Admin`;

      const isBundle = data.kind === 'bundle';
      const isChzzk = !data.source_platform || data.source_platform === 'chzzk';
      // #448 source manifest 는 번들엔 적용 X — 링크 숨김.
      if (sourceManifestLinkEl) {
        sourceManifestLinkEl.style.display = isBundle ? 'none' : '';
      }
      if (workEvidenceLinkEl) {
        workEvidenceLinkEl.style.display = isBundle ? 'none' : '';
      }
      if (isBundle) {
        const vodList = (data.video_nos || []).map(v =>
          `<a href="/edit?base=${encodeURIComponent(v)}" target="_blank">${v}</a>`
        ).join(', ');
        metaEl.innerHTML = `📦 번들 · VOD ${data.vod_count || 0}개: ${vodList}${data.site_only ? ' · 공개만' : ''}`;
        document.querySelector('.youtube-section').style.display = 'none';
        if (naverCafePanelEl) naverCafePanelEl.style.display = '';
        if (chzzkCommentGroupEl) chzzkCommentGroupEl.style.display = '';
        renderAdminOnlyPanel(data);
        renderNaverCafeDestination(data.naver_cafe_destination || null);
        // 번들엔 단일 플레이어가 없음 — split view 비활성(VOD별 페이지에서 사용).
        splitVideoNo = '';
        if (splitViewToggleBtn) {
          splitViewToggleBtn.disabled = true;
          splitViewToggleBtn.title = '번들 보고서에는 플레이어가 없습니다. VOD별 편집 페이지에서 쓸 수 있어요.';
        }
        setSplitView(false, { silent: true });
      } else {
        const summaryPipelineLabel = data.summary_pipeline && data.summary_pipeline.label
          ? data.summary_pipeline.label
          : '방식 기록 없음 (과거 결과)';
        const isComposedReview = data.qg1_review_packet
          && data.qg1_review_packet.projection_mode === 'composed_review_draft';
        const summarySourceLabel = isComposedReview
          ? '편집 출처 ChatGPT 초안 + 기존 분석 보강'
          : `요약 방식 ${summaryPipelineLabel}`;
        metaEl.textContent = `영상번호 ${data.video_no} · ${data.channel} · ${summarySourceLabel}${data.site_only ? ' · 공개만 (자막 없음)' : ''}`;
        if (canonicalConflictBannerEl) {
          canonicalConflictBannerEl.hidden = !data.canonical_conflict;
          const conflictSource = data.canonical_source === 'site'
            ? '현재 공개 정본을 보여줍니다. 로컬 편집 파일 연결이 명확하지 않습니다.'
            : '현재 가장 최근 로컬 보고서를 보여줍니다. 어떤 파일이 정본인지 확인이 필요합니다.';
          canonicalConflictBannerEl.textContent = data.canonical_conflict
            ? `중복 보고서 ${Number(data.duplicate_artifact_count || 0)}개: ${conflictSource} 저장 전에 본문을 확인하세요.`
            : '';
        }
        document.querySelector('.youtube-section').style.display = '';
        if (naverCafePanelEl) naverCafePanelEl.style.display = '';
        if (chzzkCommentGroupEl) chzzkCommentGroupEl.style.display = isChzzk ? '' : 'none';
        applyYoutubePayload(data.youtube_alignment || {});
        renderAdminOnlyPanel(data);
        renderNaverCafeDestination(data.naver_cafe_destination || null);
        splitVideoNo = isChzzk ? String(data.video_no || '') : '';
        if (splitViewToggleBtn) {
          splitViewToggleBtn.disabled = !splitVideoNo;
          splitViewToggleBtn.title = !isChzzk
            ? '이 원본은 장면 도구의 원본 링크에서 확인합니다.'
            : '타임라인 카드를 누르면 위쪽 플레이어가 그 장면으로 이동합니다';
        }
        if (!isChzzk) setSplitView(false, { silent: true });
        let savedSplit = '0';
        try { savedSplit = localStorage.getItem('reportAdmin.splitView') || '0'; } catch (_) {}
        if (savedSplit === '1' && splitVideoNo) setSplitView(true, { silent: true });
      }

      mdEditor.value = data.md;
      reportLastConfirmed = data;
      savedMd = data.md;
      normalReportMarkdown = typeof data.md === 'string' ? data.md : '';
      setDirty(false);
      renderManagerOutline(data);
      restoreDetailSceneSelection();
      updateDetailSceneLinks();
      const ctDisabled = data.md.includes('<!-- ct_disabled -->');
      ctEnabledCheckbox.checked = !ctDisabled;
      ctToggleRowEl.classList.toggle('disabled', ctDisabled);
      if (isBundle) ctToggleRowEl.style.display = 'none';
      publicLinkEl.href = data.public_report_url || '#';
      publicLinkEl.style.visibility = data.public_report_url ? 'visible' : 'hidden';
      previewTitleEl.textContent = data.title;
      previewInfoEl.textContent = isBundle
        ? `📦 번들 · VOD ${data.vod_count || 0}개`
        : `${data.video_no} · ${data.channel}`;
      setStatus(
        data.kind === 'manager_outline'
          ? '방송 목차 구조를 불러왔어요. 수정한 뒤 [목차 수정본 저장]을 누르세요.'
          : '불러오기 완료 — 본문 수정 후 [미리보기 갱신] 또는 [저장]',
        'ok',
      );
      applyNaverCafePostState(data.naver_cafe_post || null);
      chzzkCommentDraft = null;
      if (chzzkCommentPreviewEl) {
        chzzkCommentPreviewEl.textContent = '';
        chzzkCommentPreviewEl.hidden = true;
      }
      if (chzzkCommentDraftListEl) chzzkCommentDraftListEl.innerHTML = '';
      setChzzkCommentStatus(
        !isChzzk
          ? '이 원본에는 CHZZK 댓글 도구를 사용하지 않습니다.'
          : isBundle
          ? '현재 본문 기준으로 VOD별 CHZZK 댓글 초안을 만들 수 있습니다.'
          : '현재 본문 기준으로 CHZZK 댓글 초안을 만들 수 있습니다.',
        'info'
      );
      if (data.chzzk_comment_text) renderChzzkCommentDraft({text: data.chzzk_comment_text});
      mdEditor.readOnly = !!editorialReviewReadError;
      ['saveBtn', 'previewBtn', 'publishBtn'].forEach(id => {
        const control = document.getElementById(id);
        if (control) control.disabled = !!editorialReviewReadError;
      });
      if (editorialReviewReadError) {
        if (managerOutlineEditorEl) managerOutlineEditorEl.querySelectorAll('button, input, textarea, select').forEach(control => { control.disabled = true; });
        setStatus(editorialReviewReadError.message, 'warn');
      }
      previewReady.catch(() => {});
      renderStalenessBanner(data.staleness || {});
      if (data.review_tools_pending) {
        if (signalTimelineBodyEl) signalTimelineBodyEl.textContent = '장면 찾기·검토 자료를 불러오고 있습니다. 본문과 목차는 먼저 확인할 수 있습니다.';
        if (signalDraftControlsEl) signalDraftControlsEl.hidden = true;
        requestAnimationFrame(() => { loadReportReviewTools(data, loadSequence); });
      } else if (signalDraftControlsEl) {
        signalDraftControlsEl.hidden = false;
      }
      loadCommentUsefulness().catch(() => {
        if (commentUsefulnessBodyEl) commentUsefulnessBodyEl.textContent = '검증 결과를 불러오지 못했습니다. 본문 검토와 저장은 계속할 수 있습니다.';
      });
      if (data.capabilities?.publish === false) { publishBtn.disabled = true; const reason = '이 로컬 모듈은 편집·미리보기·저장만 지원합니다. 공개 반영은 별도 게시 도구에서 진행하세요.'; publishBtn.title = reason; document.getElementById('publishHint').textContent = reason; }
      if (data.capabilities?.local_edit === true && data.capabilities?.publish === false) {
        if (sourceManifestLinkEl) { sourceManifestLinkEl.textContent = '원문 자막'; sourceManifestLinkEl.title = '이 보고서에 연결된 원본 자막을 확인합니다.'; }
        if (workEvidenceLinkEl) workEvidenceLinkEl.style.display = 'none';
      }
      if (data.capabilities?.cafe === false) { const reason = '이 로컬 모듈에는 카페·댓글 내보내기 연결이 없습니다. 편집 내용은 이 PC에 저장됩니다.';
        if (naverCafePanelEl) naverCafePanelEl.querySelectorAll('button').forEach(control => { control.disabled = true; control.title = reason; });
        if (chzzkCommentGroupEl) chzzkCommentGroupEl.querySelectorAll('button').forEach(control => { control.disabled = true; control.title = reason; });
        if (naverCafeDestinationEl) naverCafeDestinationEl.textContent = reason; setNaverCafeStatus(reason, 'info'); setChzzkCommentStatus(reason, 'info');
      }
    }

    async function loadCommentUsefulness() {
      if (!commentUsefulnessSectionEl || !commentUsefulnessBodyEl || !base) return;
      const data = await api(`/api/comment-usefulness-ablation?base=${encodeURIComponent(base)}`);
      const scope = data.scope || {};
      const comparison = data.comparison || {};
      const on = comparison.comments_on || {};
      const off = comparison.comments_off || {};
      const current = scope.current_report_in_holdout
        ? '<strong>이 보고서도 비교 표본에 포함됐습니다.</strong>'
        : '이 보고서는 아직 비교 표본에 포함되지 않았습니다.';
      commentUsefulnessBodyEl.innerHTML = `
        <strong>${escapeHtml(data.manager_label || '검증 중')}</strong>
        <p>${escapeHtml(data.manager_message || '')}</p>
        <ul>
          <li>비교한 VOD: ${Number(scope.grouped_holdout_count || 0)}개</li>
          <li>댓글 ON: 최종본에 남은 장면 ${Number(on.retained_contribution_count || 0)}개 · 삭제된 장면 ${Number(on.deleted_false_positive_count || 0)}개 · 수동 수정 ${Number(on.manual_actions || 0)}건</li>
          <li>댓글 OFF: 다시 추가해야 할 장면 ${Number(off.missed_retained_scene_count || 0)}개 · 수동 수정 ${Number(off.manual_actions_to_restore_manager_truth || 0)}건</li>
        </ul>
        <p>${current} 기본 요약·랭킹·학습·공개 동작은 바꾸지 않았습니다.</p>`;
    }

    function renderStalenessBanner(staleness) {
      let el = document.getElementById('staleness-banner');
      if (!el) {
        el = document.createElement('div');
        el.id = 'staleness-banner';
        el.style.cssText = 'padding:8px 12px;border-radius:6px;margin-bottom:8px;font-size:13px;display:none;';
        const header = document.querySelector('.sidebar .topbar') || document.querySelector('.sidebar');
        if (header) header.appendChild(el);
        else document.body.prepend(el);
      }
      if (!staleness.stale) { el.style.display = 'none'; return; }
      el.style.display = 'block';
      el.style.background = '#fff3cd';
      el.style.border = '1px solid #ffc107';
      el.style.color = '#664d03';
      const src = staleness.source || '';
      const at = staleness.at ? new Date(staleness.at).toLocaleString('ko-KR') : '';
      let msg;
      if (src.startsWith('bundle:')) {
        msg = `⚠️ 이 VOD가 포함된 번들(${src.replace('bundle:', '')})이 수정되었습니다.`;
      } else if (src.startsWith('vod:')) {
        msg = `⚠️ 이 번들에 포함된 개별 VOD(${src.replace('vod:', '')})가 수정되었습니다.`;
      } else {
        msg = '⚠️ 연동된 리포트가 수정되었습니다.';
      }
      if (at) msg += ` (${at})`;
      msg += ' — 내용을 확인하고 필요하면 이 리포트도 수정해주세요.';
      el.textContent = msg;
    }

    async function previewCurrent() {
      if (editorialReviewReadError) { setStatus(editorialReviewReadError.message, 'warn'); return; }
      if (!base) return;
      if (managerOutlineBusy) return;
      window.clearTimeout(managerOutlineSyncTimer);
      const previewSequence = ++managerOutlineSyncSequence;
      setStatus('미리보기 생성 중...');
      const data = await api('/api/preview', {
        method: 'POST',
        body: JSON.stringify({
          base,
          md: mdEditor.value,
          ct_disabled: !ctEnabledCheckbox.checked,
          structured_outline: collectManagerOutline(),
          editorial_review_packet: collectEditorialReviewPacket(),
        }),
      });
      if (previewSequence !== managerOutlineSyncSequence) return;
      previewFrame.srcdoc = data.html;
      if (data.structured_outline && managerOutlineContext) {
        managerOutlineMarkdownPending = false;
        managerOutlineState = JSON.parse(JSON.stringify(data.structured_outline));
        if (data.qg1_review_packet) managerEditorialPacket = JSON.parse(JSON.stringify(data.qg1_review_packet));
        hydrateManagerPointStories();
        managerOutlineState.highlights = managerEditorialHighlights(managerEditorialPacket, managerOutlineState.points || []);
        renderManagerOutlineNavigator(); renderManagerOutlineInspector(); renderManagerOutlineTimeline();
      }
      previewTitleEl.textContent = data.title || titleTextEl.textContent;
      if (typeof data.md === 'string') {
        const beforeMd = mdEditor.value;
        mdEditor.value = data.md;
        setDirty(mdEditor.value !== savedMd);
        if (beforeMd !== data.md) resetNaverCafeDraft('미리보기에서 본문이 정리됐습니다. 카페에 올리려면 다시 게시 버튼을 누르세요.');
        if (beforeMd !== data.md) resetChzzkCommentDraft('미리보기에서 본문이 정리됐습니다. CHZZK 댓글을 만들려면 다시 초안을 확인하거나 채우기 버튼을 누르세요.');
      }
      if (data.marker_warnings && data.marker_warnings.length) {
        setStatus('⚠️ 마커 경고: ' + data.marker_warnings.join('; '), 'warn');
      } else {
        setStatus('미리보기 갱신 완료', 'ok');
      }
      renderAdminOnlyPanel(data);
    }

    async function saveCurrent() {
      if (editorialReviewReadError) { setStatus(editorialReviewReadError.message, 'warn'); return; }
      if (managerOutlineContext) return saveManagerOutline();
      return saveOrPublish(false);
    }

    async function publishCurrent() {
      assertReportSaveReady();
      if (editorialReviewReadError) { setStatus(editorialReviewReadError.message, 'warn'); return; }
      if (!base || publishPreparing || activePublishOperationId) return;
      const confirmed = window.confirm(
        '현재 수정본을 공개 사이트에 반영할까요?\n\n'
        + '저장과 다른 공개 행동입니다. 확인 후에도 서버의 관리자 권한과 공개 승인 검사를 통과해야 실행됩니다.'
      );
      if (!confirmed) {
        setStatus('공개 반영을 취소했습니다. 로컬 저장본과 공개본은 그대로입니다.', 'warn');
        return;
      }
      publishPreparing = true;
      showPublishBusy('공개할 내용을 준비하고 있습니다.');
      try {
        if (managerOutlineContext && managerOutlineContext.normalReport) {
          // Commit the BroadcastMap first, then publish the complete report body.
          // The visible editor contains only the wiki-style structure projection.
          if (!await saveManagerOutline()) return;
          const currentReport = await api(`/api/report?base=${encodeURIComponent(base)}`);
          normalReportMarkdown = typeof currentReport.md === 'string' ? currentReport.md : normalReportMarkdown;
          return await saveOrPublish(true, normalReportMarkdown);
        }
        return await saveOrPublish(true);
      } finally {
        publishPreparing = false;
        if (!activePublishOperationId) hidePublishBusy();
      }
    }


    let activePublishOperationId = '';
    let publishOperationPollTimer = 0;
    let publishConflictControlStates = null;
    let publishPreparing = false;
    let publishSubmitting = false;
    let publishBusyBackground = null;
    let publishBusyReturnFocus = null;
    let publishBusyDismissed = false;
    const publishBusyOverlayEl = document.getElementById('busyOverlay');
    const publishBusyElapsedEl = document.getElementById('publishBusyElapsed');
    const publishBusyNoteEl = document.getElementById('publishBusyNote');
    const publishBusyReturnEl = document.getElementById('publishBusyReturn');

    // One visual owner over the existing durable operation and its existing poll.
    function showPublishBusy(message, elapsedSeconds = null, reconnecting = false) {
      if (publishBusyDismissed || !publishBusyOverlayEl) return;
      const opening = !publishBusyBackground;
      if (opening) {
        publishBusyReturnFocus = document.activeElement;
        publishBusyBackground = [...document.body.children]
          .filter((el) => el !== publishBusyOverlayEl && !['SCRIPT', 'STYLE'].includes(el.tagName))
          .map((el) => [el, el.inert, el.getAttribute('aria-busy')]);
        publishBusyBackground.forEach(([el]) => { el.inert = true; el.setAttribute('aria-busy', 'true'); });
      }
      setBusy(true, reconnecting ? '진행 상태에 다시 연결 중' : '공개 사이트에 반영 중', message);
      // Announce the dialog, rather than marking its live region busy as well.
      document.body.setAttribute('aria-busy', 'false');
      document.body.classList.add('publish-busy');
      if (publishBusyElapsedEl) {
        publishBusyElapsedEl.hidden = elapsedSeconds === null;
        publishBusyElapsedEl.textContent = elapsedSeconds === null ? '' : `경과 시간 ${publishElapsedLabel(elapsedSeconds)}`;
      }
      if (publishBusyNoteEl) publishBusyNoteEl.hidden = !activePublishOperationId;
      if (publishBusyReturnEl) publishBusyReturnEl.hidden = !reconnecting;
      if (opening) publishBusyOverlayEl.focus({ preventScroll: true });
    }

    function hidePublishBusy() {
      if (!publishBusyBackground) return;
      setBusy(false);
      document.body.classList.remove('publish-busy');
      publishBusyBackground.forEach(([el, inert, busy]) => {
        el.inert = inert;
        if (busy === null) el.removeAttribute('aria-busy'); else el.setAttribute('aria-busy', busy);
      });
      publishBusyBackground = null;
      [publishBusyElapsedEl, publishBusyNoteEl, publishBusyReturnEl].forEach((el) => { if (el) el.hidden = true; });
      const target = publishBusyReturnFocus;
      publishBusyReturnFocus = null;
      if (target && target.isConnected && !target.disabled && !target.closest('[inert]')) target.focus({ preventScroll: true });
    }

    if (publishBusyReturnEl) publishBusyReturnEl.addEventListener('click', () => {
      publishBusyDismissed = true;
      hidePublishBusy();
      setStatus('진행 상태에 다시 연결하고 있습니다. 공개 작업은 서버에서 계속될 수 있으며, 같은 요청을 다시 보내지 않습니다.', 'warn');
    });
    if (publishBusyOverlayEl) publishBusyOverlayEl.addEventListener('keydown', (event) => {
      if (!publishBusyBackground || event.key !== 'Tab') return;
      event.preventDefault();
      const target = publishBusyReturnEl && !publishBusyReturnEl.hidden ? publishBusyReturnEl : publishBusyOverlayEl;
      target.focus({ preventScroll: true });
    });

    function setPublishConflictControlsBusy(busy) {
      if (busy && !publishConflictControlStates) publishConflictControlStates = {};
      ['saveBtn', 'previewBtn', 'reloadBtn', 'publishBtn'].forEach((id) => {
        const control = document.getElementById(id);
        if (!control) return;
        if (busy) {
          if (!Object.prototype.hasOwnProperty.call(publishConflictControlStates, id)) {
            publishConflictControlStates[id] = Boolean(control.disabled);
          }
          control.disabled = true;
        } else if (
          publishConflictControlStates
          && Object.prototype.hasOwnProperty.call(publishConflictControlStates, id)
        ) {
          control.disabled = publishConflictControlStates[id];
        }
      });
      if (!busy) publishConflictControlStates = null;
    }

    function publishElapsedLabel(seconds) {
      const total = Math.max(0, Number(seconds) || 0);
      const minutes = Math.floor(total / 60);
      const rest = Math.floor(total % 60);
      return minutes ? `${minutes}분 ${rest}초` : `${rest}초`;
    }

    function publishOperationKind(operation) {
      if (operation.status === 'succeeded') return 'ok';
      if (operation.status === 'partial' || operation.status === 'interrupted') return 'warn';
      if (operation.status === 'blocked' || operation.status === 'failed') return 'err';
      return 'info';
    }

    async function handlePublishOperation(operation, publishedMd) {
      if (!operation || !operation.operation_id) throw new Error('공개 작업 상태를 확인하지 못했습니다. 같은 요청을 다시 보내기 전에 상태를 확인하세요.');
      activePublishOperationId = operation.operation_id;
      const elapsed = publishElapsedLabel(operation.elapsed_seconds);
      const prefix = operation.terminal
        ? ''
        : `공개 반영 중 · ${elapsed} · `;
      setStatus(prefix + (operation.manager_message || '공개 작업 상태를 확인하고 있습니다.'), publishOperationKind(operation));
      if (!operation.terminal) {
        setPublishConflictControlsBusy(true);
        showPublishBusy(operation.manager_message || '공개 작업 상태를 확인하고 있습니다.', operation.elapsed_seconds);
        window.clearTimeout(publishOperationPollTimer);
        const pollPublishOperation = async () => {
          try {
            const next = await api(`/api/publish-operation?operation_id=${encodeURIComponent(activePublishOperationId)}`);
            await handlePublishOperation(next, publishedMd);
          } catch (err) {
            setStatus(`공개 작업은 서버에서 계속될 수 있습니다. 상태 연결을 다시 시도합니다. (${err.message})`, 'warn');
            showPublishBusy('서버 연결을 다시 확인하고 있습니다. 같은 공개 요청을 다시 보내지 않습니다.', operation.elapsed_seconds, true);
            publishOperationPollTimer = window.setTimeout(pollPublishOperation, 2500);
          }
        };
        publishOperationPollTimer = window.setTimeout(pollPublishOperation, 1000);
        return;
      }
      activePublishOperationId = '';
      setPublishConflictControlsBusy(false);
      hidePublishBusy();
      publishBusyDismissed = false;
      if (operation.status === 'succeeded' || operation.status === 'partial') {
        if (typeof publishedMd === 'string' && mdEditor.value === publishedMd) {
          savedMd = publishedMd;
          setDirty(false);
        }
        if (managerOutlineContext && managerOutlineContext.normalReport && typeof publishedMd === 'string') {
          normalReportMarkdown = publishedMd;
        }
      }
    }

    async function resumeActivePublishOperation() {
      if (!base || activePublishOperationId) return;
      try {
        const operation = await api(`/api/publish-operation?base=${encodeURIComponent(base)}`);
        const updatedAtMs = Date.parse(operation && operation.updated_at ? operation.updated_at : '');
        const recentRetryableStop = Boolean(
          operation && operation.terminal && operation.retryable
          && Number.isFinite(updatedAtMs) && Date.now() - updatedAtMs < 6 * 60 * 60 * 1000
        );
        if (operation && (
          operation.status === 'queued' || operation.status === 'running' || recentRetryableStop
        )) {
          await handlePublishOperation(operation, null);
        }
      } catch (_) {
        // No operation is the normal state for reports that were never published.
      }
    }

    async function startAsyncPublish(transactionMd, markdownOverride) {
      if (!base || activePublishOperationId || publishSubmitting) return;
      publishSubmitting = true;
      setPublishConflictControlsBusy(true);
      showPublishBusy('공개할 내용과 승인 범위를 확인하고 있습니다.');
      setStatus('정확한 공개 후보와 승인 범위를 확인하고 있습니다...');
      try {
        const approvalPayload = {
          base,
          md: transactionMd,
          ct_disabled: !ctEnabledCheckbox.checked,
          actor_kind: 'human_manager',
          test_mode: false,
        };
        let approval = await api('/api/publish-approval', {
          method: 'POST',
          body: JSON.stringify(approvalPayload),
        });
        if (approval.requires_destructive_confirmation) {
          const change = approval.destructive_change || {};
          const confirmed = window.confirm(
            '본문 대부분이 사라집니다.\n\n'
            + '현재 ' + Number(change.before_chars || 0).toLocaleString() + '자 → 공개 후보 '
            + Number(change.after_chars || 0).toLocaleString() + '자\n'
            + Number(change.removed_chars || 0).toLocaleString() + '자 삭제 · '
            + Number(change.remaining_percent || 0).toLocaleString() + '% 남음'
            + '\n\n확인하면 이 정확한 변경에만 유효한 공개 승인 영수증을 만듭니다.'
          );
          if (!confirmed) {
            setStatus('공개 반영을 취소했습니다. 저장본과 공개본은 그대로입니다.', 'warn');
            return;
          }
          approval = await api('/api/publish-approval', {
            method: 'POST',
            body: JSON.stringify({
              ...approvalPayload,
              destructive_confirmation_token: approval.confirmation_token,
            }),
          });
        }
        if (approval.blocked || !approval.approval_receipt) {
          setStatus('🚫 공개 승인 차단: ' + (approval.message || '승인 영수증을 만들 수 없습니다.'), 'err');
          return;
        }
        if (typeof approval.md === 'string') {
          transactionMd = approval.md;
          if (typeof markdownOverride !== 'string') mdEditor.value = approval.md;
        }
        const operation = await api('/api/publish-operation', {
          method: 'POST',
          body: JSON.stringify({
            base,
            md: transactionMd,
            publish: true,
            ct_disabled: !ctEnabledCheckbox.checked,
            actor_kind: 'human_manager',
            approval_receipt: approval.approval_receipt,
            approved_html: approval.html || '',
            test_mode: false,
            idempotency_key: approval.idempotency_key || '',
          }),
        });
        setStatus('공개 요청을 접수했습니다. 이 페이지를 다시 열어도 진행 상태가 이어집니다.', 'ok');
        await handlePublishOperation(operation, transactionMd);
      } finally {
        publishSubmitting = false;
        if (!activePublishOperationId) {
          setPublishConflictControlsBusy(false);
          hidePublishBusy();
        }
      }
    }

    async function saveOrPublish(willPublish, markdownOverride = null) {
      if (!base) return;
      assertReportSaveReady();
      if (activePublishOperationId) {
        setStatus('공개 반영 중입니다. 완료 후 내용을 편집하거나 저장할 수 있습니다.', 'warn');
        return;
      }
      let transactionMd = typeof markdownOverride === 'string' ? markdownOverride : mdEditor.value;
      if (willPublish) return startAsyncPublish(transactionMd, markdownOverride);
      const report = reportLastConfirmed || {};
      const attempt = {kind: 'save', structured: false, base, reportKind: report.kind,
        videoNo: String(report.video_no || ''), bundleId: String(report.bundle_id || ''), md: transactionMd,
        beforeMd: String(report.md || '')};
      const saveBtnEl = document.getElementById('saveBtn');
      const previewBtnEl = document.getElementById('previewBtn');
      const reloadBtnEl = document.getElementById('reloadBtn');
      return runBusyTask({
        title: willPublish ? '저장하고 공개 반영 중' : '리포트 저장 중',
        message: willPublish
          ? '수정본을 이 PC에 저장하고 공개 사이트까지 갱신합니다. 완료될 때까지 다른 편집 버튼을 누르지 말고 기다려 주세요.'
          : '수정한 리포트를 이 PC에 저장하고 미리보기를 갱신합니다. 완료될 때까지 기다려 주세요.',
        controls: [saveBtnEl, previewBtnEl, reloadBtnEl, mdEditor, publishBtn, ctEnabledCheckbox, naverCafePostBtn, naverCafeFillDraftBtn, naverCafeCopyBtn, naverCafePreviewBtn, chzzkCommentFillBtn, chzzkCommentCopyBtn, chzzkCommentPreviewBtn],
        returnFocusEl: saveBtnEl,
      }, async () => {
        reportSavePending = true;
        let committed = false;
        try {
        setStatus(willPublish ? '저장 및 공개 반영 중...' : '저장 중...');
        let approval = null;
        if (willPublish) {
          const approvalPayload = {
            base,
            md: transactionMd,
            ct_disabled: !ctEnabledCheckbox.checked,
            actor_kind: 'human_manager',
            test_mode: false,
          };
          approval = await api('/api/publish-approval', {
            method: 'POST',
            body: JSON.stringify(approvalPayload),
          });
          if (approval.requires_destructive_confirmation) {
            const change = approval.destructive_change || {};
            const confirmed = window.confirm(
              '본문 대부분이 사라집니다.\n\n'
              + '현재 ' + Number(change.before_chars || 0).toLocaleString() + '자 → 공개 후보 '
              + Number(change.after_chars || 0).toLocaleString() + '자\n'
              + Number(change.removed_chars || 0).toLocaleString() + '자 삭제 · '
              + Number(change.remaining_percent || 0).toLocaleString() + '% 남음'
              + '\n\n확인하면 이 정확한 변경에만 유효한 공개 승인 영수증을 만듭니다.'
            );
            if (!confirmed) {
              setStatus('공개 반영을 취소했습니다. 저장본과 공개본은 그대로입니다.', 'warn');
              return;
            }
            approval = await api('/api/publish-approval', {
              method: 'POST',
              body: JSON.stringify({
                ...approvalPayload,
                destructive_confirmation_token: approval.confirmation_token,
              }),
            });
          }
          if (approval.blocked || !approval.approval_receipt) {
            setStatus('🚫 공개 승인 차단: ' + (approval.message || '승인 영수증을 만들 수 없습니다.'), 'err');
            return;
          }
          if (typeof approval.md === 'string') {
            transactionMd = approval.md;
            if (typeof markdownOverride !== 'string') mdEditor.value = approval.md;
          }
        }
        const savePayload = {
          base,
          md: transactionMd,
          publish: willPublish,
          ct_disabled: !ctEnabledCheckbox.checked,
          actor_kind: willPublish ? 'human_manager' : 'unknown',
          approval_receipt: approval ? approval.approval_receipt : null,
          approved_html: approval ? approval.html : '',
          test_mode: false,
          idempotency_key: approval ? approval.idempotency_key : '',
        };
        let data = await api('/api/save', {
          method: 'POST',
          body: JSON.stringify(savePayload),
        });
        if (data.requires_destructive_confirmation) {
          const change = data.destructive_change || {};
          const publishWarning = willPublish
            ? '\n\n확인하면 공개 사이트에도 이 축소된 본문이 반영됩니다.'
            : '\n\n확인하면 이 PC의 저장본이 축소됩니다. 공개 사이트는 아직 바뀌지 않습니다.';
          const confirmed = await confirmReportSaveAction('본문 축소 변경 확인',
            '본문 대부분이 사라집니다.\n\n'
            + '현재 ' + Number(change.before_chars || 0).toLocaleString() + '자 → 저장 후 '
            + Number(change.after_chars || 0).toLocaleString() + '자\n'
            + Number(change.removed_chars || 0).toLocaleString() + '자 삭제 · '
            + Number(change.remaining_percent || 0).toLocaleString() + '% 남음'
            + publishWarning
            + '\n\n취소하면 원본과 화면의 편집 내용을 유지합니다.', '검토한 로컬 변경 저장'
          );
          if (!confirmed) {
            setStatus('저장을 취소했습니다. 원본과 화면의 편집 내용은 그대로입니다.', 'warn');
            return;
          }
          setStatus('명시적 확인을 받아 저장을 다시 검증하는 중...');
          data = await api('/api/save', {
            method: 'POST',
            body: JSON.stringify({
              ...savePayload,
              destructive_confirmation_token: data.confirmation_token,
            }),
          });
          if (data.requires_destructive_confirmation) {
            setStatus('안전 확인이 만료되었거나 본문이 달라졌습니다. 원본은 그대로입니다. 다시 저장해 주세요.', 'err');
            return;
          }
        }
        if (data.blocked) {
          setStatus('🚫 저장 차단: ' + (data.message || (data.marker_warnings || []).join('; ')), 'err');
          return;
        }
        committed = validReportSaveAck(data, attempt);
        if (!committed) throw Error('save_revision_ack_missing');
        await loadPreviewFrameHtml(`/api/saved-html?base=${encodeFrameParam(base)}&format=latest&t=${Date.now()}`);
        previewTitleEl.textContent = data.title || titleTextEl.textContent;
        if (typeof data.md === 'string') {
          if (typeof markdownOverride === 'string') normalReportMarkdown = data.md;
          else mdEditor.value = data.md;
        }
        savedMd = mdEditor.value;
        reportLastConfirmed = {...reportLastConfirmed, md: data.md};
        setDirty(false);
        if (!naverCafePost || !naverCafePost.article_url) {
          setNaverCafeStatus('저장된 본문 기준으로 네이버 카페에 자동 게시할 수 있습니다.', 'info');
        }
        setChzzkCommentStatus('저장된 본문 기준으로 CHZZK 댓글 초안을 만들 수 있습니다.', 'info');
        if (data.marker_warnings && data.marker_warnings.length) {
          setStatus((data.message || '저장 완료') + ' ⚠️ ' + data.marker_warnings.join('; '), 'warn');
        } else {
          setStatus(data.message || '저장 완료', 'ok');
        }
        renderAdminOnlyPanel(data);
        return true;
        } catch (error) {
          if (committed) {
            reportSaveOutcome = {...attempt, checked: null, confirmed: true};
            setStatus('로컬 수정본은 저장됐습니다. 화면 갱신을 마치지 못했으며 저장을 반복하지 않습니다. 저장 결과 확인으로 현재 저장본을 읽으세요.', 'warn');
          } else holdReportSaveOutcome(attempt, error);
          throw error;
        } finally {
          reportSavePending = false;
          syncReportSaveRecovery();
        }
      }).finally(syncReportSaveRecovery);
    }
