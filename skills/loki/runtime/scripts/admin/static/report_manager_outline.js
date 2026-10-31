    const managerOutlineEditorEl = document.getElementById('managerOutlineEditor');
    // A normal Report Admin page can carry the same canonical BroadcastMap.
    // Keep its ordinary base/video identity so one local Save pairs it with Markdown.
    let managerOutlineContext = null;
    let reportSaveOutcome = null;
    let reportSaveChecking = false;
    let reportSavePending = false;
    let reportLastConfirmed = null;

    function confirmReportSaveAction(title, message, actionLabel) {
      const dialog = document.getElementById('reportSaveConfirmDialog');
      if (dialog.open) return Promise.resolve(false);
      document.getElementById('reportSaveConfirmTitle').textContent = title;
      document.getElementById('reportSaveConfirmMessage').textContent = message;
      document.getElementById('reportSaveConfirmAction').textContent = actionLabel;
      dialog.returnValue = '';
      return new Promise(resolve => {
        dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once: true});
        dialog.showModal();
      });
    }

    function reportOutlineEditKey(outline, packet) {
      const text = value => String(value ?? '').trim();
      return JSON.stringify({
        ranges: (outline?.ranges || []).map(row => [row.id, row.level, row.parent_id || '', row.start, row.end, text(row.title), text(row.content)]),
        points: (outline?.points || []).map(row => [row.id, row.timestamp, text(row.title), text(row.content), row.story_ref || '']),
        candidates: (outline?.candidates || []).map(row => [row.id, row.start, row.end, text(row.title), text(row.content)]),
        stories: (packet?.stories || []).map(row => [row.story_ref, text(row.why_notable)]),
        highlights: (packet?.editorial_highlights || []).map(row => [row.highlight_id, text(row.title), text(row.story_summary),
          Object.prototype.hasOwnProperty.call(row, 'selection_reason') ? text(row.selection_reason) : null, row.source_spans]),
        collaboration: packet?.collaboration_periods || [],
      }, (_key, value) => value && typeof value === 'object' && !Array.isArray(value)
        ? Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]])) : value);
    }

    function validReportSaveRead(data, attempt) {
      if (!data || data.base !== attempt.base || typeof data.md !== 'string') return false;
      if (attempt.videoNo && String(data.video_no || '') !== attempt.videoNo) return false;
      if (attempt.structured) {
        const outline = data.structured_outline;
        if (!outline || !['ranges', 'points', 'candidates'].every(key => Array.isArray(outline[key]))
            || typeof data.manager_markdown !== 'string' || typeof data.manager_edit_revision !== 'string'
            || !data.manager_edit_revision) return false;
        if (attempt.packet && (!data.qg1_review_packet || !Array.isArray(data.qg1_review_packet.editorial_highlights)
            || !Array.isArray(data.qg1_review_packet.stories))) return false;
      }
      return true;
    }

    function validReportSaveAck(data, attempt) {
      if (!validReportSaveRead(data, attempt)) return false;
      if (!attempt.structured) return data.kind === attempt.reportKind && typeof data.html === 'string'
        && (!attempt.bundleId || String(data.bundle_id || '') === attempt.bundleId)
        && data.publish_status === 'not_requested';
      if (typeof data.revision_id !== 'string' || !data.revision_id) return false;
      const authority = data.manager_outline_authority || {};
      const selected = authority.saved_revision?.revision_id || authority.revision_id;
      if (selected && selected !== data.revision_id) return false;
      if (attempt.kind === 'restore') return data.restored_from_revision_id === attempt.revisionId;
      const returned = reportOutlineEditKey(data.structured_outline, data.qg1_review_packet);
      // A pending Markdown edit is merged by stable row ID by the existing
      // save owner. Its returned rows legitimately differ from the older
      // outline sent alongside it; do not require the stale rows to win.
      if (!attempt.markdownPending && returned !== attempt.editKey) return false;
      const before = reportLastConfirmed && reportOutlineEditKey(reportLastConfirmed.structured_outline, reportLastConfirmed.qg1_review_packet);
      const markdownChanged = String(attempt.markdown || '').trim()
        !== String(reportLastConfirmed?.manager_markdown ?? savedMd).trim();
      return (!markdownChanged && returned === before) || data.manager_edit_revision !== attempt.editRevision;
    }

    function syncReportSaveRecovery() {
      const check = document.getElementById('reportSaveCheckBtn');
      const adopt = document.getElementById('reportSaveAdoptBtn');
      if (check) { check.hidden = !reportSaveOutcome; check.disabled = reportSaveChecking || reportSavePending; }
      if (adopt) { adopt.hidden = !reportSaveOutcome?.checked; adopt.disabled = reportSaveChecking || reportSavePending; }
      if (reportSaveOutcome) {
        [managerOutlineSaveBtn, document.getElementById('saveBtn'), publishBtn,
          ...managerOutlineHistoryEl.querySelectorAll('[data-outline-restore]')].filter(Boolean)
          .forEach(button => { button.disabled = true; });
      }
    }

    function assertReportSaveReady() {
      if (reportSaveOutcome || reportSaveChecking || reportSavePending
          || document.getElementById('reportSaveConfirmDialog')?.open) {
        throw Error('이전 저장·복원 결과를 먼저 확인하세요. 입력은 유지하며 같은 요청을 다시 보내지 않습니다.');
      }
    }

    function holdReportSaveOutcome(attempt, error) {
      reportSaveOutcome = {...attempt, checked: null};
      const refused = error?.serverDeclined;
      const message = refused
        ? '다른 수정본이 먼저 저장되었습니다. 초안과 기존 저장 기준을 유지했습니다. 현재 저장본을 확인한 뒤 계속하세요.'
        : '저장·복원 결과를 확인하지 못했습니다. 초안·선택·되돌리기를 유지했습니다. 같은 요청을 반복하지 말고 저장 결과를 확인하세요.';
      managerOutlineStatusEl.textContent = '저장 결과 확인 필요 · 초안 보존';
      setStatus(message, 'warn');
      syncReportSaveRecovery();
    }

    async function checkReportSaveOutcome() {
      if (!reportSaveOutcome || reportSaveChecking || reportSavePending) return;
      const attempt = reportSaveOutcome;
      reportSaveChecking = true;
      syncReportSaveRecovery();
      setStatus('현재 저장본을 읽고 있습니다. 초안은 바꾸지 않으며 저장·복원 요청은 보내지 않습니다.');
      try {
        const fresh = await api(`/api/report?base=${encodeURIComponent(attempt.base)}&view=editor`);
        if (reportSaveOutcome !== attempt) return;
        if (!validReportSaveRead(fresh, attempt)) throw Error('같은 대상의 저장본 응답을 확인하지 못했습니다.');
        if ((attempt.structured && fresh.manager_edit_revision === attempt.editRevision)
            || (!attempt.structured && attempt.md !== attempt.beforeMd && fresh.md === attempt.beforeMd)) {
          attempt.checked = null;
          const message = '아직 이전 저장본 그대로입니다. 앞선 요청이 뒤늦게 저장될 가능성을 배제할 수 없습니다. 초안과 기존 저장 기준을 유지하며 저장 결과만 다시 확인하세요.';
          managerOutlineStatusEl.textContent = '이전 저장본 그대로 · 결과 미확인';
          setStatus(message, 'warn');
          return;
        }
        attempt.checked = fresh;
        const matches = attempt.structured && attempt.kind === 'save'
          ? attempt.markdownPending ? fresh.manager_markdown.trim() === attempt.markdown.trim()
            : reportOutlineEditKey(fresh.structured_outline, fresh.qg1_review_packet) === attempt.editKey
          : !attempt.structured && fresh.md === attempt.md;
        const description = attempt.kind === 'restore' ? '현재 저장본을 조회했습니다.'
          : matches ? '현재 저장본이 제출한 내용과 일치합니다.' : '현재 저장본은 제출한 내용과 다릅니다.';
        const message = `${description} 이 조회만으로 앞선 요청의 성공을 확정하지 않습니다. 현재 초안·선택·되돌리기는 그대로입니다. 확인한 저장본으로 다시 열려면 별도로 선택하세요.`;
        managerOutlineStatusEl.textContent = '현재 저장본 조회됨 · 초안 유지';
        setStatus(message, 'warn');
      } catch (error) {
        attempt.checked = null;
        managerOutlineStatusEl.textContent = '저장본 확인 실패 · 초안 유지';
        setStatus('현재 저장본을 확인하지 못했습니다. 초안과 선택은 그대로입니다. 저장 결과 확인만 다시 할 수 있습니다.', 'err');
      } finally {
        reportSaveChecking = false;
        syncReportSaveRecovery();
      }
    }

    async function adoptReportSaveOutcome() {
      const attempt = reportSaveOutcome, fresh = attempt?.checked;
      if (!fresh || reportSaveChecking || reportSavePending) return;
      if (!await confirmReportSaveAction('확인한 저장본으로 다시 열기',
        '현재 미저장 초안과 편집 되돌리기 기록을 조회한 저장본으로 대체합니다.\n앞선 요청의 성공을 확정하거나 저장·복원·공개 요청을 다시 보내지 않습니다.', '확인한 저장본으로 열기')) return;
      if (reportSaveOutcome !== attempt || attempt.checked !== fresh) return;
      mdEditor.value = fresh.md;
      savedMd = fresh.md;
      normalReportMarkdown = fresh.md;
      reportSaveOutcome = null;
      reportLastConfirmed = fresh;
      renderManagerOutline(fresh);
      setManagerOutlineActionBusy(false);
      restoreDetailSceneSelection();
      updateDetailSceneLinks();
      setDirty(false);
      [document.getElementById('saveBtn'), publishBtn].filter(Boolean).forEach(button => { button.disabled = false; });
      syncReportSaveRecovery();
      setStatus('확인한 저장본을 채택했습니다. 저장된 화면을 불러오는 중입니다. 저장·복원·공개 요청은 다시 보내지 않습니다.', 'ok');
      await loadPreviewFrameHtml(`/api/saved-html?base=${encodeFrameParam(attempt.base)}&t=${Date.now()}`);
      setStatus('확인한 저장본을 열었습니다. 저장·복원·공개 요청은 다시 보내지 않았습니다.', 'ok');
      const status = document.getElementById('status');
      status.tabIndex = -1;
      status.focus({preventScroll: true});
    }

    async function requestReportSaveWithConfirmation(path, payload) {
      let data = await api(path, {method: 'POST', body: JSON.stringify(payload)});
      if (data?.error_code !== 'report_save_confirmation_required' || data.mayHaveRun !== false) return data;
      const change = data.destructive_change;
      if (data.requires_destructive_confirmation !== true || typeof data.confirmation_token !== 'string'
          || !data.confirmation_token || !change || !['before_chars', 'after_chars', 'removed_chars', 'remaining_percent']
            .every(key => Number.isFinite(change[key]))) throw Error('save_confirmation_response_incomplete');
      if (!await confirmReportSaveAction('본문 축소 변경 확인',
        `${change.before_chars}자 → ${change.after_chars}자 · ${change.removed_chars}자 삭제 · ${change.remaining_percent}% 남음\n확인하면 이 로컬 변경에만 승인합니다. 공개 사이트는 바뀌지 않습니다.`, '검토한 로컬 변경 실행')) {
        const error = Error('로컬 저장·복원을 취소했습니다. 초안과 저장본은 그대로입니다.');
        error.saveCanceled = true;
        throw error;
      }
      data = await api(path, {method: 'POST', body: JSON.stringify({...payload, destructive_confirmation_token: data.confirmation_token})});
      if (data?.error_code === 'report_save_confirmation_required' && data.mayHaveRun === false) {
        const error = Error('확인이 만료되었거나 본문이 바뀌었습니다. 초안과 저장본은 그대로입니다. 다시 검토하세요.');
        error.saveCanceled = true;
        throw error;
      }
      return data;
    }
    const managerOutlineGroupTabsEl = document.getElementById('managerOutlineGroupTabs');
    const managerOutlineRowsEl = document.getElementById('managerOutlineRows');
    const managerOutlineTimelineEl = document.getElementById('managerOutlineTimeline');
    const managerOutlineTimelineViewportEl = document.getElementById('managerOutlineTimelineViewport');
    const managerOutlineTimelineCanvasEl = document.getElementById('managerOutlineTimelineCanvas');
    const managerOutlineTimelineZoomEl = document.getElementById('managerOutlineTimelineZoom');
    const managerOutlineTimelineZoomReadoutEl = document.getElementById('managerOutlineTimelineZoomReadout');
    const managerOutlineTimelineFullViewBtn = document.getElementById('managerOutlineTimelineFullView');
    const managerOutlineTimelineTooltipEl = document.getElementById('managerOutlineTimelineTooltip');
    const managerOutlineDragStatusEl = document.getElementById('managerOutlineDragStatus');
    const managerOutlineInspectorEl = document.getElementById('managerOutlineInspector');
    const managerOutlineInspectorHeadingEl = document.getElementById('managerOutlineInspectorHeading');
    const managerOutlineBreadcrumbEl = document.getElementById('managerOutlineBreadcrumb');
    const selectedEvidenceSectionEl = document.getElementById('selectedEvidenceSection');
    const managerOutlineSelectionEvidenceEl = document.getElementById('managerOutlineSelectionEvidence');
    const managerOutlineSelectionEvidenceScopeEl = document.getElementById('managerOutlineSelectionEvidenceScope');
    const managerOutlineReadOnlyEl = document.getElementById('managerOutlineReadOnly');
    const managerOutlineSaveBtn = document.getElementById('managerOutlineSaveBtn');
    const reportSourceToggle = document.getElementById('reportSourceToggle');
    const reportShareToggle = document.getElementById('reportShareToggle');
    const reportReadButton = document.getElementById('reportReadButton');
    function setReportSurface(requested, updateUrl = true) {
      const surface = ['source', 'share'].includes(requested)
        && !(requested === 'share' && reportAppEl.classList.contains('manager-outline-mode')) ? requested : 'read';
      reportAppEl.dataset.reportSurface = surface;
      reportAppEl.classList.toggle('report-source-visible', surface !== 'read');
      reportSourceToggle.setAttribute('aria-expanded', String(surface === 'source'));
      reportShareToggle.setAttribute('aria-expanded', String(surface === 'share'));
      reportReadButton.setAttribute('aria-pressed', String(surface === 'read'));
      if (updateUrl) {
        const query = new URLSearchParams(location.search); query.set('tab', surface);
        window.history?.replaceState(null, '', `${location.pathname}?${query}${location.hash || ''}`);
        updateDetailSceneLinks();
      }
    }
    reportSourceToggle.addEventListener('click', () => setReportSurface(reportAppEl.dataset.reportSurface === 'source' ? 'read' : 'source'));
    reportShareToggle.addEventListener('click', () => setReportSurface(reportAppEl.dataset.reportSurface === 'share' ? 'read' : 'share'));
    reportReadButton.addEventListener('click', () => setReportSurface('read'));
    const managerOutlineAddBtn = document.getElementById('managerOutlineAddBtn');
    const managerOutlineAddMenu = document.getElementById('managerOutlineAddMenu');
    const managerOutlineUndoBtn = document.getElementById('managerOutlineUndoBtn');
    const managerOutlineStatusEl = document.getElementById('managerOutlineStatus');
    const managerOutlineHistoryEl = document.getElementById('managerOutlineHistory');
    const managerOutlineHistorySummaryEl = document.getElementById('managerOutlineHistorySummary');

    let managerOutlineState = null;
    let managerEditorialPacket = null;
    const collaborationPeriodsEditor = document.getElementById('collaborationPeriodsEditor');
    const collaborationPeriodsInput = document.getElementById('collaborationPeriodsInput');
    let collaborationPeriodsEdited = false;
    function collaborationPeriodLine(row) {
      const clock = sec => [String(Math.floor(sec / 3600)).padStart(2, '0'), String(Math.floor(sec / 60) % 60).padStart(2, '0'), (sec % 60 < 10 ? '0' : '') + String(sec % 60)].join(':');
      const vods = (row.member_vods || []).map(link => `${link.member_name}=${link.vod_no}`).join(', ');
      return `${clock(row.start_sec)} | ${clock(row.end_sec)} | ${row.members.join(', ')}` + (vods ? ` | ${vods}` : '');
    }
    if (collaborationPeriodsInput) collaborationPeriodsInput.addEventListener('input', () => {
      collaborationPeriodsEdited = true;
      setDirty(true);
    });
    let managerOutlineSelection = null;
    let managerOutlineGroup = 'range';
    let managerOutlineUndo = null;
    let managerOutlinePendingInput = null;

    function outlineInput(item, kind, field, label, wide=false) {
      const value = String(item && item[field] || '');
      const isContent = ['content', 'story_summary', 'why_notable'].includes(field);
      const isTime = ['start','end','timestamp'].includes(field);
      const inputId = `outline-${kind}-${item.id}-${field}`;
      const helpId = `${inputId}-help`;
      const errorId = `${inputId}-error`;
      const control = isContent
        ? `<textarea id="${escapeHtml(inputId)}" rows="3" placeholder="필요할 때만 내용을 입력하세요" data-outline-kind="${kind}" data-outline-id="${escapeHtml(item.id)}" data-outline-field="${field}">${escapeHtml(value)}</textarea>`
        : `<input id="${escapeHtml(inputId)}" type="text" value="${escapeHtml(value)}" placeholder="${isTime ? '00:00:00' : '제목을 입력하세요'}" data-outline-kind="${kind}" data-outline-id="${escapeHtml(item.id)}" data-outline-field="${field}" ${isTime ? `inputmode="numeric" pattern="[0-9]{2}:[0-9]{2}:[0-9]{2}" aria-invalid="false" aria-describedby="${escapeHtml(helpId)} ${escapeHtml(errorId)}"` : ''}>`;
      const helper = isTime ? `<span id="${escapeHtml(helpId)}" class="manager-outline-field-help">시:분:초</span><span id="${escapeHtml(errorId)}" class="manager-outline-field-error" data-outline-error data-outline-error-default="시:분:초 형식으로 입력하세요." hidden>시:분:초 형식으로 입력하세요.</span>` : '';
      return `<label for="${escapeHtml(inputId)}" class="${wide ? 'wide ' : ''}${isTime ? 'time' : ''}">${label}${control}${helper}</label>`;
    }

    function outlineCard(item, kind, label, relation='') {
      if (kind === 'highlight') {
        const sourceSpans = item.source_spans || [];
        const spans = sourceSpans.map((span, index) => `<li data-highlight-span-index="${index}">
          <button type="button" class="manager-outline-span-seek" data-highlight-span-seek data-highlight-span-index="${index}" data-outline-id="${escapeHtml(item.id)}" data-start="${escapeHtml(span.start_sec)}" aria-label="장면 ${index + 1}/${sourceSpans.length} ${escapeHtml(outlineHms(span.start_sec))}부터 보기">장면 ${index + 1}</button>
          <label>시작<input type="text" inputmode="numeric" value="${escapeHtml(outlineHms(span.start_sec))}" data-highlight-span-boundary="start" data-highlight-span-index="${index}" data-outline-id="${escapeHtml(item.id)}" aria-label="장면 ${index + 1} 시작"></label>
          <span aria-hidden="true">–</span>
          <label>끝<input type="text" inputmode="numeric" value="${escapeHtml(outlineHms(span.end_sec))}" data-highlight-span-boundary="end" data-highlight-span-index="${index}" data-outline-id="${escapeHtml(item.id)}" aria-label="장면 ${index + 1} 끝"></label>
          ${span.reason ? `<span class="manager-outline-span-reason">${escapeHtml(span.reason)}</span>` : ''}</li>`).join('');
        const points = (item.point_labels || []).join('\n');
        return `<article class="manager-outline-card" data-outline-card="${escapeHtml(item.id)}">
          <div class="manager-outline-fields">
            ${outlineInput(item, kind, 'title', '제목', true)}
            ${outlineInput(item, kind, 'story_summary', '이야기 요약', true)}
            ${outlineInput(item, kind, 'why_notable', '왜 볼 가치가 있나', true)}
            <label class="wide" for="outline-highlight-${escapeHtml(item.id)}-points">포함 Point<textarea id="outline-highlight-${escapeHtml(item.id)}-points" name="outline_highlight_points" readonly rows="3">${escapeHtml(points)}</textarea></label>
            <div class="wide"><strong>편집 원본 구간</strong><ol class="manager-outline-span-list">${spans}</ol><span class="manager-outline-field-help">표시값은 source_spans에서 계산합니다. 별도 Highlight 시작·끝 데이터는 만들지 않습니다.</span></div>
          </div>
        </article>`;
      }
      const fields = kind === 'point'
        ? outlineInput(item, kind, 'timestamp', '중요 내용이 시작되는 시각')
        : outlineInput(item, kind, 'start', '시작') + outlineInput(item, kind, 'end', '끝');
      const storyEvidenceField = kind === 'point' && managerEditorialPacket
        ? outlineInput(item, kind, 'story_why_notable', '이 Point를 선택한 이유 (Story 근거)', true)
          + '<span class="manager-outline-field-help wide">별도 Story 카드를 만들지 않습니다. 이 Point와 1:1인 편집 근거이며 시간 값은 갖지 않습니다.</span>'
        : '';
      return `<article class="manager-outline-card ${item.level === 'D2' ? 'd2' : ''}" data-outline-card="${escapeHtml(item.id)}">
        <div class="manager-outline-card-head"><strong>${escapeHtml(label)}</strong>${relation ? `<span class="manager-outline-link">${escapeHtml(relation)}</span>` : ''}</div>
        <div class="manager-outline-fields">
          ${outlineInput(item, kind, 'title', '제목', true)}
          ${outlineInput(item, kind, 'content', '내용', true)}
          ${storyEvidenceField}
          ${fields}
        </div>
      </article>`;
    }

    function managerOutlineCollection(kind) {
      if (!managerOutlineState) return [];
      if (kind === 'range') return managerOutlineState.ranges || [];
      if (kind === 'point') return managerOutlineState.points || [];
      if (kind === 'highlight') return managerOutlineState.highlights || [];
      return [];
    }

    function outlineSeconds(value) {
      const match = String(value || '').match(/^(\d{2}):(\d{2}):(\d{2})$/);
      return match ? Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) : 0;
    }

    function outlineHms(seconds) {
      const value = Math.max(0, Math.round(seconds));
      return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60]
        .map(part => String(part).padStart(2, '0')).join(':');
    }

    function managerOutlineDuration() {
      // ResizeObserver can run while the report is still loading. Treat the
      // not-yet-loaded outline as an empty timeline instead of throwing.
      if (!managerOutlineState) return 1;
      const actual = Number(managerOutlineState.duration_sec);
      if (Number.isFinite(actual) && actual > 0) return actual;
      const times = [];
      (managerOutlineState.ranges || []).forEach(row => times.push(outlineSeconds(row.end)));
      (managerOutlineState.points || []).forEach(row => times.push(outlineSeconds(row.timestamp)));
      (managerOutlineState.highlights || []).forEach(row => {
        (row.source_spans || []).forEach(span => times.push(Number(span.end_sec || 0)));
      });
      return Math.max(1, ...times);
    }

    const managerOutlineTooltipShowDelayMs = 400;
    let activeManagerOutlineTooltipTarget = null;
    let activeManagerOutlineTooltipDescriptionTarget = null;
    let hoveredManagerOutlineTooltipTarget = null;
    let managerOutlineTooltipPointerInside = false;
    let managerOutlineTooltipShowTimer = null;
    let managerOutlineTooltipHideTimer = null;
    let suppressedManagerOutlineTooltipKey = '';
    let suppressedManagerOutlineTooltipTarget = null;

    function managerOutlineTooltipKey(target) {
      const itemTarget = target && target.closest ? target.closest('[data-outline-timeline-id]') : null;
      return itemTarget
        ? `${itemTarget.dataset.outlineTimelineKind}:${itemTarget.dataset.outlineTimelineId}:${itemTarget.dataset.highlightSpanIndex || ''}`
        : '';
    }

    function setManagerOutlineTooltipDescription(target, visible) {
      if (!target) return;
      const ids = String(target.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
      const next = visible
        ? [...new Set([...ids, 'managerOutlineTimelineTooltip'])]
        : ids.filter(id => id !== 'managerOutlineTimelineTooltip');
      if (next.length) target.setAttribute('aria-describedby', next.join(' '));
      else target.removeAttribute('aria-describedby');
    }

    function cancelManagerOutlineTooltipShow() {
      if (managerOutlineTooltipShowTimer !== null) window.clearTimeout(managerOutlineTooltipShowTimer);
      managerOutlineTooltipShowTimer = null;
    }

    function cancelManagerOutlineTooltipHide() {
      if (managerOutlineTooltipHideTimer !== null) window.clearTimeout(managerOutlineTooltipHideTimer);
      managerOutlineTooltipHideTimer = null;
    }

    function managerOutlineTooltipText(target) {
      if (!target || !managerOutlineState) return '';
      const kind = target.dataset.outlineTimelineKind;
      const id = target.dataset.outlineTimelineId;
      const item = managerOutlineCollection(kind).find(row => row.id === id);
      if (!item) return '';
      const typeLabel = kind === 'point' ? 'Point'
        : kind === 'highlight' ? 'Highlight'
        : item.level === 'D2' ? 'D2' : 'D1';
      const title = String(item.title || (kind === 'point' ? 'Point' : kind === 'highlight' ? 'Highlight' : '요약 구간')).trim();
      if (kind === 'point') return `${typeLabel} · ${title} · ${item.timestamp || '00:00'}`;
      if (kind === 'highlight') {
        const spans = item.source_spans || [];
        const spanIndex = Number(target.dataset.highlightSpanIndex || 0);
        const span = spans[spanIndex];
        if (!span) return `${typeLabel} · ${title} · 구간 정보 없음`;
        return `${typeLabel} · ${title} · 장면 ${spanIndex + 1}/${spans.length} · ${outlineHms(span.start_sec)}–${outlineHms(span.end_sec)}`;
      }
      return `${typeLabel} · ${title} · ${item.start || '00:00'}–${item.end || item.start || '00:00'}`;
    }

    function positionManagerOutlineTooltip(target) {
      if (!target || !target.isConnected || managerOutlineTimelineTooltipEl.hidden) return;
      const targetRect = target.getBoundingClientRect();
      const tooltipRect = managerOutlineTimelineTooltipEl.getBoundingClientRect();
      const gap = 8;
      const left = Math.max(8, Math.min(window.innerWidth - tooltipRect.width - 8,
        targetRect.left + (targetRect.width - tooltipRect.width) / 2));
      const above = targetRect.top - tooltipRect.height - gap;
      const below = targetRect.bottom + gap;
      const top = targetRect.top >= tooltipRect.height + gap + 8
        ? above
        : Math.max(8, Math.min(window.innerHeight - tooltipRect.height - 8, below));
      managerOutlineTimelineTooltipEl.style.left = `${left}px`;
      managerOutlineTimelineTooltipEl.style.top = `${top}px`;
    }

    function showManagerOutlineTooltip(target) {
      const itemTarget = target && target.closest('[data-outline-timeline-id]');
      const text = managerOutlineTooltipText(itemTarget);
      if (!itemTarget || !text || suppressedManagerOutlineTooltipKey === managerOutlineTooltipKey(itemTarget)) {
        hideManagerOutlineTooltip();
        return;
      }
      cancelManagerOutlineTooltipShow();
      cancelManagerOutlineTooltipHide();
      setManagerOutlineTooltipDescription(activeManagerOutlineTooltipDescriptionTarget, false);
      activeManagerOutlineTooltipTarget = itemTarget;
      activeManagerOutlineTooltipDescriptionTarget = target.matches('[data-outline-handle]') ? target : itemTarget;
      managerOutlineTimelineTooltipEl.textContent = text;
      managerOutlineTimelineTooltipEl.hidden = false;
      setManagerOutlineTooltipDescription(activeManagerOutlineTooltipDescriptionTarget, true);
      positionManagerOutlineTooltip(itemTarget);
    }

    function hideManagerOutlineTooltip() {
      cancelManagerOutlineTooltipShow();
      cancelManagerOutlineTooltipHide();
      setManagerOutlineTooltipDescription(activeManagerOutlineTooltipDescriptionTarget, false);
      activeManagerOutlineTooltipTarget = null;
      activeManagerOutlineTooltipDescriptionTarget = null;
      managerOutlineTimelineTooltipEl.hidden = true;
    }

    function scheduleManagerOutlineTooltip(target) {
      const itemTarget = target && target.closest('[data-outline-timeline-id]');
      if (!itemTarget || suppressedManagerOutlineTooltipKey === managerOutlineTooltipKey(itemTarget)) return;
      cancelManagerOutlineTooltipShow();
      managerOutlineTooltipShowTimer = window.setTimeout(() => {
        managerOutlineTooltipShowTimer = null;
        if (hoveredManagerOutlineTooltipTarget === itemTarget) showManagerOutlineTooltip(target);
      }, managerOutlineTooltipShowDelayMs);
    }

    function scheduleManagerOutlineTooltipHide(target) {
      cancelManagerOutlineTooltipHide();
      managerOutlineTooltipHideTimer = window.setTimeout(() => {
        managerOutlineTooltipHideTimer = null;
        const itemTarget = target && target.closest ? target.closest('[data-outline-timeline-id]') : target;
        const stillHovered = hoveredManagerOutlineTooltipTarget === itemTarget || managerOutlineTooltipPointerInside;
        const stillFocused = itemTarget && itemTarget.contains(document.activeElement);
        if (!stillHovered && !stillFocused && activeManagerOutlineTooltipTarget === itemTarget) {
          hideManagerOutlineTooltip();
        }
      }, 100);
    }

    function suppressManagerOutlineTooltip(target) {
      const itemTarget = target && target.closest ? target.closest('[data-outline-timeline-id]') : activeManagerOutlineTooltipTarget;
      if (itemTarget) {
        suppressedManagerOutlineTooltipKey = managerOutlineTooltipKey(itemTarget);
        suppressedManagerOutlineTooltipTarget = itemTarget;
      }
      hideManagerOutlineTooltip();
    }

    function releaseManagerOutlineTooltipSuppression(target) {
      const itemTarget = target && target.closest ? target.closest('[data-outline-timeline-id]') : target;
      if (!itemTarget || suppressedManagerOutlineTooltipKey !== managerOutlineTooltipKey(itemTarget)) return;
      const stillHovered = hoveredManagerOutlineTooltipTarget === itemTarget || managerOutlineTooltipPointerInside;
      const stillFocused = itemTarget.contains(document.activeElement);
      if (!stillHovered && !stillFocused) {
        suppressedManagerOutlineTooltipKey = '';
        suppressedManagerOutlineTooltipTarget = null;
      }
    }

    const managerOutlineMinimumTargetPx = 44;
    const managerOutlineHandleHitPx = 44;
    let managerOutlineTimelineZoom = 1;
    let managerOutlineTimelineViewportWidth = 0;
    function applyManagerOutlineTimelineZoom(zoom, pointerX=null, preservedAnchorRatio=null) {
      const viewportWidth = managerOutlineTimelineViewportEl.clientWidth;
      const previousCanvasWidth = Math.max(1, managerOutlineTimelineCanvasEl.offsetWidth);
      const effectivePointerX = Number.isFinite(pointerX)
        ? Math.max(0, Math.min(viewportWidth, pointerX))
        : viewportWidth / 2;
      const anchorRatio = Number.isFinite(preservedAnchorRatio)
        ? preservedAnchorRatio
        : (managerOutlineTimelineViewportEl.scrollLeft + effectivePointerX) / previousCanvasWidth;
      managerOutlineTimelineZoom = Math.max(1, Math.min(8, Number(zoom) || 1));
      managerOutlineTimelineZoomEl.value = String(managerOutlineTimelineZoom);
      managerOutlineTimelineZoomReadoutEl.textContent = `${managerOutlineTimelineZoom}×`;
      const nextCanvasWidth = Math.max(viewportWidth, viewportWidth * managerOutlineTimelineZoom);
      managerOutlineTimelineCanvasEl.style.width = `${nextCanvasWidth}px`;
      managerOutlineTimelineViewportEl.scrollLeft = Math.min(
        Math.max(0, nextCanvasWidth - viewportWidth),
        Math.max(0, anchorRatio * nextCanvasWidth - effectivePointerX),
      );
      managerOutlineTimelineViewportWidth = viewportWidth;
      layoutManagerOutlineTimelineTargets();
    }

    function reapplyManagerOutlineTimelineZoomAfterResize() {
      const previousViewportWidth = managerOutlineTimelineViewportWidth || managerOutlineTimelineViewportEl.clientWidth;
      const previousCanvasWidth = Math.max(1, managerOutlineTimelineCanvasEl.offsetWidth);
      const centerRatio = (managerOutlineTimelineViewportEl.scrollLeft + previousViewportWidth / 2) / previousCanvasWidth;
      applyManagerOutlineTimelineZoom(managerOutlineTimelineZoom, null, centerRatio);
    }

    function outlineTimelineItem(item, kind, className='', highlightIndex=0) {
      const duration = managerOutlineDuration();
      const selected = managerOutlineSelection && managerOutlineSelection.kind === kind && managerOutlineSelection.id === item.id;
      if (kind === 'point') {
        const left = Math.min(100, outlineSeconds(item.timestamp) / duration * 100);
        return `<button type="button" class="manager-outline-pin" style="left:${left}%" data-outline-timeline-kind="point" data-outline-timeline-id="${escapeHtml(item.id)}" data-outline-drag-mode="move" aria-pressed="${selected ? 'true' : 'false'}" aria-label="Point ${escapeHtml(item.timestamp)} ${escapeHtml(item.title || '')}. 좌우 화살표 1초, Shift와 화살표 10초 이동"><span>${escapeHtml(item.title || 'Point')}</span></button>`;
      }
      const spans = kind === 'highlight' ? (item.source_spans || []) : [];
      if (kind === 'highlight') {
        return spans.map((span, spanIndex) => {
          const itemStart = Number(span.start_sec);
          const itemEnd = Number(span.end_sec);
          const left = Math.min(100, itemStart / duration * 100);
          const width = Math.max(0, (itemEnd - itemStart) / duration * 100);
          const current = selected && Number(managerOutlineSelection.spanIndex || 0) === spanIndex;
          const handles = current
            ? `<button type="button" class="manager-outline-handle start" data-outline-handle="start" aria-label="${escapeHtml(item.title || 'Highlight')} 장면 ${spanIndex + 1} 시작 조절"></button><button type="button" class="manager-outline-handle end" data-outline-handle="end" aria-label="${escapeHtml(item.title || 'Highlight')} 장면 ${spanIndex + 1} 끝 조절"></button>`
            : '';
          const rangeSummary = `Highlight H${highlightIndex + 1}, ${escapeHtml(item.title || '제목 없음')}, 장면 ${spanIndex + 1}/${spans.length}, ${escapeHtml(outlineHms(itemStart))}–${escapeHtml(outlineHms(itemEnd))}`;
          const label = spanIndex === 0 ? `H${highlightIndex + 1}` : '';
          return `<div role="button" tabindex="0" class="manager-outline-bar ${className} manager-outline-highlight-span${selected ? ' highlight-group-selected' : ''}${current ? ' current-span' : ''}" style="left:${left}%;width:${width}%" data-outline-range-target${selected ? ' data-outline-drag-mode="move"' : ''} data-outline-visual-left="${left}" data-outline-visual-width="${width}" data-outline-time-start="${itemStart}" data-outline-time-end="${itemEnd}" data-outline-timeline-kind="highlight" data-outline-timeline-id="${escapeHtml(item.id)}" data-highlight-span-index="${spanIndex}" data-highlight-span-count="${spans.length}" aria-pressed="${selected ? 'true' : 'false'}"${current ? ' aria-current="true"' : ''} aria-describedby="managerOutlineDragStatus" aria-label="${rangeSummary}. 이 장면을 선택하면 전체 Highlight를 열고 장면 시작으로 이동"><span class="manager-outline-bar-visual"><span class="manager-outline-bar-label">${label}</span></span>${handles}</div>`;
        }).join('');
      }
      const itemStart = outlineSeconds(item.start);
      const itemEnd = outlineSeconds(item.end);
      const left = Math.min(100, itemStart / duration * 100);
      const width = Math.max(0, (itemEnd - itemStart) / duration * 100);
      const handles = selected && ['range', 'candidate'].includes(kind)
        ? `<button type="button" class="manager-outline-handle start" data-outline-handle="start" aria-label="${escapeHtml(item.title || item.level)} 시작 조절"></button><button type="button" class="manager-outline-handle end" data-outline-handle="end" aria-label="${escapeHtml(item.title || item.level)} 끝 조절"></button>`
        : '';
      const instructions = '. 양쪽 손잡이로 시작과 끝만 조절';
      const label = escapeHtml(item.title || item.level || 'Highlight');
      const startLabel = item.start;
      const endLabel = item.end;
      const rangeSummary = `${label} · ${escapeHtml(startLabel)}–${escapeHtml(endLabel)}`;
      const dragMode = selected ? ' data-outline-drag-mode="move"' : '';
      return `<div role="button" tabindex="0" class="manager-outline-bar ${className}" style="left:${left}%;width:${width}%" data-outline-range-target${dragMode} data-outline-visual-left="${left}" data-outline-visual-width="${width}" data-outline-time-start="${itemStart}" data-outline-time-end="${itemEnd}" data-outline-timeline-kind="${kind}" data-outline-timeline-id="${escapeHtml(item.id)}" aria-pressed="${selected ? 'true' : 'false'}" aria-describedby="managerOutlineDragStatus" aria-label="${rangeSummary}${instructions}"><span class="manager-outline-bar-visual"><span class="manager-outline-bar-label">${rangeSummary}</span></span>${handles}</div>`;
    }

    function layoutManagerOutlineRangeTargets(track) {
      const trackWidth = Math.max(1, track.getBoundingClientRect().width);
      track.querySelectorAll('[data-outline-range-target]').forEach(target => {
        const visualLeft = Number(target.dataset.outlineVisualLeft) / 100 * trackWidth;
        const visualWidth = Number(target.dataset.outlineVisualWidth) / 100 * trackWidth;
        const targetWidth = Math.min(trackWidth, Math.max(managerOutlineMinimumTargetPx, visualWidth));
        const targetLeft = Math.max(0, Math.min(trackWidth - targetWidth, visualLeft + (visualWidth - targetWidth) / 2));
        target.style.left = `${targetLeft}px`;
        target.style.width = `${targetWidth}px`;
        target.style.top = '4px';
        const visual = target.querySelector('.manager-outline-bar-visual');
        target.style.setProperty('--manager-outline-visual-left', `${visualLeft - targetLeft}px`);
        target.style.setProperty('--manager-outline-visual-width', `${visualWidth}px`);
        target.classList.toggle('narrow', visualWidth < managerOutlineHandleHitPx * 2);
        visual.style.left = `${visualLeft - targetLeft}px`;
        visual.style.width = `${visualWidth}px`;
      });
    }

    function layoutManagerOutlineTimelineTargets() {
      managerOutlineTimelineCanvasEl.querySelectorAll('.manager-outline-track').forEach(track => {
        if (track.querySelector('[data-outline-range-target]')) layoutManagerOutlineRangeTargets(track);
      });
      updateManagerOutlineCandidateOverlapLabels();
      installManagerOutlineHighlightCollisionGroups();
    }

    function appendManagerHighlightClusterLabel(summary, identifierText, meaningText, count) {
      const identifier = document.createElement('span');
      const meaning = document.createElement('span');
      const badge = document.createElement('span');
      identifier.className = 'manager-outline-highlight-cluster-id';
      meaning.className = 'manager-outline-highlight-cluster-meaning';
      badge.className = 'manager-outline-highlight-cluster-count';
      identifier.textContent = identifierText;
      meaning.textContent = meaningText;
      badge.textContent = String(count);
      summary.append(identifier, meaning, badge);
    }

    function installManagerOutlineHighlightCollisionGroups() {
      const track = managerOutlineTimelineCanvasEl.querySelector('.candidate-track');
      if (!track) return;
      const openClusterKey = track.querySelector('.manager-outline-highlight-cluster[open]')?.dataset.highlightClusterKey || '';
      track.querySelectorAll('.manager-outline-highlight-cluster').forEach(node => node.remove());
      const markers = [...track.querySelectorAll('[data-outline-timeline-kind="highlight"][data-highlight-span-index]')];
      markers.forEach(marker => {
        marker.classList.remove('manager-outline-collision-source');
        marker.removeAttribute('aria-hidden');
        marker.tabIndex = 0;
      });
      const groups = [];
      markers.forEach(marker => {
        const rect = marker.getBoundingClientRect();
        const candidate = groups.find(group => rect.left <= group.right + 1 && rect.right >= group.left - 1);
        if (candidate) {
          candidate.markers.push(marker);
          candidate.left = Math.min(candidate.left, rect.left);
          candidate.right = Math.max(candidate.right, rect.right);
        } else {
          groups.push({left: rect.left, right: rect.right, markers: [marker]});
        }
      });
      groups.filter(group => group.markers.length > 1).forEach(group => {
        const highlightIds = [...new Set(group.markers.map(marker => marker.dataset.outlineTimelineId))];
        const clusterKey = group.markers
          .map(marker => `${marker.dataset.outlineTimelineId}:${marker.dataset.highlightSpanIndex}`)
          .sort()
          .join('|');
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        const list = document.createElement('div');
        details.className = 'manager-outline-highlight-cluster';
        details.dataset.highlightClusterKey = clusterKey;
        if (openClusterKey === clusterKey) details.open = true;
        details.style.left = `${Math.max(0, Math.min(...group.markers.map(marker => marker.offsetLeft)))}px`;
        summary.className = 'manager-outline-highlight-cluster-trigger';
        list.className = 'manager-outline-highlight-cluster-list';
        if (highlightIds.length === 1) {
          const highlightIndex = (managerOutlineState.highlights || []).findIndex(row => row.id === highlightIds[0]);
          const highlight = (managerOutlineState.highlights || [])[highlightIndex];
          const spanCount = (highlight && highlight.source_spans || []).length;
          appendManagerHighlightClusterLabel(summary, `H${highlightIndex + 1}`, '연결 장면', spanCount);
          summary.setAttribute('aria-label', `Highlight H${highlightIndex + 1}, 연결 장면 ${spanCount}개 펼치기`);
          summary.title = `H${highlightIndex + 1} · 연결 장면 ${spanCount}개`;
        } else {
          appendManagerHighlightClusterLabel(summary, 'H', '겹침', highlightIds.length);
          summary.setAttribute('aria-label', `현재 축에서 겹친 Highlight ${highlightIds.length}개 펼치기`);
          summary.title = `현재 축에서 겹친 Highlight ${highlightIds.length}개`;
        }
        const selectedCluster = managerOutlineSelection
          && managerOutlineSelection.kind === 'highlight'
          && highlightIds.includes(managerOutlineSelection.id);
        summary.classList.toggle('selected', Boolean(selectedCluster));
        summary.setAttribute('aria-pressed', String(Boolean(selectedCluster)));
        group.markers.forEach(marker => {
          marker.classList.add('manager-outline-collision-source');
          marker.setAttribute('aria-hidden', 'true');
          marker.tabIndex = -1;
          const id = marker.dataset.outlineTimelineId;
          const spanIndex = Number(marker.dataset.highlightSpanIndex || 0);
          const highlightIndex = (managerOutlineState.highlights || []).findIndex(row => row.id === id);
          const highlight = (managerOutlineState.highlights || [])[highlightIndex];
          const spans = highlight && highlight.source_spans || [];
          const span = spans[spanIndex];
          const member = document.createElement('button');
          member.type = 'button';
          member.dataset.outlineTimelineKind = 'highlight';
          member.dataset.outlineTimelineId = id;
          member.dataset.highlightSpanIndex = String(spanIndex);
          member.textContent = `H${highlightIndex + 1} · 장면 ${spanIndex + 1}/${spans.length} · ${outlineHms(span.start_sec)}–${outlineHms(span.end_sec)}`;
          member.setAttribute('aria-label', `${highlight.title || `Highlight H${highlightIndex + 1}`}, 장면 ${spanIndex + 1}/${spans.length}, ${outlineHms(span.start_sec)}–${outlineHms(span.end_sec)}`);
          list.append(member);
        });
        details.append(summary, list);
        track.append(details);
      });
    }

    function updateManagerOutlineCandidateOverlapLabels() {
      const candidates = [...managerOutlineTimelineCanvasEl.querySelectorAll('[data-outline-range-target][data-outline-timeline-kind="candidate"]')];
      candidates.forEach(target => {
        const start = Number(target.dataset.outlineTimeStart);
        const end = Number(target.dataset.outlineTimeEnd);
        const overlaps = candidates.some(other => {
          if (other === target) return false;
          const otherStart = Number(other.dataset.outlineTimeStart);
          const otherEnd = Number(other.dataset.outlineTimeEnd);
          return start < otherEnd && end > otherStart;
        });
        target.classList.toggle('candidate-overlap', overlaps);
      });
    }

    function renderManagerOutlineTimeline() {
      const tooltipWasVisible = !!activeManagerOutlineTooltipTarget && !managerOutlineTimelineTooltipEl.hidden;
      const tooltipKey = activeManagerOutlineTooltipTarget && activeManagerOutlineTooltipTarget.isConnected
        ? {
            kind: activeManagerOutlineTooltipTarget.dataset.outlineTimelineKind,
            id: activeManagerOutlineTooltipTarget.dataset.outlineTimelineId,
            spanIndex: activeManagerOutlineTooltipTarget.dataset.highlightSpanIndex,
            moveGrip: activeManagerOutlineTooltipTarget.matches('[data-outline-move-grip]'),
          }
        : null;
      hideManagerOutlineTooltip();
      if (!managerOutlineState) return;
      const ranges = managerOutlineState.ranges || [];
      const selectedD1 = managerOutlineSelection && managerOutlineSelection.kind === 'range'
        ? ranges.find(row => row.id === managerOutlineSelection.id && row.level === 'D1')
        : null;
      const d2Parent = managerOutlineSelection && managerOutlineSelection.kind === 'range'
        ? ranges.find(row => row.id === managerOutlineSelection.id && row.level === 'D2')
        : null;
      const parentId = selectedD1 ? selectedD1.id : d2Parent ? d2Parent.parent_id : '';
      const duration = managerOutlineDuration();
      const selectedHighlightIndex = managerOutlineSelection && managerOutlineSelection.kind === 'highlight'
        ? (managerOutlineState.highlights || []).findIndex(row => row.id === managerOutlineSelection.id)
        : -1;
      const selectedHighlight = selectedHighlightIndex >= 0
        ? (managerOutlineState.highlights || [])[selectedHighlightIndex]
        : null;
      const selectedHighlightStatus = selectedHighlight
        ? `<span class="manager-outline-highlight-selection-status">H${selectedHighlightIndex + 1} 선택 · ${(selectedHighlight.source_spans || []).length}장면</span>`
        : '';
      const tracks = [
        `<div class="manager-outline-track" data-outline-window-duration="${duration}"><span class="manager-outline-track-label">D1</span>${ranges.filter(row => row.level === 'D1').map(row => outlineTimelineItem(row, 'range')).join('')}</div>`,
        parentId ? `<div class="manager-outline-track" data-outline-window-duration="${duration}"><span class="manager-outline-track-label">선택 D1의 D2</span>${ranges.filter(row => row.level === 'D2' && row.parent_id === parentId).map(row => outlineTimelineItem(row, 'range', 'd2')).join('')}</div>` : '',
        `<div class="manager-outline-track" data-outline-window-duration="${duration}"><span class="manager-outline-track-label">Point</span>${(managerOutlineState.points || []).map(row => outlineTimelineItem(row, 'point')).join('')}</div>`,
        `<div class="manager-outline-track candidate-track${selectedHighlight ? ' highlight-group-active' : ''}" data-outline-window-duration="${duration}"><span class="manager-outline-track-label">Highlight${selectedHighlightStatus}</span>${(managerOutlineState.highlights || []).map((row, highlightIndex) => outlineTimelineItem(row, 'highlight', 'candidate', highlightIndex)).join('')}</div>`,
      ];
      const axis = `<div class="manager-outline-axis" aria-label="방송 전체 ${escapeHtml(outlineHms(duration))}">${[0, .25, .5, .75, 1].map(ratio => `<span class="manager-outline-tick" style="left:${ratio * 100}%">${escapeHtml(outlineHms(duration * ratio))}</span>`).join('')}</div>`;
      // Manager-authored values are escaped above before this trusted layout is assigned.
      managerOutlineTimelineCanvasEl.innerHTML = axis + tracks.filter(Boolean).join('');
      applyManagerOutlineTimelineZoom(managerOutlineTimelineZoom);
      if (tooltipWasVisible && tooltipKey) {
        const selector = `[data-outline-timeline-kind="${CSS.escape(tooltipKey.kind)}"][data-outline-timeline-id="${CSS.escape(tooltipKey.id)}"]${tooltipKey.spanIndex != null ? `[data-highlight-span-index="${CSS.escape(tooltipKey.spanIndex)}"]` : ''}${tooltipKey.moveGrip ? '[data-outline-move-grip]' : ':not([data-outline-move-grip])'}`;
        showManagerOutlineTooltip(managerOutlineTimelineEl.querySelector(selector));
      }
    }

    function validateOutlineDrag(item, original) {
      const start = outlineSeconds(item.start);
      const end = outlineSeconds(item.end);
      const duration = managerOutlineDuration();
      if (start < 0 || end > duration) return '방송 범위를 벗어날 수 없어요.';
      if (end <= start) return '끝은 시작보다 뒤여야 해요.';
      const ranges = managerOutlineState.ranges || [];
      if (item.level === 'D2') {
        const parent = ranges.find(row => row.id === item.parent_id);
        if (!parent || start < outlineSeconds(parent.start) || end > outlineSeconds(parent.end)) return 'D2는 소속 D1 안에 있어야 해요.';
        const siblings = ranges.filter(row => row.level === 'D2' && row.parent_id === item.parent_id && row.id !== item.id);
        if (siblings.some(row => start < outlineSeconds(row.end) && end > outlineSeconds(row.start))) return '같은 D1의 D2끼리는 겹칠 수 없어요.';
      } else {
        const children = ranges.filter(row => row.level === 'D2' && row.parent_id === item.id);
        if (children.some(row => outlineSeconds(row.start) < start || outlineSeconds(row.end) > end)) return 'D1은 소속 D2를 모두 포함해야 해요.';
        const siblings = ranges.filter(row => row.level === 'D1' && row.id !== item.id);
        if (siblings.some(row => start < outlineSeconds(row.end) && end > outlineSeconds(row.start))) return 'D1끼리는 겹칠 수 없어요.';
      }
      return '';
    }

    function restoreInvalidManagerOutlineManipulation(snapshot, reason) {
      restoreManagerOutlineSnapshot(snapshot);
      managerOutlineDragStatusEl.textContent = `원래 위치로 돌아갔어요. ${reason}`;
      managerOutlineStatusEl.textContent = `변경하지 않았어요. ${reason}`;
      renderManagerOutlineNavigator();
      renderManagerOutlineInspector();
      renderManagerOutlineTimeline();
      return false;
    }

    function refreshManagerOutlineTimelineGeometry() {
      const duration = managerOutlineDuration();
      managerOutlineTimelineCanvasEl.querySelectorAll('[data-outline-timeline-kind="point"]').forEach(element => {
        const point = (managerOutlineState.points || []).find(row => row.id === element.dataset.outlineTimelineId);
        if (point) element.style.left = `${outlineSeconds(point.timestamp) / duration * 100}%`;
      });
      managerOutlineTimelineCanvasEl.querySelectorAll('[data-outline-range-target]').forEach(element => {
        const kind = element.dataset.outlineTimelineKind;
        const item = managerOutlineCollection(kind).find(row => row.id === element.dataset.outlineTimelineId);
        if (!item) return;
        const spans = kind === 'highlight' ? (item.source_spans || []) : [];
        const span = kind === 'highlight' ? spans[Number(element.dataset.highlightSpanIndex || 0)] : null;
        const itemStart = span ? Number(span.start_sec) : outlineSeconds(item.start);
        const itemEnd = span ? Number(span.end_sec) : outlineSeconds(item.end);
        const left = itemStart / duration * 100;
        const width = Math.max(0, (itemEnd - itemStart) / duration * 100);
        element.dataset.outlineVisualLeft = String(left);
        element.dataset.outlineVisualWidth = String(width);
        element.dataset.outlineTimeStart = String(itemStart);
        element.dataset.outlineTimeEnd = String(itemEnd);
      });
      layoutManagerOutlineTimelineTargets();
      if (activeManagerOutlineTooltipTarget) positionManagerOutlineTooltip(activeManagerOutlineTooltipTarget);
    }

    function finishManagerOutlineManipulation(label, snapshot, statusText, live=false) {
      if (!live) {
        rememberManagerOutlineChange(label, snapshot);
        setDirty(true);
      }
      managerOutlineDragStatusEl.textContent = statusText;
      if (live) {
        refreshManagerOutlineTimelineGeometry();
      } else {
        renderManagerOutlineTimeline();
        renderManagerOutlineNavigator();
        renderManagerOutlineInspector();
      }
      if (!live) scheduleManagerOutlineDraftSync('outline');
      return true;
    }

    function movePoint(pointId, seconds, snapshot, live=false, invalidControl=null) {
      const originalPoint = (snapshot.state.points || []).find(row => row.id === pointId);
      if (!originalPoint) return false;
      const duration = managerOutlineDuration();
      const target = Math.round(seconds);
      if (target < 0 || target > duration) {
        const reason = '방송 범위를 벗어날 수 없어요.';
        if (invalidControl) {
          restoreManagerOutlineSnapshot(snapshot);
          setManagerOutlineFieldError(invalidControl, reason, 'range');
          managerOutlineStatusEl.textContent = reason;
          managerOutlineDragStatusEl.textContent = `변경하지 않았어요. ${reason}`;
          renderManagerOutlineNavigator();
          renderManagerOutlineTimeline();
          invalidControl.focus({preventScroll: true});
          return false;
        }
        return restoreInvalidManagerOutlineManipulation(snapshot, reason);
      }
      const point = (managerOutlineState.points || []).find(row => row.id === pointId);
      if (!point) return false;
      point.timestamp = outlineHms(target);
      return finishManagerOutlineManipulation(
        'Point 이동',
        snapshot,
        `${live ? '이동 중' : '이동 완료'}: Point ${point.timestamp}`,
        live,
      );
    }

    function validateCandidateGeometry(candidate) {
      const start = outlineSeconds(candidate.start);
      const end = outlineSeconds(candidate.end);
      if (start < 0 || end > managerOutlineDuration()) return '검토 범위는 방송 범위를 벗어날 수 없어요.';
      if (end <= start) return '검토 범위의 끝은 시작보다 뒤여야 해요.';
      return '';
    }

    function highlightBoundarySeconds(highlight, edge) {
      const spans = highlight && Array.isArray(highlight.source_spans) ? highlight.source_spans : [];
      if (!spans.length) return NaN;
      return Number(edge === 'start' ? spans[0].start_sec : spans[spans.length - 1].end_sec);
    }

    function validateHighlightGeometry(highlight) {
      const spans = highlight && Array.isArray(highlight.source_spans) ? highlight.source_spans : [];
      if (!spans.length) return 'Highlight 편집 구간이 없습니다.';
      let previousEnd = -1;
      for (const span of spans) {
        const start = Number(span.start_sec);
        const end = Number(span.end_sec);
        if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end > managerOutlineDuration()) {
          return 'Highlight는 방송 범위를 벗어날 수 없어요.';
        }
        if (end <= start) return 'Highlight의 끝은 시작보다 뒤여야 해요.';
        if (start < previousEnd) return 'Highlight의 편집 구간은 시간순이어야 하고 서로 겹칠 수 없어요.';
        previousEnd = end;
      }
      return '';
    }

    function moveHighlightRange(highlightId, deltaSeconds, snapshot, live=false) {
      const original = (snapshot.state.highlights || []).find(row => row.id === highlightId);
      const highlight = (managerOutlineState.highlights || []).find(row => row.id === highlightId);
      if (!original || !highlight) return false;
      const delta = Math.round(deltaSeconds);
      highlight.source_spans = (original.source_spans || []).map(span => ({
        ...span,
        start_sec: Number(span.start_sec) + delta,
        end_sec: Number(span.end_sec) + delta,
      }));
      const reason = validateHighlightGeometry(highlight);
      if (reason) return restoreInvalidManagerOutlineManipulation(snapshot, reason);
      return finishManagerOutlineManipulation(
        'Highlight 전체 이동',
        snapshot,
        `${live ? '이동 중' : '이동 완료'}: Highlight ${outlineHms(highlightBoundarySeconds(highlight, 'start'))}–${outlineHms(highlightBoundarySeconds(highlight, 'end'))} · 내부 구간 유지`,
        live,
      );
    }

    function resizeHighlightRange(highlightId, edge, seconds, snapshot, live=false) {
      const highlight = (managerOutlineState.highlights || []).find(row => row.id === highlightId);
      if (!highlight || !(highlight.source_spans || []).length) return false;
      const target = Math.round(seconds);
      const spans = highlight.source_spans;
      if (edge === 'start') spans[0].start_sec = target;
      else spans[spans.length - 1].end_sec = target;
      const reason = validateHighlightGeometry(highlight);
      if (reason) return restoreInvalidManagerOutlineManipulation(snapshot, reason);
      return finishManagerOutlineManipulation(
        `Highlight ${edge === 'start' ? '시작' : '끝'} 조절`,
        snapshot,
        `${live ? '조절 중' : '조절 완료'}: Highlight ${outlineHms(highlightBoundarySeconds(highlight, 'start'))}–${outlineHms(highlightBoundarySeconds(highlight, 'end'))}`,
        live,
      );
    }

    function highlightSpanBoundarySeconds(highlight, spanIndex, edge) {
      const span = highlight && (highlight.source_spans || [])[spanIndex];
      return span ? Number(span[edge === 'start' ? 'start_sec' : 'end_sec']) : NaN;
    }

    function moveHighlightSpan(highlightId, spanIndex, deltaSeconds, snapshot, live=false) {
      const original = (snapshot.state.highlights || []).find(row => row.id === highlightId);
      const highlight = (managerOutlineState.highlights || []).find(row => row.id === highlightId);
      const originalSpan = original && (original.source_spans || [])[spanIndex];
      const span = highlight && (highlight.source_spans || [])[spanIndex];
      if (!originalSpan || !span) return false;
      const delta = Math.round(deltaSeconds);
      span.start_sec = Number(originalSpan.start_sec) + delta;
      span.end_sec = Number(originalSpan.end_sec) + delta;
      const reason = validateHighlightGeometry(highlight);
      if (reason) return restoreInvalidManagerOutlineManipulation(snapshot, reason);
      managerOutlineSelection = {kind: 'highlight', id: highlightId, spanIndex};
      return finishManagerOutlineManipulation(
        `Highlight 장면 ${spanIndex + 1} 이동`, snapshot,
        `${live ? '이동 중' : '이동 완료'}: 장면 ${spanIndex + 1} ${outlineHms(span.start_sec)}–${outlineHms(span.end_sec)}`,
        live,
      );
    }

    function resizeHighlightSpan(highlightId, spanIndex, edge, seconds, snapshot, live=false) {
      const highlight = (managerOutlineState.highlights || []).find(row => row.id === highlightId);
      const span = highlight && (highlight.source_spans || [])[spanIndex];
      if (!span) return false;
      span[edge === 'start' ? 'start_sec' : 'end_sec'] = Math.round(seconds);
      const reason = validateHighlightGeometry(highlight);
      if (reason) return restoreInvalidManagerOutlineManipulation(snapshot, reason);
      managerOutlineSelection = {kind: 'highlight', id: highlightId, spanIndex};
      return finishManagerOutlineManipulation(
        `Highlight 장면 ${spanIndex + 1} ${edge === 'start' ? '시작' : '끝'} 조절`, snapshot,
        `${live ? '조절 중' : '조절 완료'}: 장면 ${spanIndex + 1} ${outlineHms(span.start_sec)}–${outlineHms(span.end_sec)}`,
        live,
      );
    }

    function moveCandidateRange(candidateId, deltaSeconds, snapshot, live=false) {
      const original = (snapshot.state.candidates || []).find(row => row.id === candidateId);
      const candidate = (managerOutlineState.candidates || []).find(row => row.id === candidateId);
      if (!original || !candidate) return false;
      const delta = Math.round(deltaSeconds);
      const targetStart = outlineSeconds(original.start) + delta;
      const targetEnd = outlineSeconds(original.end) + delta;
      if (targetStart < 0 || targetEnd > managerOutlineDuration()) {
        return restoreInvalidManagerOutlineManipulation(snapshot, '검토 범위는 방송 범위를 벗어날 수 없어요.');
      }
      candidate.start = outlineHms(targetStart);
      candidate.end = outlineHms(targetEnd);
      const reason = validateCandidateGeometry(candidate);
      if (reason) return restoreInvalidManagerOutlineManipulation(snapshot, reason);
      return finishManagerOutlineManipulation(
        '검토 범위 전체 이동',
        snapshot,
        `${live ? '이동 중' : '이동 완료'}: 검토 범위 ${candidate.start}–${candidate.end} · 길이 유지`,
        live,
      );
    }

    function moveOutlineRange(rangeId, deltaSeconds, snapshot, live=false) {
      const original = (snapshot.state.ranges || []).find(row => row.id === rangeId);
      const item = (managerOutlineState.ranges || []).find(row => row.id === rangeId);
      if (!original || !item) return false;
      const delta = Math.round(deltaSeconds);
      const targetStart = outlineSeconds(original.start) + delta;
      const targetEnd = outlineSeconds(original.end) + delta;
      if (targetStart < 0 || targetEnd > managerOutlineDuration()) {
        return restoreInvalidManagerOutlineManipulation(snapshot, '방송 범위를 벗어날 수 없어요.');
      }
      item.start = outlineHms(targetStart);
      item.end = outlineHms(targetEnd);
      const reason = validateOutlineDrag(item, original);
      if (reason) return restoreInvalidManagerOutlineManipulation(snapshot, reason);
      return finishManagerOutlineManipulation(
        `${item.level} 전체 이동`,
        snapshot,
        `${live ? '이동 중' : '이동 완료'}: ${item.level} ${item.start}–${item.end} · 길이 유지`,
        live,
      );
    }

    function resizeCandidateRange(candidateId, edge, seconds, snapshot, live=false) {
      const candidate = (managerOutlineState.candidates || []).find(row => row.id === candidateId);
      if (!candidate) return false;
      if (seconds < 0 || seconds > managerOutlineDuration()) {
        return restoreInvalidManagerOutlineManipulation(snapshot, '검토 범위는 방송 범위를 벗어날 수 없어요.');
      }
      candidate[edge] = outlineHms(Math.round(seconds));
      const reason = validateCandidateGeometry(candidate);
      if (reason) return restoreInvalidManagerOutlineManipulation(snapshot, reason);
      return finishManagerOutlineManipulation(
        `검토 범위 ${edge === 'start' ? '시작' : '끝'} 조절`,
        snapshot,
        `${live ? '조절 중' : '조절 완료'}: 검토 범위 ${candidate.start}–${candidate.end}`,
        live,
      );
    }

    function resizeOutlineRange(rangeId, edge, seconds, snapshot, live=false) {
      const item = (managerOutlineState.ranges || []).find(row => row.id === rangeId);
      const original = (snapshot.state.ranges || []).find(row => row.id === rangeId);
      if (!item || !original) return false;
      if (seconds < 0 || seconds > managerOutlineDuration()) {
        return restoreInvalidManagerOutlineManipulation(snapshot, '방송 범위를 벗어날 수 없어요.');
      }
      item[edge] = outlineHms(Math.round(seconds));
      const reason = validateOutlineDrag(item, original);
      if (reason) return restoreInvalidManagerOutlineManipulation(snapshot, reason);
      return finishManagerOutlineManipulation(
        `${item.level} ${edge === 'start' ? '시작' : '끝'} 조절`,
        snapshot,
        `${live ? '조절 중' : '조절 완료'}: ${item.level} ${item.start}–${item.end}`,
        live,
      );
    }

    function outlineNavItem(item, kind, label, relation='', child=false, nestedChildren='') {
      const selected = managerOutlineSelection
        && managerOutlineSelection.kind === kind
        && managerOutlineSelection.id === item.id;
      const spans = kind === 'highlight' ? (item.source_spans || []) : [];
      const time = kind === 'point' ? item.timestamp
        : kind === 'highlight' && spans.length
          ? `${outlineHms(spans[0].start_sec)}–${outlineHms(spans[spans.length - 1].end_sec)}`
          : `${item.start}–${item.end}`;
      return `<div role="treeitem" class="manager-outline-node ${child ? 'manager-outline-child' : ''}" data-outline-select-kind="${kind}" data-outline-select-id="${escapeHtml(item.id)}" aria-selected="${selected ? 'true' : 'false'}" tabindex="${selected ? '0' : '-1'}"${item.level === 'D1' ? ' aria-expanded="true"' : ''}>
        <div class="manager-outline-nav-item">
          <span class="manager-outline-type">${escapeHtml(label)}</span>
          <strong class="manager-outline-nav-title">${escapeHtml(item.title || '제목 없음')}</strong>
          <span class="manager-outline-nav-meta">${escapeHtml(time)}${relation ? ` · ${escapeHtml(relation)}` : ''}</span>
          ${selected ? '<span class="manager-outline-current">현재 선택</span>' : ''}
        </div>${nestedChildren}
      </div>`;
    }

    function managerOutlineRangeTree(ranges) {
      const d1Rows = ranges.filter(item => item.level === 'D1');
      const d2Rows = ranges.filter(item => item.level === 'D2');
      const owned = new Set();
      const parents = d1Rows.map(parent => {
        const children = d2Rows.filter(child => child.parent_id === parent.id);
        children.forEach(child => owned.add(child.id));
        const childHtml = children.length
          ? `<div role="group" class="manager-outline-children">${children.map(child => outlineNavItem(child, 'range', 'D2', `소속 ${parent.title || parent.id}`, true)).join('')}</div>`
          : '';
        return outlineNavItem(parent, 'range', 'D1', '', false, childHtml);
      }).join('');
      const orphans = d2Rows.filter(child => !owned.has(child.id)).map(child => outlineNavItem(child, 'range', 'D2', '소속 D1 확인 필요', true)).join('');
      return `<div role="group" class="manager-outline-tree">${parents}${orphans}</div>`;
    }

    function renderManagerOutlineNavigator() {
      const ranges = managerOutlineState.ranges || [];
      const points = managerOutlineState.points || [];
      const highlights = managerOutlineState.highlights || [];
      const groups = [
        ['range', '구간', ranges.length],
        ['point', 'Point', points.length],
        ['highlight', 'Highlight', highlights.length],
      ];
      managerOutlineGroupTabsEl.innerHTML = groups.map(([kind, label, count]) => {
        const selected = managerOutlineGroup === kind;
        return `<button type="button" role="tab" class="manager-outline-segment" data-outline-group="${kind}" aria-selected="${selected ? 'true' : 'false'}" aria-controls="managerOutlineRows" tabindex="${selected ? '0' : '-1'}"><span>${label}</span> <strong class="manager-outline-segment-count">${count}</strong></button>`;
      }).join('');
      if (managerOutlineGroup === 'range') {
        managerOutlineRowsEl.innerHTML = ranges.length ? managerOutlineRangeTree(ranges) : '<p class="manager-outline-empty">구간이 없습니다.</p>';
      } else if (managerOutlineGroup === 'point') {
        managerOutlineRowsEl.innerHTML = points.length ? points.map(item => outlineNavItem(item, 'point', 'Point')).join('') : '<p class="manager-outline-empty">Point가 없습니다.</p>';
      } else {
        managerOutlineRowsEl.innerHTML = highlights.length ? highlights.map(item => outlineNavItem(item, 'highlight', 'Highlight')).join('') : '<p class="manager-outline-empty">승인된 Highlight가 없습니다.</p>';
      }
      updateManagerOutlineAddMenu();
    }

    function updateManagerOutlineAddMenu() {
      const selectedD1 = managerOutlineSelection && managerOutlineSelection.kind === 'range'
        ? (managerOutlineState.ranges || []).find(row => row.id === managerOutlineSelection.id && row.level === 'D1')
        : null;
      const rules = {
        D2: {enabled: !!selectedD1, reason: 'D1을 먼저 선택하면 추가할 수 있어요.'},
      };
      Object.entries(rules).forEach(([kind, rule]) => {
        const button = managerOutlineAddMenu.querySelector(`[data-outline-add="${kind}"]`);
        const reason = managerOutlineAddMenu.querySelector(`[data-outline-add-reason="${kind}"]`);
        if (!button) return;
        button.disabled = !rule.enabled;
        button.title = rule.enabled ? '' : rule.reason;
        if (reason) {
          reason.hidden = rule.enabled;
          reason.textContent = rule.enabled ? '' : rule.reason;
        }
      });
    }

    function syncManagerOutlineInspector() {
      managerOutlineInspectorEl.querySelectorAll('[data-outline-field]').forEach(control => {
        const item = managerOutlineCollection(control.dataset.outlineKind).find(row => row.id === control.dataset.outlineId);
        if (item) item[control.dataset.outlineField] = control.value;
      });
    }

    const managerOutlineLayoutButtons = [...document.querySelectorAll('[data-outline-layout]')];
    const managerOutlineLayoutKey = 'reportAdmin.managerOutlineLayout';
    let managerOutlineLayout = 'horizontal';

    function applyManagerOutlineLayout(layout, persist = false) {
      const workspace = document.querySelector('.manager-outline-workspace');
      managerOutlineLayout = layout === 'vertical' ? 'vertical' : 'horizontal';
      if (workspace) workspace.dataset.layout = managerOutlineLayout;
      managerOutlineLayoutButtons.forEach(button => {
        button.setAttribute('aria-pressed', button.dataset.outlineLayout === managerOutlineLayout ? 'true' : 'false');
      });
      if (persist) {
        try { localStorage.setItem(managerOutlineLayoutKey, managerOutlineLayout); } catch (_) {}
      }
      requestAnimationFrame(() => {
        reapplyManagerOutlineTimelineZoomAfterResize();
        refreshManagerOutlineTimelineGeometry();
      });
    }

    try { managerOutlineLayout = localStorage.getItem(managerOutlineLayoutKey) || managerOutlineLayout; } catch (_) {}
    managerOutlineLayoutButtons.forEach(button => {
      button.addEventListener('click', () => applyManagerOutlineLayout(button.dataset.outlineLayout, true));
    });
    applyManagerOutlineLayout(managerOutlineLayout);

    let managerOutlineSyncTimer = 0;
    let managerOutlineSyncSequence = 0;
    let managerOutlineMarkdownPending = false;

    let managerOutlinePreviewTimer = 0;

    function scheduleManagerOutlineLivePreview() {
      if (!managerOutlineContext || !managerOutlineState) return;
      window.clearTimeout(managerOutlinePreviewTimer);
      managerOutlinePreviewTimer = window.setTimeout(() => {
        previewCurrent().catch(error => {
          managerOutlineStatusEl.textContent = `Markdown은 반영했지만 HTML 미리보기는 갱신하지 못했어요. ${error && error.message ? error.message : error}`;
        });
      }, 180);
    }

    function scheduleManagerOutlineDraftSync(direction) {
      if (managerOutlineBusy) return;
      if (!managerOutlineEditorEl.classList.contains('manager-outline-docked')) return;
      if (direction === 'markdown') managerOutlineMarkdownPending = true;
      ++managerOutlineSyncSequence;
      managerOutlineStatusEl.textContent = direction === 'markdown' ? 'Markdown을 방송 목차에 맞추는 중...' : '방송 목차를 Markdown에 맞추는 중...';
      window.clearTimeout(managerOutlineSyncTimer);
      managerOutlineSyncTimer = window.setTimeout(() => { syncManagerOutlineDraft(direction).catch(() => {}); }, direction === 'markdown' ? 320 : 80);
    }

    async function syncManagerOutlineDraft(direction) {
      if (managerOutlineBusy) return;
      if (!managerOutlineEditorEl.classList.contains('manager-outline-docked') || !managerOutlineState) return;
      if (direction === 'outline' && managerOutlineMarkdownPending) {
        managerOutlineStatusEl.textContent = '입력 중인 Markdown을 보존했어요. Markdown을 확인한 뒤 미리보기 또는 저장을 눌러 주세요.';
        return;
      }
      syncManagerOutlineInspector();
      const sequence = ++managerOutlineSyncSequence;
      try {
        const data = await api('/api/manager-outline/sync', {method: 'POST', body: JSON.stringify({
          video_no: managerOutlineContext.videoNo, base: managerOutlineContext.base || base, direction,
          outline: collectManagerOutline(), manager_markdown: mdEditor.value,
          editorial_review_packet: collectEditorialReviewPacket(),
        })});
        if (sequence !== managerOutlineSyncSequence) return;
        if (direction === 'markdown') {
          managerOutlineMarkdownPending = false;
          managerOutlineState = JSON.parse(JSON.stringify(data.structured_outline));
          if (data.qg1_review_packet) managerEditorialPacket = JSON.parse(JSON.stringify(data.qg1_review_packet));
          hydrateManagerPointStories();
          managerOutlineState.highlights = managerEditorialHighlights(managerEditorialPacket, managerOutlineState.points || []);
          renderManagerOutlineNavigator(); renderManagerOutlineInspector(); renderManagerOutlineTimeline();
          managerOutlineStatusEl.textContent = 'Markdown 내용을 방송 목차 편집에 반영했어요. 아직 저장되지 않았어요.';
        } else if (typeof data.manager_markdown === 'string') {
          const start = mdEditor.selectionStart; const end = mdEditor.selectionEnd;
          mdEditor.value = data.manager_markdown;
          if (document.activeElement === mdEditor) mdEditor.setSelectionRange(Math.min(start, mdEditor.value.length), Math.min(end, mdEditor.value.length));
          managerOutlineStatusEl.textContent = '방송 목차 편집 내용을 Markdown에 반영했어요. 아직 저장되지 않았어요.';
        }
        setDirty(true);
        scheduleManagerOutlineLivePreview();
      } catch (error) {
        if (sequence !== managerOutlineSyncSequence) return;
        managerOutlineStatusEl.textContent = `아직 반영하지 않았어요. ${error && error.message ? error.message : error}`;
      }
    }

    function selectManagerOutlineAtTime(sec) {
      if (!managerOutlineState || !Number.isFinite(sec)) return;
      const points = (managerOutlineState.points || []).map(row => ({kind:'point', row, distance:Math.abs(outlineSeconds(row.timestamp) - sec)})).sort((a,b) => a.distance - b.distance);
      const candidates = (managerOutlineState.candidates || []).filter(row => outlineSeconds(row.start) <= sec && sec <= outlineSeconds(row.end));
      const ranges = (managerOutlineState.ranges || []).filter(row => outlineSeconds(row.start) <= sec && sec <= outlineSeconds(row.end));
      const d2 = ranges.find(item => item.level === 'D2');
      const match = points[0] && points[0].distance <= 45 ? points[0] : candidates[0] ? {kind:'candidate', row:candidates[0]} : d2 ? {kind:'range', row:d2} : ranges[0] ? {kind:'range', row:ranges[0]} : null;
      if (!match) return;
      managerOutlineSelection = {kind:match.kind, id:match.row.id}; managerOutlineGroup = match.kind;
      renderManagerOutlineNavigator(); renderManagerOutlineInspector(); renderManagerOutlineTimeline();
    }

    function syncManagerOutlineSelectionEvidence(item, kind, spanIndex=0) {
      if (!item || !managerOutlineEditorEl.classList.contains('manager-outline-docked')) return;
      const activeSpan = kind === 'highlight' ? (item.source_spans || [])[spanIndex] : null;
      const anchor = activeSpan
        ? Number(activeSpan.start_sec)
        : outlineSeconds(kind === 'point' ? item.timestamp : item.start);
      if (!Number.isFinite(anchor)) return;
      selectCockpitTime(anchor, '', {displayEvent: {start_sec: anchor, title: item.title || '', lane: '방송 목차'}});
      if (selectedEvidenceSectionEl) setCockpitSectionOpen(selectedEvidenceSectionEl, true);
    }

    function renderManagerOutlineSelectionEvidence(item, kind, spanIndex=0) {
      if (!managerOutlineSelectionEvidenceEl || !managerOutlineSelectionEvidenceScopeEl) return;
      if (!item) {
        managerOutlineSelectionEvidenceScopeEl.textContent = '항목을 선택하면 같은 시각 주변의 근거만 보여줍니다.';
        managerOutlineSelectionEvidenceEl.textContent = '왼쪽 방송 구조에서 항목을 선택하세요.';
        return;
      }
      const spans = kind === 'highlight' ? (item.source_spans || []) : [];
      const start = spans.length ? Number(spans[0].start_sec) : outlineSeconds(kind === 'point' ? item.timestamp : item.start);
      const end = spans.length ? Number(spans[spans.length - 1].end_sec) : kind === 'point' ? start : outlineSeconds(item.end);
      const activeSpan = spans[spanIndex] || spans[0];
      const anchor = activeSpan ? (Number(activeSpan.start_sec) + Number(activeSpan.end_sec)) / 2 : Number.isFinite(end) ? (start + end) / 2 : start;
      const events = Array.isArray(signalTimelineState.events) ? signalTimelineState.events : [];
      const nearby = events.map(event => ({event, sec: Number(event.start_sec || 0), distance: Math.abs(Number(event.start_sec || 0) - anchor)}))
        .filter(row => Number.isFinite(row.distance) && (kind === 'point' ? row.distance <= 90 : kind === 'highlight' ? spans.some(span => row.sec >= Number(span.start_sec) && row.sec <= Number(span.end_sec)) : row.sec >= start && row.sec <= end))
        .sort((a, b) => a.distance - b.distance).slice(0, 6);
      const scopeTime = kind === 'point' ? item.timestamp
        : kind === 'highlight' ? spans.map(span => `[${fmtSeconds(Number(span.start_sec))}–${fmtSeconds(Number(span.end_sec))}]`).join(' · ')
        : `${item.start}–${item.end}`;
      managerOutlineSelectionEvidenceScopeEl.textContent = `${scopeTime} 주변 근거`;
      if (!nearby.length) {
        managerOutlineSelectionEvidenceEl.textContent = '이 시각에서 확인된 근거가 없습니다.';
        return;
      }
      const friendlyKind = event => {
        const raw = `${event.lane || ''} ${event.kind || ''}`.toLowerCase();
        if (raw.includes('subtitle') || raw.includes('stt')) return '자막';
        if (raw.includes('chat')) return '채팅 반응';
        if (raw.includes('comment')) return '공개 댓글';
        if (raw.includes('clip')) return '시청자 클립';
        if (raw.includes('audio') || raw.includes('wave')) return '오디오 반응';
        return '시간축 근거';
      };
      managerOutlineSelectionEvidenceEl.innerHTML = `<ul class="manager-outline-evidence-list">${nearby.map(({event}) => {
        const rawText = String(event.title || event.text || event.label || '');
        const internalText = /(^|[\s(])(confidence|score|role|metadata|match_proof|snippet|matched)(=|:)|^(seg_|event_)[A-Za-z0-9_-]+$/i.test(rawText);
        const text = !rawText || internalText ? '이 시각에 근거 신호가 확인되었습니다.' : rawText;
        return `<li><span class="manager-outline-evidence-kind">${escapeHtml(friendlyKind(event))}</span> · ${escapeHtml(fmtSeconds(Number(event.start_sec || 0)))}<br>${escapeHtml(text)}</li>`;
      }).join('')}</ul>`;
    }

    function renderManagerOutlineInspector() {
      const selection = managerOutlineSelection;
      const item = selection && managerOutlineCollection(selection.kind).find(row => row.id === selection.id);
      if (!item) {
        managerOutlineInspectorHeadingEl.textContent = '선택 항목 편집';
        managerOutlineBreadcrumbEl.textContent = '왼쪽에서 항목을 선택하세요.';
        managerOutlineInspectorEl.innerHTML = '<p>왼쪽에서 편집할 항목을 선택하세요.</p>';
        renderManagerOutlineSelectionEvidence(null, '');
        return;
      }
      const ranges = managerOutlineState.ranges || [];
      const rangeById = Object.fromEntries(ranges.map(row => [row.id, row]));
      const label = item.level || (selection.kind === 'point' ? 'Point' : 'Highlight');
      let relation = '';
      if (item.level === 'D2') relation = `소속 D1: ${(rangeById[item.parent_id] || {}).title || item.parent_id}`;

      managerOutlineInspectorHeadingEl.textContent = `${label} · ${item.title || '제목 없음'}`;
      managerOutlineBreadcrumbEl.textContent = relation || (selection.kind === 'point' ? '방송 흐름의 정확한 한 시각' : selection.kind === 'highlight' ? '승인된 Point와 source_spans를 사용한 편집 구간' : '상위 방송 구간');
      managerOutlineInspectorEl.innerHTML = outlineCard(item, selection.kind, label, relation)
        + (selection.kind === 'highlight' ? '' : `<button type="button" class="manager-outline-delete" data-outline-delete="${escapeHtml(item.id)}">${escapeHtml(label)} 삭제</button>`);
      renderManagerOutlineSelectionEvidence(item, selection.kind, Number(selection.spanIndex || 0));
      syncManagerOutlineSelectionEvidence(item, selection.kind, Number(selection.spanIndex || 0));
      managerOutlineRowsEl.querySelectorAll('[data-outline-select-id]').forEach(button => {
        const isSelected = button.dataset.outlineSelectKind === selection.kind
          && button.dataset.outlineSelectId === selection.id;
        button.setAttribute('aria-selected', isSelected ? 'true' : 'false');
      });
    }

    function managerOutlineNewId(prefix) {
      return `${prefix}-new-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
    }

    function captureManagerOutlineSnapshot() {
      return {
        state: JSON.parse(JSON.stringify(managerOutlineState)),
        selection: managerOutlineSelection ? {...managerOutlineSelection} : null,
        group: managerOutlineGroup,
        dirty: !!dirty,
      };
    }

    function restoreManagerOutlineSnapshot(snapshot) {
      managerOutlineState = JSON.parse(JSON.stringify(snapshot.state));
      managerOutlineSelection = snapshot.selection ? {...snapshot.selection} : null;
      managerOutlineGroup = snapshot.group;
      setDirty(!!snapshot.dirty);
    }

    function rememberManagerOutlineChange(label, snapshot=null) {
      const source = snapshot || captureManagerOutlineSnapshot();
      managerOutlineUndo = {
        state: JSON.parse(JSON.stringify(source.state)),
        selection: source.selection ? {...source.selection} : null,
        group: source.group,
        dirty: !!source.dirty,
        label,
      };
      managerOutlineUndoBtn.disabled = false;
    }

    function undoManagerOutlineChange() {
      if (!managerOutlineUndo) return;
      const undo = managerOutlineUndo;
      managerOutlineUndo = null;
      restoreManagerOutlineSnapshot(undo);
      managerOutlineUndoBtn.disabled = true;
      managerOutlineStatusEl.textContent = `${undo.label} 작업을 되돌렸어요.`;
      managerOutlineDragStatusEl.textContent = `되돌리기 완료: ${undo.label}`;
      renderManagerOutlineNavigator();
      renderManagerOutlineInspector();
      renderManagerOutlineTimeline();
      scheduleManagerOutlineDraftSync('outline');
    }

    function addManagerOutlineItem(kind) {
      syncManagerOutlineInspector();
      const ranges = managerOutlineState.ranges || [];
      const points = managerOutlineState.points || [];
      const highlights = managerOutlineState.highlights || [];
      let item;
      let collectionKind;
      if (kind === 'D1') {
        const duration = managerOutlineDuration();
        const d1Rows = ranges.filter(row => row.level === 'D1').sort((left, right) => outlineSeconds(left.start) - outlineSeconds(right.start));
        let cursor = 0;
        let gap = null;
        for (const row of d1Rows) {
          const rowStart = Math.min(duration, outlineSeconds(row.start));
          if (rowStart > cursor) { gap = [cursor, rowStart]; break; }
          cursor = Math.max(cursor, Math.min(duration, outlineSeconds(row.end)));
        }
        if (!gap && cursor < duration) gap = [cursor, duration];
        if (!gap) {
          managerOutlineStatusEl.textContent = '실제 방송 길이 안에 새 D1을 넣을 빈 시간이 없어요.';
          return;
        }
        const [start, limit] = gap;
        item = {id: managerOutlineNewId('range'), level: 'D1', parent_id: '', start: outlineHms(start), end: outlineHms(Math.min(start + 60, limit)), title: '새 D1', content: ''};
        rememberManagerOutlineChange('D1 추가');
        ranges.push(item);
        collectionKind = 'range';
      } else if (kind === 'D2') {
        const parent = managerOutlineSelection && managerOutlineSelection.kind === 'range'
          ? ranges.find(row => row.id === managerOutlineSelection.id && row.level === 'D1') : null;
        if (!parent) {
          managerOutlineStatusEl.textContent = 'D2를 추가하려면 먼저 소속 D1을 선택해 주세요.';
          return;
        }
        const siblings = ranges
          .filter(row => row.level === 'D2' && row.parent_id === parent.id)
          .sort((left, right) => outlineSeconds(left.start) - outlineSeconds(right.start));
        let start = outlineSeconds(parent.start);
        let limit = outlineSeconds(parent.end);
        let nextSibling = null;
        for (const sibling of siblings) {
          if (outlineSeconds(sibling.start) > start) {
            limit = outlineSeconds(sibling.start);
            nextSibling = sibling;
            break;
          }
          start = Math.max(start, outlineSeconds(sibling.end));
        }
        const end = Math.min(start + 60, limit);
        if (end <= start) {
          managerOutlineStatusEl.textContent = '선택한 D1 안에 새 D2를 넣을 빈 시간이 없어요.';
          return;
        }
        item = {id: managerOutlineNewId('range'), level: 'D2', parent_id: parent.id, start: outlineHms(start), end: outlineHms(end), title: '새 D2', content: ''};
        rememberManagerOutlineChange('D2 추가');
        const insertion = nextSibling ? ranges.indexOf(nextSibling) : Math.max(ranges.indexOf(parent) + 1, ...siblings.map(row => ranges.indexOf(row) + 1));
        ranges.splice(insertion, 0, item);
        collectionKind = 'range';
      } else if (kind === 'point') {
        const selectedRange = managerOutlineSelection && managerOutlineSelection.kind === 'range'
          ? ranges.find(row => row.id === managerOutlineSelection.id) : null;
        const storyRef = managerEditorialPacket ? managerOutlineNewId('story') : '';
        item = {id: managerOutlineNewId('point'), timestamp: selectedRange ? selectedRange.start : '00:00:00', title: '새 Point', content: '', story_why_notable: '', ...(storyRef ? {story_ref: storyRef} : {})};
        rememberManagerOutlineChange('Point 추가');
        points.push(item);
        collectionKind = 'point';
      } else {
        managerOutlineStatusEl.textContent = 'Highlight는 승인된 편집 결과에서만 가져옵니다.';
        return;
      }
      managerOutlineSelection = {kind: collectionKind, id: item.id};
      managerOutlineGroup = collectionKind;
      setDirty(true);
      closeManagerOutlineAddMenu(false);
      managerOutlineStatusEl.textContent = `${item.level || 'Point'} 항목을 추가했어요. 시간을 확인한 뒤 저장하세요.`;
      renderManagerOutlineNavigator();
      renderManagerOutlineInspector();
      renderManagerOutlineTimeline();
      const firstInput = managerOutlineInspectorEl.querySelector('[data-outline-field]');
      if (firstInput) firstInput.focus({preventScroll: true});
    }

    function deleteSelectedManagerOutlineItem() {
      syncManagerOutlineInspector();
      const selection = managerOutlineSelection;
      if (!selection) return;
      const item = managerOutlineCollection(selection.kind).find(row => row.id === selection.id);
      if (!item) return;
      if (selection.kind === 'highlight') return;
      if (selection.kind === 'range' && item.level === 'D1' && (managerOutlineState.ranges || []).some(row => row.level === 'D2' && row.parent_id === item.id)) {
        managerOutlineStatusEl.textContent = 'D2가 있는 D1은 삭제할 수 없어요. D2를 먼저 검토해 주세요.';
        return;
      }
      const deleteLabel = item.level || 'Point';
      if (selection.kind === 'point' && (managerOutlineState.highlights || []).some(highlight => (highlight.point_refs || []).map(String).includes(String(item.id)))) {
        managerOutlineStatusEl.textContent = '이 Point는 Highlight가 사용하고 있어 삭제할 수 없어요. Highlight 연결을 먼저 검토해 주세요.';
        return;
      }
      rememberManagerOutlineChange(`${deleteLabel} 삭제`);
      const key = selection.kind === 'range' ? 'ranges' : 'points';
      managerOutlineState[key] = managerOutlineState[key].filter(row => row.id !== item.id);
      managerOutlineSelection = null;
      setDirty(true);
      managerOutlineStatusEl.textContent = `${deleteLabel}를 로컬 수정본에서 삭제했어요. [되돌리기]로 한 단계 복원할 수 있어요.`;
      renderManagerOutlineNavigator();
      renderManagerOutlineInspector();
      renderManagerOutlineTimeline();
      scheduleManagerOutlineDraftSync('outline');
    }

    function outlineVersionSummary(outline) {
      const rows = (outline && outline.ranges || []).map(item => `${item.level} ${item.start}-${item.end} ${item.title}`).join('\n');
      const points = (outline && outline.points || []).map(item => `Point ${item.timestamp} ${item.title}`).join('\n');
      const highlights = (outline && outline.highlights || []).map(item => `Highlight ${item.title}`).join('\n');
      return [rows, points, highlights].filter(Boolean).join('\n');
    }

    function managerEditorialHighlights(packet, points) {
      if (!packet) return [];
      const pointById = Object.fromEntries((points || []).map(point => [String(point.id || ''), point]));
      const storyByRef = Object.fromEntries((packet.stories || []).map(story => [String(story.story_ref || ''), story]));
      return (packet.editorial_highlights || []).map(highlight => {
        const pointRows = (highlight.point_refs || []).map(ref => pointById[String(ref)]).filter(Boolean);
        const storyRefs = pointRows.map(point => String(point.story_ref || '')).filter(Boolean);
        const whyRows = storyRefs.map(ref => String((storyByRef[ref] || {}).why_notable || '')).filter(Boolean);
        return {
          ...JSON.parse(JSON.stringify(highlight)),
          id: String(highlight.highlight_id || ''),
          why_notable: Object.prototype.hasOwnProperty.call(highlight, 'selection_reason')
            ? String(highlight.selection_reason || '') : [...new Set(whyRows)].join(' / '),
          story_refs: storyRefs,
          point_labels: pointRows.map(point => `${point.timestamp || ''} ${point.title || 'Point'}`),
        };
      });
    }

    function hydrateManagerPointStories() {
      if (!managerOutlineState || !managerEditorialPacket) return;
      const storyByRef = Object.fromEntries((managerEditorialPacket.stories || []).map(story => [String(story.story_ref || ''), story]));
      (managerOutlineState.points || []).forEach(point => {
        const story = storyByRef[String(point.story_ref || '')] || {};
        point.story_why_notable = String(story.why_notable || '');
      });
    }

    function collectEditorialReviewPacket() {
      if (!managerEditorialPacket) return null;
      const packet = JSON.parse(JSON.stringify(managerEditorialPacket));
      if (collaborationPeriodsEdited) {
        const original = packet.collaboration_periods || [];
        const clock = text => {
          if (!/^\d{2,}:\d{2}:\d{2}(?:\.\d+)?$/.test(text)) throw new Error('합방 시각은 HH:MM:SS 형식으로 입력하세요.');
          const [h, m, s] = text.split(':').map(Number);
          if (m >= 60 || s >= 60) throw new Error('합방 시각의 분과 초는 60보다 작아야 합니다.');
          return h * 3600 + m * 60 + s;
        };
        packet.collaboration_periods = collaborationPeriodsInput.value.split('\n').map(line => line.trim()).filter(Boolean).map((line, i) => {
          if (original[i] && collaborationPeriodLine(original[i]) === line) return original[i];
          const columns = line.split('|').map(x => x.trim());
          if (columns.length < 3 || columns.length > 4) throw new Error('합방 멤버는 시작 | 끝 | 멤버 | 멤버=VOD번호 형식으로 입력하세요. VOD 칸은 생략할 수 있습니다.');
          const members = columns[2] ? columns[2].split(',').map(x => x.trim()) : [];
          const links = columns[3] ? columns[3].split(',').map(text => {
            const match = text.trim().match(/^(.+)=([1-9][0-9]{0,19})$/);
            if (!match || !members.includes(match[1].trim())) throw new Error('VOD 연결은 멤버 목록의 이름=숫자번호로 입력하세요.');
            const member_name = match[1].trim(), vod_no = match[2];
            const previous = ((original[i] || {}).member_vods || []).find(link => link.member_name === member_name && link.vod_no === vod_no);
            return previous ? {...previous} : {member_name, vod_no};
          }) : [];
          let id = original[i] ? original[i].id : `manager-collab-${i + 1}`;
          while (!original[i] && original.some(row => row.id === id)) id += '-new';
          return {id, start_sec: clock(columns[0]), end_sec: clock(columns[1]), members,
            ...(links.length ? {member_vods: links} : {}), source_kind: 'manager_declared', support_status: 'provisional'};
        });
      }
      const originalPointById = Object.fromEntries(((packet.broadcast_map || {}).points || []).map(point => [String(point.id || ''), point]));
      const originalStoryByRef = Object.fromEntries((packet.stories || []).map(story => [String(story.story_ref || ''), story]));
      const points = (managerOutlineState.points || []).map(point => {
        const pointId = String(point.id || '');
        const original = originalPointById[pointId] || {};
        const storyRef = String(point.story_ref || original.story_ref || managerOutlineNewId('story'));
        point.story_ref = storyRef;
        const projected = {...original, ...JSON.parse(JSON.stringify(point)), story_ref: storyRef};
        delete projected.story_why_notable;
        return projected;
      });
      packet.broadcast_map = {...(packet.broadcast_map || {}), points};
      packet.stories = points.map(point => {
        const uiPoint = (managerOutlineState.points || []).find(row => String(row.id || '') === String(point.id || '')) || {};
        const original = originalStoryByRef[String(point.story_ref || '')] || {};
        return {
          ...original,
          story_ref: String(point.story_ref || ''),
          title: String(original.title || point.title || '새 Point 근거'),
          why_notable: String(uiPoint.story_why_notable || ''),
          roles: original.roles || {},
          evidence_refs: original.evidence_refs || [],
        };
      });
      const highlightById = Object.fromEntries((managerOutlineState.highlights || []).map(item => [String(item.id || ''), item]));
      const originalHighlightById = Object.fromEntries(managerEditorialHighlights(
        managerEditorialPacket, (managerEditorialPacket.broadcast_map || {}).points || []
      ).map(item => [String(item.id || ''), item]));
      packet.editorial_highlights = (packet.editorial_highlights || []).map(highlight => {
        const edited = highlightById[String(highlight.highlight_id || '')];
        const reasonChanged = edited && String(edited.why_notable || '')
          !== String((originalHighlightById[String(highlight.highlight_id || '')] || {}).why_notable || '');
        return edited ? {
          ...highlight, title: edited.title, story_summary: edited.story_summary,
          ...(reasonChanged ? {selection_reason: String(edited.why_notable || '')} : {}),
          source_spans: JSON.parse(JSON.stringify(edited.source_spans || [])),
        } : highlight;
      });
      return packet;
    }

    function managerEditorialCompletionText(packet, progress) {
      const questions = [];
      for (const question of packet && Array.isArray(packet.unresolved_questions) ? packet.unresolved_questions : []) {
        const text = typeof question === 'string' ? question : question && question.question;
        if (typeof text === 'string' && text.trim()) questions.push(text);
      }
      const questionNotice = questions.length ? ` 확인할 내용: ${questions.join(' / ')}` : '';
      if (progress && progress.pipeline_complete === true
          && progress.completion_disposition === 'context_unverified_preserved') {
        return '요약 처리가 완료됐습니다. 확인하지 못한 내용은 미확인으로 남겼습니다.' + questionNotice;
      }
      if (progress && progress.schema_version === 'manager_outline.review_progress.v1'
          && progress.review_available === true && progress.pipeline_complete === false) {
        return '기본 요약 준비 · 장면 분석 미완. 현재 목차와 Point는 검토·수정할 수 있어요. '
          + '남은 장면의 원문 확인과 편집 구간 구성은 아직 끝나지 않았습니다. '
          + '미검토 후보를 가치 없다고 판단한 결과는 아닙니다.' + questionNotice;
      }
      if (!packet || typeof packet !== 'object') return '';
      const exploration = packet.source_exploration || {};
      const refsCount = rows => Array.isArray(rows)
        ? new Set(rows.filter(ref => typeof ref === 'string' && ref.trim())).size : null;
      const pending = refsCount(exploration.incomplete_hypothesis_refs);
      const unreviewed = refsCount(exploration.not_reached_hypothesis_refs);
      const partial = String(packet.completion_state || '').startsWith('partial_')
        || exploration.all_producer_hypotheses_reached === false
        || pending > 0 || unreviewed > 0 || questions.length > 0;
      if (!partial) return exploration.coverage_complete === false
        ? '이번 후보 분석은 끝났어요. 방송 전체 원문을 전수 판정한 결과는 아닙니다.' : '';
      const counts = [];
      if (Array.isArray(packet.highlights)) counts.push(`편집 Highlight ${packet.highlights.length}개`);
      if (pending !== null) counts.push(`확인 중인 후보 ${pending}개`);
      if (unreviewed !== null) counts.push(`아직 검토하지 못한 후보 ${unreviewed}개`);
      return `분석이 아직 끝나지 않았어요.${counts.length ? ' ' + counts.join(' · ') + '.' : ''} `
        + '남은 후보를 가치 없다고 판단한 결과는 아닙니다. 현재 목차와 Point는 검토·수정할 수 있어요.' + questionNotice;
    }

    function renderManagerOutline(data) {
      const completionNotice = document.getElementById('editorialCompletionNotice');
      if (completionNotice) {
        const completionText = managerEditorialCompletionText(data.qg1_review_packet, data.manager_outline_review_progress);
        completionNotice.textContent = completionText;
        completionNotice.hidden = !completionText;
      }
      const isOutline = data.kind === 'manager_outline';
      const hasStructuredOutline = isOutline || !!data.structured_outline;
      reportAppEl.classList.toggle('report-reading-layout', hasStructuredOutline);
      reportSourceToggle.hidden = !hasStructuredOutline;
      managerOutlineSaveBtn.hidden = !hasStructuredOutline;
      managerOutlineContext = hasStructuredOutline
        ? {
            videoNo: String(data.video_no || ''),
            base: String(data.base || base || ''),
            normalReport: !isOutline,
            editRevision: data.manager_edit_revision || '',
            revisions: Array.isArray(data.manager_outline_revisions)
              ? data.manager_outline_revisions
              : [],
          }
        : null;
      if (isOutline) applyReportPageRole('manager');
      if (reportRoleSwitchEl) reportRoleSwitchEl.hidden = isOutline;
      if (reportAppEl) reportAppEl.classList.toggle('manager-outline-mode', isOutline);
      reportReadButton.hidden = !hasStructuredOutline;
      reportShareToggle.hidden = !hasStructuredOutline || isOutline;
      if (hasStructuredOutline) setReportSurface(reportAppEl.dataset.reportSurface || new URLSearchParams(location.search).get('tab'), false);
      managerOutlineEditorEl.hidden = !hasStructuredOutline;
      if (chzzkCommentGroupEl) {
        if (hasStructuredOutline) managerOutlineEditorEl.append(chzzkCommentGroupEl);
        else mdEditor.insertAdjacentElement('afterend', chzzkCommentGroupEl);
        chzzkCommentGroupEl.classList.toggle('manager-outline-comment-export', hasStructuredOutline);
      }
      mdEditor.hidden = false;
      mdEditor.previousElementSibling.hidden = false;
      document.querySelector('.save-row').hidden = false;
      publishRowEl.hidden = isOutline;
      if (!hasStructuredOutline) return;

      if (hasStructuredOutline) {
        const adminPanelEl = document.getElementById('adminPanel');
        const legacyCockpitFlowEl = adminPanelEl && adminPanelEl.querySelector(':scope > .cockpit-flow');
        const adminPanelHeadingEl = adminPanelEl && adminPanelEl.querySelector(':scope > h2');
        const adminPanelCaptionEl = adminPanelEl && adminPanelEl.querySelector(':scope > .admin-caption');
        if (adminPanelEl && managerOutlineEditorEl.parentElement !== adminPanelEl) {
          adminPanelEl.append(managerOutlineEditorEl);
        }
        managerOutlineEditorEl.classList.add('manager-outline-docked');
        if (legacyCockpitFlowEl) {
          legacyCockpitFlowEl.hidden = false;
          Array.from(legacyCockpitFlowEl.children).forEach((section) => {
            section.hidden = section.id !== 'signalTimelineSection';
          });
        }
        if (adminPanelSummaryEl) adminPanelSummaryEl.hidden = true;
        if (adminPanelHeadingEl) adminPanelHeadingEl.textContent = '방송 목차·Highlight 편집';
        if (adminPanelCaptionEl) adminPanelCaptionEl.textContent = '장면 찾기에서 관리자 전용 반응 신호를 확인하고, 아래에서 방송 구조와 Highlight 구간을 고치면 같은 수정본이 반영됩니다.';
        if (managerOutlineSaveBtn) managerOutlineSaveBtn.hidden = false;
      }

      managerEditorialPacket = data.qg1_review_packet ? JSON.parse(JSON.stringify(data.qg1_review_packet)) : null;
      if (collaborationPeriodsEditor && collaborationPeriodsInput) {
        collaborationPeriodsEditor.hidden = !managerEditorialPacket;
        collaborationPeriodsInput.value = ((managerEditorialPacket || {}).collaboration_periods || []).map(collaborationPeriodLine).join('\n');
        collaborationPeriodsEdited = false;
      }
      managerOutlineState = JSON.parse(JSON.stringify(data.structured_outline || {ranges: [], points: [], candidates: []}));
      hydrateManagerPointStories();
      if (hasStructuredOutline && typeof data.manager_markdown === 'string') {
        mdEditor.value = data.manager_markdown;
        savedMd = data.manager_markdown;
        if (mdEditor.previousElementSibling) mdEditor.previousElementSibling.textContent = '방송 구조 Markdown';
      }
      // Keep existing review windows paired with Markdown; they are not Highlights.
      managerOutlineState.highlights = managerEditorialHighlights(managerEditorialPacket, managerOutlineState.points || []);
      managerOutlineUndo = null;
      managerOutlinePendingInput = null;
      managerOutlineMarkdownPending = false;
      closeManagerOutlineAddMenu(false);
      managerOutlineUndoBtn.disabled = true;
      const ranges = managerOutlineState.ranges || [];
      const points = managerOutlineState.points || [];
      const highlights = managerOutlineState.highlights || [];
      const allSelections = [
        ...ranges.map(item => ({kind: 'range', id: item.id})),
        ...points.map(item => ({kind: 'point', id: item.id})),
        ...highlights.map(item => ({kind: 'highlight', id: item.id})),
      ];
      if (!managerOutlineSelection || !allSelections.some(item => item.kind === managerOutlineSelection.kind && item.id === managerOutlineSelection.id)) {
        managerOutlineSelection = allSelections[0] || null;
      }
      renderManagerOutlineNavigator();
      renderManagerOutlineInspector();
      renderManagerOutlineTimeline();
      const readOnly = managerOutlineState.read_only || {};
      managerOutlineReadOnlyEl.querySelectorAll('[data-outline-readonly]').forEach(block => {
        block.querySelector('pre').textContent = String(readOnly[block.dataset.outlineReadonly] || '없음');
      });
      const revisions = data.manager_outline_revisions || [];
      managerOutlineHistorySummaryEl.textContent = `로컬 수정 이력 ${revisions.length}개`;
      managerOutlineHistoryEl.innerHTML = revisions.map(version => {
        return `<section class="outline-history-entry"><strong>${escapeHtml(window.ReportNavigation.revisionTitle(version))}</strong> <button type="button" data-outline-restore="${escapeHtml(version.revision_id)}">이 저장본 복원…</button><p>${escapeHtml(window.ReportNavigation.revisionSummary(version))}</p><details><summary>기록 ID·저장 내용 보기</summary><code>${escapeHtml(version.revision_id)}</code><pre>${escapeHtml(outlineVersionSummary(version.outline))}</pre></details></section>`;
      }).join('') || '<p>아직 저장된 수정본이 없습니다.</p>';
      managerOutlineStatusEl.textContent = '목차를 불러왔어요.';
      if (typeof data.chzzk_comment_text === 'string') {
        renderChzzkCommentDraft({text: data.chzzk_comment_text});
      }
    }

    function collectManagerOutline() {
      syncManagerOutlineInspector();
      const outline = JSON.parse(JSON.stringify(managerOutlineState || {ranges: [], points: [], candidates: []}));
      delete outline.highlights;
      (outline.points || []).forEach(point => { delete point.story_why_notable; });
      return outline;
    }

    let managerOutlineBusy = false;

    function managerOutlineActionButtons() {
      return [
        managerOutlineSaveBtn,
        ...managerOutlineHistoryEl.querySelectorAll('[data-outline-restore]'),
      ].filter(Boolean);
    }

    function setManagerOutlineActionBusy(active) {
      managerOutlineBusy = active;
      managerOutlineEditorEl.setAttribute('aria-busy', active ? 'true' : 'false');
      managerOutlineActionButtons().forEach(button => { button.disabled = active; });
      managerOutlineAddBtn.disabled = active;
      mdEditor.disabled = active;
      managerOutlineUndoBtn.disabled = active || !managerOutlineUndo;
      if (active) closeManagerOutlineAddMenu(false);
      managerOutlineInspectorEl.querySelectorAll('[data-outline-field]').forEach(control => { control.disabled = active; });
    }

    function managerOutlineFieldErrorElement(control) {
      return control && control.parentElement
        ? control.parentElement.querySelector('[data-outline-error]')
        : null;
    }

    function setManagerOutlineFieldError(control, message, kind) {
      if (!control) return;
      control.setAttribute('aria-invalid', 'true');
      control.dataset.outlineErrorKind = kind;
      const error = managerOutlineFieldErrorElement(control);
      if (error) {
        error.hidden = false;
        error.textContent = message;
      }
    }

    function clearManagerOutlineFieldError(control, kind=null) {
      if (!control || (kind && control.dataset.outlineErrorKind !== kind)) return;
      control.setAttribute('aria-invalid', 'false');
      delete control.dataset.outlineErrorKind;
      const error = managerOutlineFieldErrorElement(control);
      if (error) {
        error.hidden = true;
        error.textContent = error.dataset.outlineErrorDefault || '시:분:초 형식으로 입력하세요.';
      }
    }

    function validateManagerOutlineField(control) {
      if (!control || !['start', 'end', 'timestamp'].includes(control.dataset.outlineField)) return true;
      const match = control.value.match(/^([0-9]{2}):([0-9]{2}):([0-9]{2})$/);
      const valid = !!match && Number(match[2]) < 60 && Number(match[3]) < 60;
      if (!valid) {
        setManagerOutlineFieldError(control, '시:분:초 형식으로 입력하세요.', 'format');
      } else {
        clearManagerOutlineFieldError(control);
      }
      return valid;
    }


    function validateManagerOutlineFields() {
      const controls = [...managerOutlineInspectorEl.querySelectorAll('[data-outline-field]')];
      const valid = controls.every(validateManagerOutlineField);
      if (!valid) controls.find(control => control.getAttribute('aria-invalid') === 'true').focus();
      return valid;
    }

    function managerOutlineErrorMessage(error, action) {
      const raw = String((error && error.message) || '');
      if (raw.includes('목차의 구성은 바꿀 수 없어요')) return raw;
      if (
        raw.includes('invalid HH:MM:SS timestamp')
        || raw.includes('outline rows must be D1/D2 start-end ranges')
        || raw.includes('Point must be `없음` or exact timestamped event rows')
        || raw.includes('highlight candidate must be a start-end range')
      ) return '시간은 시:분:초 형식으로 입력해 주세요. 예: 00:20:39';
      if (raw.includes('end must be after start')) return '끝 시간은 시작 시간보다 뒤여야 해요.';
      if (raw.includes('exceeds VOD duration')) return '방송 길이를 넘는 시간이 있어요. 시간을 줄여 주세요.';
      if (raw.includes('D1 ranges must be ordered and non-overlapping')) return 'D1 구간은 시간순이어야 하고 서로 겹치면 안 돼요.';
      if (raw.includes('D2 range must be contained by its D1')) return 'D2의 시작과 끝을 표시된 D1 안으로 맞춰 주세요.';
      if (raw.includes('sibling D2 ranges must be ordered and non-overlapping')) return '같은 D1의 D2 구간은 시간순이어야 하고 서로 겹치면 안 돼요.';
      if (raw.includes('highlight candidate end')) return '검토 범위의 끝은 시작보다 뒤여야 하고 방송 길이를 넘을 수 없어요.';
      if (raw.includes('revision not found')) return '선택한 이전 버전을 찾지 못했어요. 페이지를 다시 연 뒤 선택해 주세요.';
      if (raw.includes('다른 수정본이 먼저 저장되었습니다.')) return raw;
      if (raw.includes('Failed to fetch') || raw.includes('NetworkError')) return 'Report Admin 서버에 연결하지 못했어요. 서버 창을 확인한 뒤 다시 시도해 주세요.';
      return action === 'restore'
        ? '이전 버전을 복원하지 못했어요. 페이지를 다시 연 뒤 다시 시도해 주세요.'
        : '목차를 저장하지 못했어요. 입력한 내용은 그대로예요. 시간과 제목을 확인한 뒤 다시 시도해 주세요.';
    }

    async function saveManagerOutline() {
      if (managerOutlineBusy) return;
      assertReportSaveReady();
      if (!validateManagerOutlineFields()) {
        managerOutlineStatusEl.textContent = '시간을 시:분:초 형식으로 바로잡으면 저장할 수 있어요.';
        return;
      }
      managerOutlineStatusEl.textContent = '목차 수정본을 검증하고 저장하는 중입니다.';
      window.clearTimeout(managerOutlineSyncTimer);
      window.clearTimeout(managerOutlinePreviewTimer);
      ++managerOutlineSyncSequence;
      let committed = false;
      let responseKeys = [];
      const context = managerOutlineContext || {};
      const outline = collectManagerOutline(), packet = collectEditorialReviewPacket();
      const attempt = {kind: 'save', structured: true, base: context.base || base,
        videoNo: String(context.videoNo || base.split(':', 2)[1]), editRevision: context.editRevision || '',
        outline, packet, editKey: reportOutlineEditKey(outline, packet), markdown: mdEditor.value,
        markdownPending: managerOutlineMarkdownPending};
      setManagerOutlineActionBusy(true);
      reportSavePending = true;
      try {
        // Save both current views together. The server merges a pending text
        // edit by row ID; cancelling debounce must never make stale rows win.
        const data = await requestReportSaveWithConfirmation('/api/manager-outline/save', {
          video_no: context.videoNo || base.split(':', 2)[1],
          base: context.base || base,
          manager_markdown: mdEditor.value,
          outline,
          editorial_review_packet: packet,
          expected_edit_revision: context.editRevision || '',
        });
        responseKeys = Object.keys(data || {}).slice(0, 20);
        // Only the exact report's new durable revision acknowledges this save.
        // Rendering a response or preview is a separate, fallible step.
        committed = validReportSaveAck(data, attempt);
        if (!committed) throw new Error('save_revision_ack_missing');
        reportLastConfirmed = data;
        renderManagerOutline(data);
        if (typeof data.manager_markdown === 'string') {
          mdEditor.value = data.manager_markdown;
          savedMd = data.manager_markdown;
          await loadPreviewFrameHtml(`/api/saved-html?base=${encodeFrameParam(context.base || base)}&t=${Date.now()}`);
        }
        setDirty(false);
        managerOutlineStatusEl.textContent = '변경사항을 저장했어요. 아직 공개되지 않았어요.';
        setStatus('로컬 수정본을 저장했습니다. 공개 사이트는 바뀌지 않았습니다.', 'ok');
        return true;
      } catch (error) {
        const errorName = String(error && error.name || 'Error');
        const errorText = String(error && error.message || '');
        console.warn('Report Admin outline save response', JSON.stringify({
          phase: committed ? 'refresh_after_commit' : 'save_response',
          name: errorName,
          // Native JS diagnostics and typed codes only; never dump server
          // bodies, report text, headers, or arbitrary exception payloads.
          message: ['TypeError', 'ReferenceError'].includes(errorName)
            ? errorText.slice(0, 1000)
            : /^[a-z][a-z0-9_]{0,95}$/.test(errorText) ? errorText : 'details_not_recorded',
          response_keys: responseKeys,
        }));
        if (committed) {
          reportSaveOutcome = {...attempt, checked: reportLastConfirmed, confirmed: true};
          managerOutlineStatusEl.textContent = '변경사항은 저장됐어요. 화면 갱신을 마치지 못했습니다. 저장·복원을 반복하지 말고 저장본을 다시 확인하세요.';
          setStatus(managerOutlineStatusEl.textContent, 'warn');
          // A Save-before-Publish caller must also stop at this review boundary.
          throw new Error(managerOutlineStatusEl.textContent);
        }
        if (error.saveCanceled) {
          managerOutlineStatusEl.textContent = error.message;
          setStatus(error.message, 'warn');
        } else holdReportSaveOutcome(attempt, error);
        throw error;
      } finally {
        reportSavePending = false;
        setManagerOutlineActionBusy(false);
        syncReportSaveRecovery();
        managerOutlineSaveBtn.focus({preventScroll: true});
      }
    }

    async function restoreManagerOutline(revisionId, reportMarkdownOverride = null) {
      if (managerOutlineBusy) return;
      assertReportSaveReady();
      const pairing = typeof reportMarkdownOverride === 'string'
        ? '\n선택한 본문과 목차 제목·시각이 맞는 구조 후보입니다. Highlight·근거까지 당시의 정확한 짝이라고 확인한 결과는 아닙니다.' : '';
      if (!await confirmReportSaveAction('이전 저장본 복원',
        `복원할 저장본: ${revisionId}\n현재 ${dirty ? '미저장 초안과 ' : ''}편집 되돌리기 기록을 대체합니다. 새 로컬 수정본을 만들며 공개 사이트는 바뀌지 않습니다.${pairing}`, '새 로컬 수정본으로 복원')) return false;
      managerOutlineStatusEl.textContent = '선택한 이전 버전을 새 수정본으로 복원하는 중입니다.';
      setManagerOutlineActionBusy(true);
      reportSavePending = true;
      const context = managerOutlineContext || {};
      const attempt = {kind: 'restore', structured: true, base: context.base || base,
        videoNo: String(context.videoNo || base.split(':', 2)[1]), editRevision: context.editRevision || '',
        revisionId, packet: managerEditorialPacket};
      let committed = false;
      try {
        const data = await requestReportSaveWithConfirmation('/api/manager-outline/restore', {
          video_no: context.videoNo || base.split(':', 2)[1], base: context.base || base, revision_id: revisionId,
          report_markdown: typeof reportMarkdownOverride === 'string' ? reportMarkdownOverride : undefined,
          expected_edit_revision: context.editRevision || '',
        });
        committed = validReportSaveAck(data, attempt);
        if (!committed) throw Error('restore_revision_ack_missing');
        reportLastConfirmed = data;
        renderManagerOutline(data);
        if (typeof data.manager_markdown === 'string') {
          mdEditor.value = data.manager_markdown;
          savedMd = data.manager_markdown;
          await loadPreviewFrameHtml(`/api/saved-html?base=${encodeFrameParam(context.base || base)}&t=${Date.now()}`);
        }
        setDirty(false);
        managerOutlineStatusEl.textContent = '이전 버전을 새 로컬 수정본으로 복원했습니다. 공개 사이트는 바뀌지 않았습니다.';
        setStatus(managerOutlineStatusEl.textContent, 'ok');
        return true;
      } catch (error) {
        if (committed) {
          reportSaveOutcome = {...attempt, checked: reportLastConfirmed, confirmed: true};
          managerOutlineStatusEl.textContent = '복원은 저장됐습니다. 화면 갱신을 마치지 못했으며 같은 복원은 다시 보내지 않습니다.';
          setStatus(managerOutlineStatusEl.textContent, 'warn');
        } else if (error.saveCanceled) {
          managerOutlineStatusEl.textContent = error.message;
          setStatus(error.message, 'warn');
        } else holdReportSaveOutcome(attempt, error);
        throw error;
      } finally {
        reportSavePending = false;
        setManagerOutlineActionBusy(false);
        syncReportSaveRecovery();
        const returnButton = managerOutlineHistoryEl.querySelector(`[data-outline-restore="${CSS.escape(revisionId)}"]`);
        (returnButton || managerOutlineSaveBtn).focus({preventScroll: true});
      }
    }



    function openManagerOutlineAddMenu() {
      updateManagerOutlineAddMenu();
      managerOutlineAddMenu.hidden = false;
      managerOutlineAddBtn.setAttribute('aria-expanded', 'true');
      const first = managerOutlineAddMenu.querySelector('[role="menuitem"]:not(:disabled)');
      if (first) first.focus({preventScroll: true});
    }

    function closeManagerOutlineAddMenu(restoreFocus=true) {
      managerOutlineAddMenu.hidden = true;
      managerOutlineAddBtn.setAttribute('aria-expanded', 'false');
      if (restoreFocus) managerOutlineAddBtn.focus({preventScroll: true});
    }


    function commitHighlightBoundary(control) {
      const id = String(control && control.dataset.outlineId || '');
      const edge = String(control && control.dataset.highlightBoundary || '');
      const seconds = parseTimeInput(control && control.value);
      if (!id || !['start', 'end'].includes(edge) || !Number.isFinite(seconds)) {
        if (control) control.setAttribute('aria-invalid', 'true');
        managerOutlineStatusEl.textContent = '초, 분:초 또는 시:분:초로 입력해 주세요. 예: 90, 01:30, 00:01:30';
        return false;
      }
      const snapshot = captureManagerOutlineSnapshot();
      const changed = resizeHighlightRange(id, edge, seconds, snapshot, false);
      if (changed) {
        const replacement = managerOutlineInspectorEl.querySelector(`[data-highlight-boundary="${edge}"][data-outline-id="${CSS.escape(id)}"]`);
        if (replacement) replacement.setAttribute('aria-invalid', 'false');
      }
      return changed;
    }

    function commitHighlightSpanBoundary(control) {
      const id = String(control && control.dataset.outlineId || '');
      const edge = String(control && control.dataset.highlightSpanBoundary || '');
      const spanIndex = Number(control && control.dataset.highlightSpanIndex);
      const seconds = parseTimeInput(control && control.value);
      if (!id || !Number.isInteger(spanIndex) || !['start', 'end'].includes(edge) || !Number.isFinite(seconds)) {
        if (control) control.setAttribute('aria-invalid', 'true');
        managerOutlineStatusEl.textContent = '장면 시간은 초, 분:초 또는 시:분:초로 입력해 주세요.';
        return false;
      }
      const highlight = (managerOutlineState.highlights || []).find(row => row.id === id);
      const span = highlight && (highlight.source_spans || [])[spanIndex];
      if (span && String(control.value).trim() === outlineHms(span[edge === 'start' ? 'start_sec' : 'end_sec'])) return true;
      const snapshot = captureManagerOutlineSnapshot();
      return resizeHighlightSpan(id, spanIndex, edge, seconds, snapshot, false);
    }


    function commitManagerOutlineField(control) {
      const kind = control.dataset.outlineKind;
      const id = control.dataset.outlineId;
      const field = control.dataset.outlineField;
      const pending = managerOutlinePendingInput;
      const snapshot = pending && pending.kind === kind && pending.id === id && pending.field === field
        ? pending.snapshot : captureManagerOutlineSnapshot();
      const original = kind === 'range'
        ? (snapshot.state.ranges || []).find(row => row.id === id)
        : kind === 'point'
          ? (snapshot.state.points || []).find(row => row.id === id)
          : (snapshot.state.highlights || []).find(row => row.id === id);
      const item = managerOutlineCollection(kind).find(row => row.id === id);
      if (!item || !original) return false;
      if (String(original[field] || '') === control.value) {
        managerOutlinePendingInput = null;
        return true;
      }
      if (['start', 'end', 'timestamp'].includes(field)) {
        if (!validateManagerOutlineField(control)) {
          managerOutlineStatusEl.textContent = '시간은 시:분:초 형식으로 입력해 주세요. 예: 00:20:39';
          return false;
        }
        const seconds = outlineSeconds(control.value);
        let changed = false;
        if (kind === 'point') changed = movePoint(id, seconds, snapshot, false, control);
        else if (kind === 'highlight') return false;
        else changed = resizeOutlineRange(id, field, seconds, snapshot, false);
        managerOutlinePendingInput = null;
        if (changed) scheduleManagerOutlineDraftSync('outline');
        return changed;
      }
      item[field] = control.value;
      rememberManagerOutlineChange(`${item.level || (kind === 'point' ? 'Point' : 'Highlight')} ${field === 'title' ? '제목' : '내용'} 수정`, snapshot);
      managerOutlinePendingInput = null;
      setDirty(true);
      managerOutlineStatusEl.textContent = '입력 내용을 수정했어요. [되돌리기] 또는 저장을 선택할 수 있어요.';
      renderManagerOutlineNavigator();
      renderManagerOutlineTimeline();
      return true;
    }

    function dismissVisibleManagerOutlineTooltipOnEscape(event) {
      if (event.key !== 'Escape' || managerOutlineTimelineTooltipEl.hidden || !activeManagerOutlineTooltipTarget) return;
      suppressManagerOutlineTooltip(activeManagerOutlineTooltipTarget);
    }
