    function renderSignalTimeline(workbench, sharedSceneFinderHtml) {
      if (!signalTimelineSectionEl || !signalTimelineBodyEl) return;
      const nextDocumentKey = String(workbench && workbench.video_no || base || '');
      const documentChanged = nextDocumentKey !== signalTimelineDocumentKey;
      signalTimelineState = workbench || { events: [], selected_ranges_draft: { selected_ranges: [] } };
      if (documentChanged) {
        selectedCockpitSec = 0;
        selectedCockpitEvent = null;
        selectedContextRangeEvent = null;
        selectedExactMomentEvent = null;
        selectedStoryWindowEvent = null;
        selectedEvidenceKind = '';
        adaptivePublicPreviewSelection = new Set();
        clearDraftForm();
        if (splitPlayerFrame) splitPlayerFrame.src = 'about:blank';
      }
      signalTimelineDocumentKey = nextDocumentKey;
      const events = Array.isArray(signalTimelineState.events) ? signalTimelineState.events : [];
      const draftFormHtml = `
        <div class="draft-grid">
          <label class="field-label"><span>시작 시각</span><input id="signalDraftStart" type="text" inputmode="decimal" placeholder="예: 1:23"></label>
          <label class="field-label"><span>끝 시각</span><input id="signalDraftEnd" type="text" inputmode="decimal" placeholder="예: 1:53"></label>
          <label class="field-label"><span>할 일</span><select id="signalDraftAction">
            <option value="pin">중요 장면으로 표시</option>
            <option value="ignore">이번 편집에서 제외</option>
            <option value="add">요약에 추가 검토</option>
            <option value="retime">시각 조정 필요</option>
            <option value="note">메모만 남김</option>
          </select></label>
          <input id="signalDraftSegmentId" type="hidden">
        </div>
        <input id="signalDraftEventId" type="hidden">
        <textarea id="signalDraftNote" placeholder="메모"></textarea>
        <div class="tool-actions">
          <button type="button" class="ghost" id="saveSignalDraftBtn">선택 범위 저장</button>
          <button type="button" class="ghost" id="cancelSignalDraftEditBtn" style="display:none">수정 취소</button>
        </div>
      `;
      if (signalDraftControlsEl) signalDraftControlsEl.innerHTML = draftFormHtml;
      const saveBtn = document.getElementById('saveSignalDraftBtn');
      if (saveBtn) saveBtn.addEventListener('click', saveSignalDraft);
      const cancelBtn = document.getElementById('cancelSignalDraftEditBtn');
      if (cancelBtn) cancelBtn.addEventListener('click', clearDraftForm);
      renderDraftRanges(signalTimelineState.selected_ranges_draft || {});

      signalTimelineSectionEl.style.display = '';
      if (!events.length || !String(sharedSceneFinderHtml || '').trim()) {
        signalTimelineBodyEl.innerHTML = '<div class="meta-note">현재 표시할 장면 찾기 데이터는 없습니다. 저장된 선택 범위는 아래에서 계속 관리할 수 있습니다.</div>';
        return;
      }
      signalTimelineBodyEl.innerHTML = `<div class="manager-shared-scene-finder">${sharedSceneFinderHtml}</div>`;
      if (typeof initViewerEditorTools === 'function') initViewerEditorTools();
    }

    if (!window.__chzzManagerSceneFinderSelectionBound) {
      window.__chzzManagerSceneFinderSelectionBound = true;
      document.addEventListener('click', (selectionEvent) => {
        const target = selectionEvent.target && selectionEvent.target.closest
          ? selectionEvent.target.closest('.manager-shared-scene-finder [data-event-id]')
          : null;
        if (!target) return;
        const eventId = String(target.getAttribute('data-event-id') || '');
        const managerEvent = (signalTimelineState.events || []).find(
          (row) => String(row && row.event_id || '') === eventId,
        );
        if (managerEvent) {
          renderSignalEvidence(managerEvent);
          return;
        }
        const sec = Number(target.getAttribute('data-start-sec'));
        if (Number.isFinite(sec)) selectCockpitTime(sec, eventId);
      });
    }
