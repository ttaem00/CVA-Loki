
    const params = new URLSearchParams(location.search);
    const base = params.get('base') || '';
    let reportInputComposing=false;
    document.addEventListener('compositionstart',()=>{reportInputComposing=true;});
    document.addEventListener('compositionend',()=>{reportInputComposing=false;});
    function isReportComposing(event) { return reportInputComposing || event.isComposing || event.keyCode===229; }
    // Report Admin은 관리자 편집 화면 하나다. 편집자 확인은 미리보기,
    // 시청자 확인은 공개 보고서 링크에서 수행한다.
    let reportPageRole = 'manager';
    const reportRoleSwitchEl = document.getElementById('reportRoleSwitch');
    const viewerRoleSurfaceEl = document.getElementById('viewerRoleSurface');
    const reportAppEl = document.querySelector('.app');
    const reportAppAnchor = document.createComment('report-app-role-anchor');
    if (reportAppEl && reportAppEl.parentNode) {
      reportAppEl.parentNode.insertBefore(reportAppAnchor, reportAppEl);
    }
    const roleDetachedNodes = new Map();
    const titleEl = document.getElementById('title');
    const titleTextEl = document.getElementById('titleText');
    const metaEl = document.getElementById('meta');
    const canonicalConflictBannerEl = document.getElementById('canonicalConflictBanner');
    const publicLinkEl = document.getElementById('publicLink');
    const reportWorkspaceLinkEl = document.getElementById('reportWorkspaceLink');
    if (reportWorkspaceLinkEl) {
      reportWorkspaceLinkEl.href = base
        ? `/report-workspace?base=${encodeURIComponent(base)}`
        : '/report-workspace';
      const initialScene=detailSceneParams({type:params.get('type'),id:params.get('id'),spanIndex:Number(params.get('span') || 0)});
      if(base&&initialScene.has('id'))reportWorkspaceLinkEl.href+=`&${initialScene}`;
      reportWorkspaceLinkEl.addEventListener('click',()=>updateDetailSceneLinks());
    }
    function detailSceneParams(selection) {
      const result=new URLSearchParams();
      if(!selection||!['D1','D2','Point','Highlight'].includes(selection.type))return result;
      const id=String(selection.id || ''),span=selection.spanIndex ?? 0;
      if(!id||id.length>256||/[\x00-\x1f\x7f]/.test(id)||!Number.isSafeInteger(span)||span<0)return result;
      result.set('type',selection.type);result.set('id',id);result.set('span',String(span));return result;
    }
    function updateDetailSceneLinks() {
      const selection=managerOutlineSelection;
      const item=selection&&managerOutlineCollection(selection.kind).find(row=>String(row.id)===String(selection.id));
      if(!item)return;
      const type=selection.kind==='range'?item.level:selection.kind==='point'?'Point':'Highlight';
      const query=detailSceneParams({type,id:selection.id,spanIndex:Number(selection.spanIndex || 0)});
      if(!query.has('id'))return;
      const current=new URLSearchParams(location.search);
      for(const key of ['type','id','span'])current.delete(key);
      for(const [key,value] of query)current.set(key,value);
      window.history?.replaceState(null,'',`${location.pathname}?${current}${location.hash || ''}`);
      if(reportWorkspaceLinkEl){const target=new URLSearchParams({base});
        for(const [key,value] of query)target.set(key,value);
        reportWorkspaceLinkEl.href=`/report-workspace?${target}`;}
      document.querySelectorAll('a[href="/reports"], a[href^="/reports?"]').forEach(anchor=>{const target=new URLSearchParams({base});
        for(const [key,value] of query)target.set(key,value);anchor.href=`/reports?${target}`;});
      document.querySelectorAll('#reportVersions a').forEach(anchor=>{
        const target=new URL(anchor.href,location.href);
        if(target.origin!==location.origin||!['/report','/report-workspace'].includes(target.pathname))return;
        for(const key of ['type','id','span'])target.searchParams.delete(key);
        for(const [key,value] of query)target.searchParams.set(key,value);
        anchor.href=target.pathname+target.search;
      });
    }
    function restoreDetailSceneSelection() {
      const query=new URLSearchParams(location.search),type=query.get('type'),id=query.get('id'),rawSpan=query.get('span') || '0';
      if(!/^\d{1,6}$/.test(rawSpan))return;
      const spanIndex=Number(rawSpan);
      if(!detailSceneParams({type,id,spanIndex}).has('id'))return;
      const kind=type==='D1'||type==='D2'?'range':type==='Point'?'point':'highlight';
      const item=managerOutlineCollection(kind).find(row=>String(row.id)===id);
      if(!item||(kind==='range'&&item.level!==type)||(kind==='highlight'?spanIndex>=(item.source_spans || []).length:spanIndex!==0))return;
      managerOutlineSelection={kind,id,spanIndex};managerOutlineGroup=kind;
      renderManagerOutlineNavigator();renderManagerOutlineInspector();renderManagerOutlineTimeline();updateDetailSceneLinks();
    }
    // #448 admin-only source manifest panel link (LLM 호출 0 / 캐시 키 영향 X).
    const sourceManifestLinkEl = document.getElementById('sourceManifestLink');
    if (sourceManifestLinkEl) {
      sourceManifestLinkEl.href = base
        ? `/source-manifest?base=${encodeURIComponent(base)}`
        : '#';
    }
    const workEvidenceLinkEl = document.getElementById('workEvidenceLink');
    if (workEvidenceLinkEl) {
      workEvidenceLinkEl.href = base
        ? `/work-evidence?base=${encodeURIComponent(base)}`
        : '#';
    }
    const mdEditor = document.getElementById('mdEditor');
    const previewTitleEl = document.getElementById('previewTitle');
    const previewInfoEl = document.getElementById('previewInfo');
    const previewFrame = document.getElementById('previewFrame');
    // #1440 split view (장면 보며 편집) 요소/상태.
    const splitViewToggleBtn = document.getElementById('splitViewToggle');
    const splitPlayerPaneEl = document.getElementById('splitPlayerPane');
    const splitPlayerResizerEl = document.getElementById('splitPlayerResizer');
    const splitPlayerFrame = document.getElementById('splitPlayerFrame');
    const splitPlayerStatusEl = document.getElementById('splitPlayerStatus');
    const splitPlayerNewTabEl = document.getElementById('splitPlayerNewTab');
    let splitVideoNo = '';
    let splitViewOn = false;
    const publishBtn = document.getElementById('publishBtn');
    const publishRowEl = document.getElementById('publishRow');
    const editorDecisionStateEl = document.getElementById('editorDecisionState');
    const ctEnabledCheckbox = document.getElementById('ctEnabledCheckbox');
    const ctToggleRowEl = document.getElementById('ctToggleRow');
    const adminPanelSummaryEl = document.getElementById('adminPanelSummary');
    const contextVocabSectionEl = document.getElementById('contextVocabSection');
    const contextVocabBodyEl = document.getElementById('contextVocabBody');
    const approvalInboxSectionEl = document.getElementById('approvalInboxSection');
    const approvalInboxBodyEl = document.getElementById('approvalInboxBody');
    const editorToolsSectionEl = document.getElementById('editorToolsSection');
    const editorToolsBodyEl = document.getElementById('editorToolsBody');
    const signalTimelineSectionEl = document.getElementById('signalTimelineSection');
    const signalTimelineBodyEl = document.getElementById('signalTimelineBody');
    const bundleAlignmentSectionEl = document.getElementById('bundleAlignmentSection');
    const bundleAlignmentBodyEl = document.getElementById('bundleAlignmentBody');
    const bundleMembershipSectionEl = document.getElementById('bundleMembershipSection');
    const bundleMembershipInputEl = document.getElementById('bundleMembershipInput');
    const bundleMembershipPreviewBtn = document.getElementById('bundleMembershipPreviewBtn');
    const bundleMembershipSaveBtn = document.getElementById('bundleMembershipSaveBtn');
    const bundleMembershipCancelBtn = document.getElementById('bundleMembershipCancelBtn');
    const bundleMembershipPreviewEl = document.getElementById('bundleMembershipPreview');
    const diagnosticsSectionEl = document.getElementById('diagnosticsSection');
    const diagnosticsBodyEl = document.getElementById('diagnosticsBody');
    const cockpitTimecodeEl = document.getElementById('cockpitTimecode');
    const inspectTimecodeBtn = document.getElementById('inspectTimecodeBtn');
    const saveCurrentTimeBtn = document.getElementById('saveCurrentTimeBtn');
    const draftTimelineCandidateBtn = document.getElementById('draftTimelineCandidateBtn');
    const insertTimelineCandidateBtn = document.getElementById('insertTimelineCandidateBtn');
    const timelineCandidatePreviewEl = document.getElementById('timelineCandidatePreview');
    const timelineSceneContextInputEl = document.getElementById('timelineSceneContextInput');
    const managerNoteInputEl = document.getElementById('managerNoteInput');
    const managerNotePreviewBtn = document.getElementById('managerNotePreviewBtn');
    const managerNoteClearBtn = document.getElementById('managerNoteClearBtn');
    const managerNotePreviewEl = document.getElementById('managerNotePreview');
    const busyOverlayEl = document.getElementById('busyOverlay');
    const busyTitleEl = document.getElementById('busyTitle');
    const busyMessageEl = document.getElementById('busyMessage');
    const signalDraftControlsEl = document.getElementById('signalDraftControls');
    const commentUsefulnessSectionEl = document.getElementById('commentUsefulnessSection');
    const commentUsefulnessBodyEl = document.getElementById('commentUsefulnessBody');
    const naverCafePostBtn = document.getElementById('naverCafePostBtn');
    const naverCafeFillDraftBtn = document.getElementById('naverCafeFillDraftBtn');
    const naverCafeCopyBtn = document.getElementById('naverCafeCopyBtn');
    const naverCafePreviewBtn = document.getElementById('naverCafePreviewBtn');
    const naverCafePreviewBoxEl = document.getElementById('naverCafePreviewBox');
    const naverCafePreviewBodyEl = document.getElementById('naverCafePreviewBody');
    const naverCafeStatusEl = document.getElementById('naverCafeStatus');
    const naverCafeDestinationEl = document.getElementById('naverCafeDestination');
    const naverCafeClipboardSandboxEl = document.getElementById('naverCafeClipboardSandbox');
    const naverCafePanelEl = document.querySelector('[data-testid="naver-cafe-post-panel"]');
    const chzzkCommentFillBtn = document.getElementById('chzzkCommentFillBtn');
    const chzzkCommentCopyBtn = document.getElementById('chzzkCommentCopyBtn');
    const chzzkCommentPreviewBtn = document.getElementById('chzzkCommentPreviewBtn');
    const chzzkCommentPreviewEl = document.getElementById('chzzkCommentPreview');
    const chzzkCommentDraftListEl = document.getElementById('chzzkCommentDraftList');
    const chzzkCommentStatusEl = document.getElementById('chzzkCommentStatus');
    const chzzkCommentGroupEl = document.querySelector('[data-testid="chzzk-comment-group"]');
    const revisionHistoryBtn = document.getElementById('revisionHistoryBtn');
    const revisionPanelEl = document.getElementById('revisionPanel');
    const revisionCloseBtn = document.getElementById('revisionCloseBtn');
    const revisionListEl = document.getElementById('revisionList');

    ctEnabledCheckbox.addEventListener('change', () => {
      ctToggleRowEl.classList.toggle('disabled', !ctEnabledCheckbox.checked);
    });
    const youtubeUrlEl = document.getElementById('youtubeUrl');
    const youtubeMetaEl = document.getElementById('youtubeMeta');
    const anchorGridEl = document.getElementById('anchorGrid');
    const youtubePreviewEl = document.getElementById('youtubePreview');
    const youtubeCopyBtn = document.getElementById('youtubeCopyBtn');
    const statusEl = document.getElementById('status');
    let youtubeState = { anchor_candidates: [], text: '', youtube_url: '' };
    let signalTimelineState = { events: [], selected_ranges_draft: { selected_ranges: [] } };
    const sceneFinderSessionId = (() => {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID().replace(/-/g, '_');
      }
      return `admin_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`;
    })();
    const sceneFinderSessionStartedAt = performance.now();

    function recordSceneFinderUse(eventType, details = {}) {
      const payload = {
        base,
        session_id: sceneFinderSessionId,
        event_type: eventType,
        elapsed_ms: Math.max(0, Math.round(performance.now() - sceneFinderSessionStartedAt)),
      };
      if (Number.isFinite(Number(details.cluster_count))) payload.cluster_count = Number(details.cluster_count);
      if (Number.isFinite(Number(details.row_count))) payload.row_count = Number(details.row_count);
      fetch('/api/scene-finder-usefulness/event', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
        keepalive: true,
      }).catch(() => {});
    }
    let contextVocabState = { channel_id: '', suggestions: [], staged_vocab: null, dismissed: new Set(), approval_states: {}, loading: false };
    let signalDraftEditingRangeId = '';
    let selectedCockpitSec = NaN;
    let selectedCockpitEvent = null;
    let selectedContextRangeEvent = null;
    let selectedExactMomentEvent = null;
    let selectedStoryWindowEvent = null;
    let signalTimelineDocumentKey = '';
    let adaptivePublicPreviewSelection = new Set();
    let selectedEvidenceKind = '';
    let adaptiveContextMode = 'viewer';
    let timelineCandidateMarkdown = '';
    let dirty = false;
    let naverCafePost = null;
    let naverCafeDestination = null;
    let naverCafeDraft = null;
    let chzzkCommentDraft = null;
    let bundleOriginalMembers = [];
    let bundleMembershipPreview = null;
    const TIMELINE_ZOOM_STORAGE_KEY = 'reportAdmin.sceneFinderZoom';
    const TIMELINE_ZOOM_DEFAULT = 1;
    const TIMELINE_ZOOM_MIN = 0.1;
    const TIMELINE_ZOOM_MAX = 6;
    const TIMELINE_WIDTH_MIN = 240;
    const COCKPIT_SECTION_DEFAULT_OPEN = new Set([
      'signalTimelineSection',
      'selectedEvidenceSection',
    ]);
    async function api(path, options = {}) {
      let res;
      try {
        res = await fetch(path, {
          headers: { 'Content-Type': 'application/json' },
          ...options,
        });
      } catch (err) {
        const reason = err && err.message ? err.message : '연결 실패';
        throw new Error(`로컬 Report Admin 서버에 연결하지 못했습니다. 서버 창이 열려 있는지 확인한 뒤 다시 시도하세요. (${reason})`);
      }
      const text = await res.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
      if (!res.ok) {
        const error = new Error(data.error || data.raw || `HTTP ${res.status}`);
        error.status = res.status;
        error.serverDeclined = res.status === 409 && data.error_code === 'manager_edit_conflict'
          && data.mayHaveRun === false && typeof data.error === 'string';
        throw error;
      }
      return data;
    }

    function setBusy(active, title = '처리 중입니다', message = '잠시만 기다려 주세요.', returnFocusEl = null) {
      if (!busyOverlayEl) return;
      if (busyTitleEl && busyTitleEl.textContent !== title) busyTitleEl.textContent = title;
      if (busyMessageEl && busyMessageEl.textContent !== message) busyMessageEl.textContent = message;
      busyOverlayEl.classList.toggle('active', !!active);
      busyOverlayEl.setAttribute('aria-hidden', active ? 'false' : 'true');
      document.body.setAttribute('aria-busy', active ? 'true' : 'false');
      if (!active && returnFocusEl && typeof returnFocusEl.focus === 'function') {
        returnFocusEl.focus({ preventScroll: true });
      }
    }

    async function runBusyTask(options, task) {
      const controls = (options.controls || []).filter(Boolean);
      const previous = controls.map((el) => [el, el.disabled]);
      previous.forEach(([el]) => { el.disabled = true; });
      setBusy(true, options.title, options.message, options.returnFocusEl || null);
      try {
        return await task();
      } finally {
        previous.forEach(([el, disabled]) => { el.disabled = disabled; });
        setBusy(false, options.title, options.message, options.returnFocusEl || null);
      }
    }

    // 입력 시 dirty 표시.
    mdEditor.addEventListener('input', () => {
      setDirty(mdEditor.value !== savedMd);
      scheduleManagerOutlineDraftSync('markdown');
      if (!naverCafePost || !naverCafePost.article_url) resetNaverCafeDraft();
      resetChzzkCommentDraft();
    });

    document.getElementById('reloadBtn').addEventListener('click', () => {
      if (reportSaveOutcome) { checkReportSaveOutcome(); return; }
      if (dirty && !confirm('저장하지 않은 변경 사항이 있습니다.\n현재 초안 대신 저장본을 다시 열면 변경 내용과 편집 되돌리기 기록이 사라집니다. 계속할까요?')) {
        return;
      }
      loadReport().catch((err) => setStatus(err.message, 'err'));
    });
    if (revisionHistoryBtn) revisionHistoryBtn.addEventListener('click', () => setRevisionPanelOpen(true));
    if (revisionCloseBtn) revisionCloseBtn.addEventListener('click', () => setRevisionPanelOpen(false));
    if (revisionPanelEl) {
      revisionPanelEl.addEventListener('click', (event) => {
        if (event.target === revisionPanelEl) setRevisionPanelOpen(false);
      });
    }
    document.getElementById('previewBtn').addEventListener('click', () => {
      previewCurrent().catch((err) => setStatus(err.message, 'err'));
    });
    document.getElementById('saveBtn').addEventListener('click', () => {
      saveCurrent().catch((err) => setStatus(err.message, 'err'));
    });
    document.getElementById('reportSaveCheckBtn').addEventListener('click', checkReportSaveOutcome);
    document.getElementById('reportSaveAdoptBtn').addEventListener('click', () => {
      adoptReportSaveOutcome().catch(error => setStatus(error.message, 'err'));
    });
    publishBtn.addEventListener('click', () => {
      publishCurrent().catch((err) => setStatus(err.message, 'err'));
    });
      managerOutlineSaveBtn.addEventListener('click', () => {
        saveManagerOutline().catch(() => {});
      });
    managerOutlineUndoBtn.addEventListener('click', undoManagerOutlineChange);

    managerOutlineAddBtn.addEventListener('click', () => {
      if (managerOutlineAddMenu.hidden) openManagerOutlineAddMenu();
      else closeManagerOutlineAddMenu();
    });
    managerOutlineAddMenu.addEventListener('click', event => {
      const addButton = event.target.closest('[data-outline-add]');
      if (!addButton || addButton.disabled) return;
      addManagerOutlineItem(addButton.dataset.outlineAdd);
    });
    managerOutlineAddMenu.addEventListener('keydown', event => {
      if(isReportComposing(event))return;
      const items = [...managerOutlineAddMenu.querySelectorAll('[role="menuitem"]:not(:disabled)')];
      if (event.key === 'Escape') {
        event.preventDefault();
        closeManagerOutlineAddMenu();
        return;
      }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || !items.length) return;
      event.preventDefault();
      const current = items.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
        : (Math.max(0, current) + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items[next].focus();
    });
    document.addEventListener('pointerdown', event => {
      if (!managerOutlineAddMenu.hidden && !event.target.closest('.manager-outline-toolbar')) closeManagerOutlineAddMenu(false);
    });

    let managerOutlineResizeFrame = 0;
    const managerOutlineResizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
      if (!managerOutlineState || managerOutlineResizeFrame) return;
      managerOutlineResizeFrame = requestAnimationFrame(() => {
        managerOutlineResizeFrame = 0;
        reapplyManagerOutlineTimelineZoomAfterResize();
        refreshManagerOutlineTimelineGeometry();
      });
    }) : null;
    if (managerOutlineResizeObserver) managerOutlineResizeObserver.observe(managerOutlineEditorEl);

    managerOutlineInspectorEl.addEventListener('click', event => {
      if (event.target.closest('[data-outline-delete]')) {
        deleteSelectedManagerOutlineItem();
        return;
      }
      const spanSeek = event.target.closest('[data-highlight-span-seek]');
      if (spanSeek) {
        const id = String(spanSeek.dataset.outlineId || '');
        const spanIndex = Number(spanSeek.dataset.highlightSpanIndex || 0);
        const item = (managerOutlineState.highlights || []).find(row => row.id === id);
        if (!item) return;
        managerOutlineSelection = {kind: 'highlight', id, spanIndex};
        managerOutlineGroup = 'highlight';
        syncManagerOutlineSelectionEvidence(item, 'highlight', spanIndex);
        renderManagerOutlineTimeline();
        return;
      }
      const stepButton = event.target.closest('[data-highlight-time-step]');
      if (!stepButton) return;
      const id = String(stepButton.dataset.outlineId || '');
      const edge = String(stepButton.dataset.highlightEdge || '');
      const highlight = (managerOutlineState.highlights || []).find(row => row.id === id);
      const current = highlightBoundarySeconds(highlight, edge);
      if (!Number.isFinite(current)) return;
      const snapshot = captureManagerOutlineSnapshot();
      resizeHighlightRange(id, edge, current + Number(stepButton.dataset.highlightTimeStep || 0), snapshot, false);
    });
    managerOutlineInspectorEl.addEventListener('focusin', event => {
      const control = event.target.closest('[data-outline-field]');
      if (!control) return;
      managerOutlinePendingInput = {
        snapshot: captureManagerOutlineSnapshot(),
        kind: control.dataset.outlineKind,
        id: control.dataset.outlineId,
        field: control.dataset.outlineField,
      };
    });
    managerOutlineInspectorEl.addEventListener('input', event => {
      if (event.target.matches('[data-outline-field]')) {
        validateManagerOutlineField(event.target);
        const field = event.target.dataset.outlineField;
        if (field === 'title' || field === 'content' || field === 'story_why_notable') {
          const item = managerOutlineCollection(event.target.dataset.outlineKind).find(row => row.id === event.target.dataset.outlineId);
          if (item) item[field] = event.target.value;
          scheduleManagerOutlineDraftSync('outline');
        }
        setDirty(true);
      }
    });

    managerOutlineInspectorEl.addEventListener('change', event => {
      const highlightSpanBoundary = event.target.closest('[data-highlight-span-boundary]');
      if (highlightSpanBoundary) {
        commitHighlightSpanBoundary(highlightSpanBoundary);
        return;
      }
      const highlightBoundary = event.target.closest('[data-highlight-boundary]');
      if (highlightBoundary) {
        commitHighlightBoundary(highlightBoundary);
        return;
      }
      const control = event.target.closest('[data-outline-field]');
      if (control) commitManagerOutlineField(control);
    });
    document.addEventListener('keydown', dismissVisibleManagerOutlineTooltipOnEscape);
    function setAdminHighlightGroupEmphasis(id, active) {
      if (!id) return;
      managerOutlineTimelineEl.querySelectorAll('[data-outline-timeline-kind="highlight"][data-outline-timeline-id]').forEach(marker => {
        if (marker.dataset.outlineTimelineId === id) marker.classList.toggle('highlight-group-emphasis', Boolean(active));
      });
    }
    managerOutlineTimelineEl.addEventListener('click', event => {
      if (event.target.closest('[data-outline-handle]')) return;
      const target = event.target.closest('[data-outline-timeline-id]');
      if (!target) return;
      const spanIndex = Number(target.dataset.highlightSpanIndex || 0);
      managerOutlineSelection = {kind: target.dataset.outlineTimelineKind, id: target.dataset.outlineTimelineId, ...(target.dataset.outlineTimelineKind === 'highlight' ? {spanIndex} : {})};
      managerOutlineGroup = managerOutlineSelection.kind;
      renderManagerOutlineNavigator();
      renderManagerOutlineInspector();
      renderManagerOutlineTimeline();
      if (managerOutlineSelection.kind === 'highlight') {
        const item = (managerOutlineState.highlights || []).find(row => row.id === managerOutlineSelection.id);
        if (item) syncManagerOutlineSelectionEvidence(item, 'highlight', spanIndex);
      }
    });
    managerOutlineTimelineEl.addEventListener('pointerover', event => {
      const target = event.target.closest('[data-outline-timeline-id]');
      if (!target || target.contains(event.relatedTarget)) return;
      hoveredManagerOutlineTooltipTarget = target;
      if (target.dataset.outlineTimelineKind === 'highlight') setAdminHighlightGroupEmphasis(target.dataset.outlineTimelineId, true);
      cancelManagerOutlineTooltipHide();
      if (event.relatedTarget === managerOutlineTimelineTooltipEl && activeManagerOutlineTooltipTarget === target) return;
      if (activeManagerOutlineTooltipTarget !== target || managerOutlineTimelineTooltipEl.hidden) {
        scheduleManagerOutlineTooltip(event.target);
      }
    });
    managerOutlineTimelineEl.addEventListener('pointerout', event => {
      const target = event.target.closest('[data-outline-timeline-id]');
      if (!target || target.contains(event.relatedTarget)) return;
      hoveredManagerOutlineTooltipTarget = null;
      if (target.dataset.outlineTimelineKind === 'highlight') {
        const next = event.relatedTarget && event.relatedTarget.closest ? event.relatedTarget.closest('[data-outline-timeline-id]') : null;
        if (!next || next.dataset.outlineTimelineId !== target.dataset.outlineTimelineId) setAdminHighlightGroupEmphasis(target.dataset.outlineTimelineId, false);
      }
      if (event.relatedTarget === managerOutlineTimelineTooltipEl) return;
      if (target.contains(document.activeElement)) return;
      if (activeManagerOutlineTooltipTarget === target) scheduleManagerOutlineTooltipHide(target);
      else cancelManagerOutlineTooltipShow();
      releaseManagerOutlineTooltipSuppression(target);
    });
    managerOutlineTimelineEl.addEventListener('focusin', event => {
      const target = event.target.closest('[data-outline-timeline-id]');
      if (target && target.dataset.outlineTimelineKind === 'highlight') setAdminHighlightGroupEmphasis(target.dataset.outlineTimelineId, true);
      showManagerOutlineTooltip(event.target);
    });
    managerOutlineTimelineEl.addEventListener('focusout', event => {
      const target = event.target.closest('[data-outline-timeline-id]');
      if (!target || target.contains(event.relatedTarget)) return;
      const nextHighlight = event.relatedTarget && event.relatedTarget.closest ? event.relatedTarget.closest('[data-outline-timeline-id]') : null;
      if (target.dataset.outlineTimelineKind === 'highlight' && (!nextHighlight || nextHighlight.dataset.outlineTimelineId !== target.dataset.outlineTimelineId)) setAdminHighlightGroupEmphasis(target.dataset.outlineTimelineId, false);
      const nextTarget = event.relatedTarget && event.relatedTarget.closest
        ? event.relatedTarget.closest('[data-outline-timeline-id]')
        : null;
      if (nextTarget) showManagerOutlineTooltip(event.relatedTarget);
      else if (hoveredManagerOutlineTooltipTarget !== target && !managerOutlineTooltipPointerInside) hideManagerOutlineTooltip();
      releaseManagerOutlineTooltipSuppression(target);
    });
    managerOutlineTimelineTooltipEl.addEventListener('pointerenter', () => {
      managerOutlineTooltipPointerInside = true;
      cancelManagerOutlineTooltipHide();
    });
    managerOutlineTimelineTooltipEl.addEventListener('pointerleave', event => {
      managerOutlineTooltipPointerInside = false;
      if (activeManagerOutlineTooltipTarget && activeManagerOutlineTooltipTarget.contains(event.relatedTarget)) {
        hoveredManagerOutlineTooltipTarget = activeManagerOutlineTooltipTarget;
        return;
      }
      hoveredManagerOutlineTooltipTarget = null;
      const target = activeManagerOutlineTooltipTarget || suppressedManagerOutlineTooltipTarget;
      if (target && !target.contains(document.activeElement)) hideManagerOutlineTooltip();
      releaseManagerOutlineTooltipSuppression(target);
    });
    managerOutlineTimelineTooltipEl.addEventListener('pointerdown', () => {
      suppressManagerOutlineTooltip(activeManagerOutlineTooltipTarget);
    });
    window.addEventListener('resize', reapplyManagerOutlineTimelineZoomAfterResize);
    window.addEventListener('resize', () => positionManagerOutlineTooltip(activeManagerOutlineTooltipTarget));
    window.addEventListener('scroll', () => positionManagerOutlineTooltip(activeManagerOutlineTooltipTarget), true);
    managerOutlineTimelineZoomEl.addEventListener('input', () => applyManagerOutlineTimelineZoom(managerOutlineTimelineZoomEl.value));
    managerOutlineTimelineFullViewBtn.addEventListener('click', () => {
      applyManagerOutlineTimelineZoom(1);
      managerOutlineTimelineViewportEl.scrollLeft = 0;
    });
    managerOutlineTimelineViewportEl.addEventListener('wheel', event => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      const rect = managerOutlineTimelineViewportEl.getBoundingClientRect();
      const viewportWidth = managerOutlineTimelineViewportEl.clientWidth;
      const pointerX = Math.max(0, Math.min(viewportWidth, event.clientX - rect.left));
      applyManagerOutlineTimelineZoom(managerOutlineTimelineZoom + (event.deltaY < 0 ? 1 : -1), pointerX);
    }, {passive:false});
    managerOutlineTimelineEl.addEventListener('keydown', event => {
      if(isReportComposing(event))return;
      const target = event.target.closest('[data-outline-timeline-id]');
      if (!target) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        suppressManagerOutlineTooltip(target);
        return;
      }
      const kind = target.dataset.outlineTimelineKind;
      const id = target.dataset.outlineTimelineId;
      const spanIndex = Number(target.dataset.highlightSpanIndex || 0);
      const handle = event.target.closest('[data-outline-handle]');
      if ((handle || ['point', 'candidate', 'highlight'].includes(kind)) && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
        event.preventDefault();
        managerOutlineSelection = {kind, id, ...(kind === 'highlight' ? {spanIndex} : {})};
        managerOutlineGroup = kind;
        const snapshot = captureManagerOutlineSnapshot();
        const direction = event.key === 'ArrowRight' ? 1 : -1;
        const delta = (event.shiftKey ? 10 : 1) * direction;
        let changed = false;
        let focusSelector = `[data-outline-timeline-id="${CSS.escape(id)}"]${kind === 'highlight' ? `[data-highlight-span-index="${spanIndex}"]` : ''}`;
        if (handle) {
          const edge = handle.dataset.outlineHandle;
          const item = managerOutlineCollection(kind).find(row => row.id === id);
          if (!item) return;
          const edgeSeconds = kind === 'highlight'
            ? highlightSpanBoundarySeconds(item, spanIndex, edge)
            : outlineSeconds(item[edge]);
          changed = kind === 'candidate'
            ? resizeCandidateRange(id, edge, edgeSeconds + delta, snapshot, false)
            : kind === 'highlight'
              ? resizeHighlightSpan(id, spanIndex, edge, edgeSeconds + delta, snapshot, false)
              : resizeOutlineRange(id, edge, edgeSeconds + delta, snapshot, false);
          focusSelector += ` [data-outline-handle="${edge}"]`;
        } else if (kind === 'point') {
          const point = (managerOutlineState.points || []).find(row => row.id === id);
          changed = !!point && movePoint(id, outlineSeconds(point.timestamp) + delta, snapshot, false);
        } else if (kind === 'highlight') {
          changed = moveHighlightSpan(id, spanIndex, delta, snapshot, false);
        } else {
          changed = moveCandidateRange(id, delta, snapshot, false);
        }
        const replacement = managerOutlineTimelineEl.querySelector(focusSelector);
        if (replacement) replacement.focus({preventScroll: true});
        if (!changed) updateManagerOutlineAddMenu();
        return;
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        target.click();
      }
    });
    managerOutlineTimelineEl.addEventListener('pointerdown', event => {
      const target = event.target.closest('[data-outline-timeline-id]');
      if (target) suppressManagerOutlineTooltip(target);
      else hideManagerOutlineTooltip();
      const handle = event.target.closest('[data-outline-handle]');
      if (!target || (!handle && !target.matches('[data-outline-drag-mode="move"]'))) return;
      const kind = target.dataset.outlineTimelineKind;
      const spanIndex = Number(target.dataset.highlightSpanIndex || 0);

      const track = target.closest('.manager-outline-track');
      if (!track) return;
      event.preventDefault();
      managerOutlineSelection = {kind, id: target.dataset.outlineTimelineId, ...(kind === 'highlight' ? {spanIndex} : {})};
      managerOutlineGroup = kind;
      renderManagerOutlineNavigator();
      renderManagerOutlineInspector();
      const snapshot = captureManagerOutlineSnapshot();
      const duration = Number(track.dataset.outlineWindowDuration) || managerOutlineDuration();
      const startX = event.clientX;
      const originalItem = (handle || ['range', 'highlight'].includes(kind))
        ? ((kind === 'candidate' ? snapshot.state.candidates : kind === 'highlight' ? snapshot.state.highlights : snapshot.state.ranges) || [])
          .find(row => row.id === target.dataset.outlineTimelineId)
        : null;
      const originalEdgeSeconds = handle && originalItem
        ? kind === 'highlight'
          ? highlightSpanBoundarySeconds(originalItem, spanIndex, handle.dataset.outlineHandle)
          : outlineSeconds(originalItem[handle.dataset.outlineHandle])
        : null;
      const originalPoint = !handle && kind === 'point'
        ? (snapshot.state.points || []).find(row => row.id === target.dataset.outlineTimelineId)
        : null;
      const originalPointSeconds = !handle && kind === 'point' && originalPoint
        ? outlineSeconds(originalPoint.timestamp)
        : null;
      let lastX = startX;
      let moved = false;
      target.classList.add('dragging');
      const applyPointer = (clientX, live) => {
        const rect = track.getBoundingClientRect();
        const pointerDelta = (clientX - startX) / Math.max(1, rect.width) * duration;
        if (handle) {
          if (!Number.isFinite(originalEdgeSeconds)) return false;
          const edge = handle.dataset.outlineHandle;
          const resizeDelta = pointerDelta;
          const resizedSeconds = originalEdgeSeconds + resizeDelta;
          return kind === 'candidate'
            ? resizeCandidateRange(target.dataset.outlineTimelineId, edge, resizedSeconds, snapshot, live)
            : kind === 'highlight'
              ? resizeHighlightSpan(target.dataset.outlineTimelineId, spanIndex, edge, resizedSeconds, snapshot, live)
              : resizeOutlineRange(target.dataset.outlineTimelineId, edge, resizedSeconds, snapshot, live);
        }
        if (kind === 'point') {
          if (!Number.isFinite(originalPointSeconds)) return false;
          const movedPointSeconds = originalPointSeconds + pointerDelta;
          return movePoint(target.dataset.outlineTimelineId, movedPointSeconds, snapshot, live);
        }
        return kind === 'range'
          ? moveOutlineRange(target.dataset.outlineTimelineId, pointerDelta, snapshot, live)
          : kind === 'highlight'
            ? moveHighlightSpan(target.dataset.outlineTimelineId, spanIndex, pointerDelta, snapshot, live)
            : moveCandidateRange(target.dataset.outlineTimelineId, pointerDelta, snapshot, live);
      };
      const move = moveEvent => {
        lastX = moveEvent.clientX;
        moved = moved || Math.abs(lastX - startX) >= 1;
        if (moved) applyPointer(lastX, true);
      };
      const cleanup = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', finish);
        window.removeEventListener('pointercancel', cancel);
      };
      const finish = () => {
        cleanup();
        if (moved) applyPointer(lastX, false);
        else renderManagerOutlineTimeline();
      };
      const cancel = () => {
        cleanup();
        restoreManagerOutlineSnapshot(snapshot);
        managerOutlineDragStatusEl.textContent = '조작을 취소하고 원래 위치로 돌아갔어요.';
        renderManagerOutlineNavigator();
        renderManagerOutlineInspector();
        renderManagerOutlineTimeline();
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', finish, {once: true});
      window.addEventListener('pointercancel', cancel, {once: true});
    });
    managerOutlineGroupTabsEl.addEventListener('click', event => {
      const button = event.target.closest('[data-outline-group]');
      if (!button || button.dataset.outlineGroup === managerOutlineGroup) return;
      syncManagerOutlineInspector();
      managerOutlineGroup = button.dataset.outlineGroup;
      const first = managerOutlineCollection(managerOutlineGroup)[0];
      managerOutlineSelection = first ? {kind: managerOutlineGroup, id: first.id} : null;
      renderManagerOutlineNavigator();
      renderManagerOutlineInspector();
      renderManagerOutlineTimeline();
      const selected = managerOutlineRowsEl.querySelector('[aria-selected="true"]');
      if (selected) selected.focus({preventScroll: true});
    });
    managerOutlineGroupTabsEl.addEventListener('keydown', event => {
      if(isReportComposing(event))return;
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const tabs = [...managerOutlineGroupTabsEl.querySelectorAll('[role="tab"]')];
      const current = tabs.indexOf(event.target.closest('[role="tab"]'));
      if (current < 0) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
        : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].click();
      managerOutlineGroupTabsEl.querySelector(`[data-outline-group="${tabs[next].dataset.outlineGroup}"]`).focus();
    });
    managerOutlineRowsEl.addEventListener('click', event => {
      const treeItem = event.target.closest('[role="treeitem"][data-outline-select-id]');
      if (!treeItem) return;
      syncManagerOutlineInspector();
      managerOutlineSelection = {
        kind: treeItem.dataset.outlineSelectKind,
        id: treeItem.dataset.outlineSelectId,
      };
      renderManagerOutlineNavigator();
      renderManagerOutlineInspector();
      renderManagerOutlineTimeline();
      const firstInput = managerOutlineInspectorEl.querySelector('[data-outline-field]');
      if (firstInput) firstInput.focus({preventScroll: true});
    });
    managerOutlineRowsEl.addEventListener('keydown', event => {
      if(isReportComposing(event))return;
      const treeItem = event.target.closest('[role="treeitem"][data-outline-select-id]');
      if (!treeItem) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        treeItem.click();
        return;
      }
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      const items = [...managerOutlineRowsEl.querySelectorAll('[role="treeitem"][data-outline-select-id]')];
      const current = items.indexOf(treeItem);
      if (current < 0) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
        : Math.max(0, Math.min(items.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1)));
      items.forEach(item => { item.tabIndex = item === items[next] ? 0 : -1; });
      items[next].focus();
    });
    managerOutlineHistoryEl.addEventListener('click', (event) => {
      const button = event.target.closest('[data-outline-restore]');
      if (button) restoreManagerOutline(button.dataset.outlineRestore).catch(() => {});
    });
    if (naverCafePostBtn) {
      naverCafePostBtn.addEventListener('click', () => {
        postToNaverCafe().catch((err) => setNaverCafeStatus(err.message, 'err'));
      });
    }
    if (naverCafeFillDraftBtn) {
      naverCafeFillDraftBtn.addEventListener('click', () => {
        fillNaverCafeDraft().catch((err) => setNaverCafeStatus(err.message, 'err'));
      });
    }
    if (naverCafeCopyBtn) {
      naverCafeCopyBtn.addEventListener('click', () => {
        copyNaverCafeDraft().catch((err) => setNaverCafeStatus(err.message, 'err'));
      });
    }
    if (naverCafePreviewBtn) {
      naverCafePreviewBtn.addEventListener('click', () => {
        previewNaverCafeDraft().catch((err) => setNaverCafeStatus(err.message, 'err'));
      });
    }
    if (chzzkCommentFillBtn) {
      chzzkCommentFillBtn.addEventListener('click', () => {
        openChzzkCommentWriter().catch((err) => setChzzkCommentStatus(err.message, 'err'));
      });
    }
    if (chzzkCommentCopyBtn) {
      chzzkCommentCopyBtn.addEventListener('click', () => {
        copyChzzkCommentDraft().catch((err) => setChzzkCommentStatus(err.message, 'err'));
      });
    }
    if (chzzkCommentPreviewBtn) {
      chzzkCommentPreviewBtn.addEventListener('click', () => {
        previewChzzkCommentDraft().catch((err) => setChzzkCommentStatus(err.message, 'err'));
      });
    }
    if (bundleMembershipPreviewBtn) {
      bundleMembershipPreviewBtn.addEventListener('click', () => {
        previewBundleMembership().catch((err) => setStatus(err.message, 'err'));
      });
    }
    if (bundleMembershipSaveBtn) {
      bundleMembershipSaveBtn.addEventListener('click', () => {
        saveBundleMembership().catch((err) => setStatus(err.message, 'err'));
      });
    }
    if (bundleMembershipCancelBtn) {
      bundleMembershipCancelBtn.addEventListener('click', resetBundleMembership);
    }
    // Ctrl/Cmd+S → 저장.
    document.addEventListener('keydown', (e) => {
      if(isReportComposing(e))return;
      if (e.key === 'Escape' && revisionPanelEl && revisionPanelEl.classList.contains('open')) {
        setRevisionPanelOpen(false);
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        saveCurrent().catch((err) => setStatus(err.message, 'err'));
      }
    });
    document.getElementById('youtubePreviewBtn').addEventListener('click', () => {
      previewYoutube().catch((err) => setStatus(err.message, 'err'));
    });
    document.getElementById('youtubeSaveBtn').addEventListener('click', () => {
      saveYoutubeAlignment().catch((err) => setStatus(err.message, 'err'));
    });
    youtubeCopyBtn.addEventListener('click', () => {
      copyYoutubeText().catch((err) => setStatus(err.message, 'err'));
    });
    if (inspectTimecodeBtn) inspectTimecodeBtn.addEventListener('click', inspectTypedTimecode);
    if (cockpitTimecodeEl) {
      cockpitTimecodeEl.addEventListener('keydown', (e) => {
        if(isReportComposing(e))return;
        if (e.key === 'Enter') {
          e.preventDefault();
          inspectTypedTimecode();
        }
      });
    }
    if (saveCurrentTimeBtn) saveCurrentTimeBtn.addEventListener('click', () => saveSignalDraft());
    if (managerNotePreviewBtn) {
      managerNotePreviewBtn.addEventListener('click', () => {
        runBusyTask({
          title: '메모 초안 만드는 중',
          message: '입력한 메모를 저장하지 않고 작업 초안 카드로 바꾸고 있습니다.',
          controls: [managerNotePreviewBtn, managerNoteClearBtn, draftTimelineCandidateBtn],
          returnFocusEl: managerNotePreviewBtn,
        }, previewManagerNoteActions).catch((err) => {
          const message = '메모 초안 실패: ' + err.message;
          openCockpitSection('managerNoteSection');
          updateCockpitSectionStates();
          if (managerNotePreviewEl) managerNotePreviewEl.textContent = message + '\n입력한 메모는 그대로 남아 있습니다. 서버 창 상태를 확인한 뒤 다시 눌러 주세요.';
          setStatus(message, 'err');
        });
      });
    }
    if (managerNoteInputEl) {
      managerNoteInputEl.addEventListener('keydown', (e) => {
        if(isReportComposing(e))return;
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          runBusyTask({
            title: '메모 초안 만드는 중',
            message: '입력한 메모를 저장하지 않고 작업 초안 카드로 바꾸고 있습니다.',
            controls: [managerNotePreviewBtn, managerNoteClearBtn, draftTimelineCandidateBtn],
            returnFocusEl: managerNoteInputEl,
          }, previewManagerNoteActions).catch((err) => {
            const message = '메모 초안 실패: ' + err.message;
            openCockpitSection('managerNoteSection');
            updateCockpitSectionStates();
            if (managerNotePreviewEl) managerNotePreviewEl.textContent = message + '\n입력한 메모는 그대로 남아 있습니다. 서버 창 상태를 확인한 뒤 다시 눌러 주세요.';
            setStatus(message, 'err');
          });
        }
      });
    }
    if (managerNoteClearBtn) {
      managerNoteClearBtn.addEventListener('click', () => {
        if (managerNoteInputEl) managerNoteInputEl.value = '';
        if (managerNotePreviewEl) managerNotePreviewEl.textContent = '메모를 입력하면 저장 없이 작업 초안만 표시됩니다.';
        setStatus('메모 초안을 지웠습니다.', 'ok');
      });
    }
    if (draftTimelineCandidateBtn) {
      draftTimelineCandidateBtn.addEventListener('click', () => {
        runBusyTask({
          title: '한 줄 요약 후보 생성 중',
          message: '선택한 시각 주변 근거와 장면 메모를 함께 확인해 후보 1개만 만듭니다.',
          controls: [draftTimelineCandidateBtn, managerNotePreviewBtn],
          returnFocusEl: draftTimelineCandidateBtn,
        }, draftTimelineCandidate).catch((err) => {
          openCockpitSection('summaryCandidateSection');
          updateCockpitSectionStates();
          if (timelineCandidatePreviewEl) {
            timelineCandidatePreviewEl.textContent = '후보 생성 실패: ' + err.message + '\n타임코드, 장면 메모, 본문은 그대로 남아 있습니다. 서버 창이나 LLM/provider 상태를 확인한 뒤 다시 시도하세요.';
          }
          setStatus('후보 생성 실패: ' + err.message, 'err');
        });
      });
    }
    if (insertTimelineCandidateBtn) insertTimelineCandidateBtn.addEventListener('click', insertTimelineCandidate);
    if (previewFrame) previewFrame.addEventListener('load', bindPreviewTimelineCardClicks);
    if (splitViewToggleBtn) splitViewToggleBtn.addEventListener('click', () => setSplitView(!splitViewOn));
    if (splitPlayerFrame) splitPlayerFrame.addEventListener('load', () => {
      // cross-origin이라 로드 성공/실패 구분 불가 — 안내문만 평상 상태로 복귀.
      if (splitViewOn && splitPlayerFrame.src && splitPlayerFrame.src !== 'about:blank' && splitPlayerStatusEl) {
        splitPlayerStatusEl.textContent = '타임라인 카드를 누르면 그 장면으로 이동합니다. 화면이 비어 보이면 우측 새 탭 링크를 쓰세요.';
      }
    });
    if (reportRoleSwitchEl) {
      reportRoleSwitchEl.querySelectorAll('[data-report-page-role]').forEach((button) => {
        button.addEventListener('click', () => {
          applyReportPageRole(button.getAttribute('data-report-page-role'));
        });
      });
    }

    // 닫기/이동 시 변경분 손실 가드.
    window.addEventListener('beforeunload', (e) => {
      if (dirty || reportSaveOutcome || reportSavePending) { e.preventDefault(); e.returnValue = ''; }
    });

    function setupColumnResizer(options) {
      const appEl = document.querySelector('.app');
      const resizer = document.getElementById(options.resizerId);
      if (!appEl || !resizer) return;
      const mainMin = 280;
      const gripWidth = 12;
      const oppositeMin = options.oppositeMin || 320;
      const oppositeDefault = options.oppositeDefault || 560;
      function readCssPx(name, fallback) {
        const value = parseInt(getComputedStyle(appEl).getPropertyValue(name), 10);
        return Number.isFinite(value) ? value : fallback;
      }
      function dynamicMax() {
        const appWidth = appEl.getBoundingClientRect().width || window.innerWidth || options.max;
        const room = appWidth - oppositeMin - mainMin - gripWidth;
        return Math.max(options.min, Math.min(options.max, room));
      }
      function clamp(w) {
        return Math.min(dynamicMax(), Math.max(options.min, Math.round(w)));
      }
      function applyWidth(w, persist) {
        const cw = clamp(w);
        appEl.style.setProperty(options.cssVar, cw + 'px');
        const appWidth = appEl.getBoundingClientRect().width || window.innerWidth || 0;
        const opposite = readCssPx(options.oppositeVar, oppositeDefault);
        const oppositeRoom = Math.max(oppositeMin, appWidth - cw - mainMin - gripWidth);
        if (opposite > oppositeRoom) {
          const nextOpposite = Math.round(oppositeRoom);
          appEl.style.setProperty(options.oppositeVar, nextOpposite + 'px');
          if (persist && options.oppositeStorageKey) {
            try { localStorage.setItem(options.oppositeStorageKey, String(nextOpposite)); } catch (_) {}
          }
        }
        if (persist) {
          try { localStorage.setItem(options.storageKey, String(cw)); } catch (_) {}
        }
        return cw;
      }
      try {
        const saved = parseInt(localStorage.getItem(options.storageKey) || '', 10);
        applyWidth(Number.isFinite(saved) ? saved : options.defaultWidth, false);
      } catch (_) {
        applyWidth(options.defaultWidth, false);
      }
      let dragging = false;
      let pendingClientX = NaN;
      let frame = 0;
      function widthFromClientX(clientX) {
        const rect = appEl.getBoundingClientRect();
        return options.edge === 'left'
          ? clientX - rect.left
          : rect.right - clientX;
      }
      function flushMove() {
        frame = 0;
        if (!dragging || !Number.isFinite(pendingClientX)) return;
        applyWidth(widthFromClientX(pendingClientX), false);
      }
      function queueMove(clientX) {
        pendingClientX = clientX;
        if (!frame) frame = requestAnimationFrame(flushMove);
      }
      function finishDrag() {
        if (!dragging) return;
        dragging = false;
        resizer.classList.remove('dragging');
        document.body.classList.remove('col-resizing');
        if (frame) {
          cancelAnimationFrame(frame);
          frame = 0;
          flushMove();
        }
        const cur = readCssPx(options.cssVar, options.defaultWidth);
        applyWidth(cur, true);
      }
      resizer.addEventListener('pointerdown', (event) => {
        dragging = true;
        pendingClientX = event.clientX;
        resizer.setPointerCapture(event.pointerId);
        resizer.classList.add('dragging');
        document.body.classList.add('col-resizing');
        queueMove(event.clientX);
        event.preventDefault();
      });
      resizer.addEventListener('pointermove', (event) => {
        if (!dragging) return;
        queueMove(event.clientX);
        event.preventDefault();
      });
      resizer.addEventListener('pointerup', finishDrag);
      resizer.addEventListener('pointercancel', finishDrag);
      resizer.addEventListener('dblclick', (event) => {
        if (event.ctrlKey || event.metaKey) applyWidth(options.defaultWidth, true);
      });
      resizer.addEventListener('keydown', (event) => {
        const cur = readCssPx(options.cssVar, options.defaultWidth);
        const step = event.shiftKey ? 64 : 16;
        const direction = options.edge === 'left'
          ? (event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0)
          : (event.key === 'ArrowLeft' ? 1 : event.key === 'ArrowRight' ? -1 : 0);
        if (direction) {
          applyWidth(cur + direction * step, true);
          event.preventDefault();
        }
      });
      window.addEventListener('resize', () => {
        applyWidth(readCssPx(options.cssVar, options.defaultWidth), true);
      });
    }

    setupColumnResizer({
      resizerId: 'sidebarResizer',
      cssVar: '--sidebar-w',
      oppositeVar: '--admin-w',
      storageKey: 'reportAdmin.sidebarWidth',
      oppositeStorageKey: 'reportAdmin.editorCockpitWidth',
      edge: 'left',
      min: 320,
      max: 1200,
      defaultWidth: 720,
      oppositeDefault: 560,
    });
    setupColumnResizer({
      resizerId: 'adminPanelResizer',
      cssVar: '--admin-w',
      oppositeVar: '--sidebar-w',
      storageKey: 'reportAdmin.editorCockpitWidth',
      oppositeStorageKey: 'reportAdmin.sidebarWidth',
      edge: 'right',
      min: 320,
      max: 1400,
      defaultWidth: 560,
      oppositeDefault: 720,
    });
    setupSplitPlayerResizer();

    document.addEventListener('click',event=>{
      if(event.target.closest('#managerOutlineRows, #managerOutlineTimeline, #managerOutlineInspector, #managerOutlineGroupTabs, #reportVersions'))updateDetailSceneLinks();
    });
    document.addEventListener('keyup',event=>{
      if(!isReportComposing(event)&&event.target.closest('#managerOutlineRows, #managerOutlineTimeline, #managerOutlineGroupTabs'))updateDetailSceneLinks();
    });
    managerOutlineEditorEl.addEventListener('keydown',event=>{
      if(isReportComposing(event)&&['Enter','Escape'].includes(event.key))event.stopPropagation();
    },true);
    initCockpitDisclosures();
    loadReport().then(() => {
      applyReportPageRole(reportPageRole);
      document.body.classList.remove('report-loading');
      return resumeActivePublishOperation();
    }).catch((err) => {
      document.body.classList.remove('report-loading');
      titleTextEl.textContent = '리포트를 불러올 수 없습니다.';
      // base 식별자도 같이 — 다른 리포트로 다시 진입할 수 있게.
      const detail = base ? ` (base=${base})` : '';
      setStatus((err && err.message ? err.message : String(err)) + detail, 'err');
      // 미리보기 영역도 회색 안내문으로 — 빈 iframe 방치 금지.
      previewFrame.srcdoc = '<div style="font-family:Geist,sans-serif;padding:32px;color:#7982a9;background:#1a1b26;height:100%;">리포트를 불러오지 못했습니다. 좌측 상단 ‹ 목록 으로 돌아가 다시 선택하세요.</div>';
    });
