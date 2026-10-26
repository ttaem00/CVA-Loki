    function clampTimelineZoom(value) {
      const n = Number(value);
      if (!Number.isFinite(n)) return TIMELINE_ZOOM_DEFAULT;
      return Math.min(TIMELINE_ZOOM_MAX, Math.max(TIMELINE_ZOOM_MIN, n));
    }

    function sectionBodyId(section) {
      return `${section.id || 'cockpit'}Body`;
    }

    function cockpitSectionStatus(section) {
      if (!section) return '';
      if (section.id === 'signalTimelineSection') {
        const count = signalTimelineState && Array.isArray(signalTimelineState.events) ? signalTimelineState.events.length : 0;
        return count ? `${count}개 표시` : '자료 없음';
      }
      if (section.id === 'selectedEvidenceSection') {
        if (selectedEvidenceKind === 'story_window') return 'Story Window 후보 선택';
        if (selectedEvidenceKind === 'context_range') return '맥락 범위 후보 선택';
        if (selectedEvidenceKind === 'moment_seed') return '사건 시점 후보 선택';
        return Number.isFinite(selectedCockpitSec) ? displayTime(selectedCockpitEvent, selectedCockpitSec) : '시각 미선택';
      }
      if (section.id === 'managerNoteSection') {
        return managerNoteInputEl && managerNoteInputEl.value.trim() ? '메모 입력됨' : '초안 없음';
      }
      if (section.id === 'approvalInboxSection') {
        const text = approvalInboxBodyEl ? approvalInboxBodyEl.innerText || '' : '';
        const match = text.match(/대기\s*(\d+)/);
        return match ? `대기 ${match[1]}개` : '승인 대기 없음';
      }
      if (section.id === 'summaryCandidateSection') {
        return timelineCandidateMarkdown.trim() ? '후보 준비됨' : '후보 없음';
      }
      if (section.id === 'savedDecisionsSection') {
        const count = signalTimelineState && signalTimelineState.selected_ranges_draft && Array.isArray(signalTimelineState.selected_ranges_draft.selected_ranges)
          ? signalTimelineState.selected_ranges_draft.selected_ranges.length
          : 0;
        return count ? `${count}개 저장` : '표시 없음';
      }
      if (section.id === 'bundleAlignmentSection') return section.style.display === 'none' ? '번들 아님' : '번들 근거';
      if (section.id === 'bundleMembershipSection') return section.style.display === 'none' ? '번들 아님' : '구성 편집';
      return '';
    }

    function updateCockpitSectionStates() {
      document.querySelectorAll('.cockpit-section[data-collapsible="true"]').forEach((section) => {
        const state = section.querySelector('.cockpit-section-state');
        if (state) state.textContent = cockpitSectionStatus(section);
      });
    }

    function setCockpitSectionOpen(section, open) {
      if (!section) return;
      const body = section.querySelector('.cockpit-section-body');
      const button = section.querySelector('.cockpit-section-toggle');
      section.classList.toggle('collapsed', !open);
      if (body) body.hidden = !open;
      if (button) button.setAttribute('aria-expanded', open ? 'true' : 'false');
      updateCockpitSectionStates();
    }

    function openCockpitSection(sectionId) {
      setCockpitSectionOpen(document.getElementById(sectionId), true);
    }

    function initCockpitDisclosures() {
      document.querySelectorAll('.cockpit-flow > .cockpit-section').forEach((section) => {
        if (!section.id || section.dataset.collapsible === 'true') return;
        const heading = section.querySelector(':scope > h3');
        if (!heading) return;
        const bodyId = sectionBodyId(section);
        const body = document.createElement('div');
        body.id = bodyId;
        body.className = 'cockpit-section-body';
        while (heading.nextSibling) body.appendChild(heading.nextSibling);
        const title = heading.textContent || '조종석 섹션';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'cockpit-section-toggle';
        button.setAttribute('aria-controls', bodyId);
        button.innerHTML = `<span class="cockpit-section-chevron" aria-hidden="true">▾</span><span>${escapeHtml(title)}</span><span class="cockpit-section-state"></span>`;
        heading.textContent = '';
        heading.appendChild(button);
        section.appendChild(body);
        section.dataset.collapsible = 'true';
        button.addEventListener('click', () => setCockpitSectionOpen(section, section.classList.contains('collapsed')));
        setCockpitSectionOpen(section, COCKPIT_SECTION_DEFAULT_OPEN.has(section.id));
      });
      updateCockpitSectionStates();
    }
    function readTimelineZoom() {
      try {
        return clampTimelineZoom(parseFloat(localStorage.getItem(TIMELINE_ZOOM_STORAGE_KEY) || ''));
      } catch (_) {
        return TIMELINE_ZOOM_DEFAULT;
      }
    }
    function saveTimelineZoom(value) {
      try { localStorage.setItem(TIMELINE_ZOOM_STORAGE_KEY, String(clampTimelineZoom(value))); } catch (_) {}
    }

    function setStatus(msg, kind = 'info') {
      statusEl.textContent = msg || '';
      statusEl.classList.remove('ok', 'err', 'warn');
      if (kind === 'err') statusEl.classList.add('err');
      else if (kind === 'ok') statusEl.classList.add('ok');
      else if (kind === 'warn') statusEl.classList.add('warn');
    }
    function updateEditorDecisionState() {
      if (!editorDecisionStateEl) return;
      if (dirty) {
        editorDecisionStateEl.textContent = '변경 사항이 있습니다. 미리보기로 확인한 뒤 로컬 저장 여부를 결정하세요.';
      } else {
        editorDecisionStateEl.textContent = '저장된 상태입니다. 공개 반영은 별도 공개 버튼과 확인을 거쳐야 실행됩니다.';
      }
    }
    function setDirty(v) {
      dirty = !!v;
      titleEl.classList.toggle('dirty', dirty);
      document.title = (dirty ? '● ' : '') + (titleTextEl.textContent || 'Report Admin');
      // 비개발자에게 dirty 점은 너무 미묘 — 저장 버튼 라벨로 명시.
      const saveBtn = document.getElementById('saveBtn');
      if (saveBtn) {
        saveBtn.textContent = dirty ? '저장 (변경 있음)' : '저장됨';
        saveBtn.classList.toggle('has-changes', dirty);
      }
      updateEditorDecisionState();
    }
    function updateCopyBtnState() {
      const text = (youtubeState && youtubeState.text || '').trim();
      youtubeCopyBtn.disabled = !text;
    }

    function escapeHtml(s) {
      return String(s || '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[c]));
    }


    function fmtSeconds(sec) {
      const n = Math.max(0, Number(sec) || 0);
      const h = Math.floor(n / 3600);
      const m = Math.floor((n % 3600) / 60);
      const s = Math.floor(n % 60);
      const two = (v) => String(v).padStart(2, '0');
      return h
        ? `${two(h)}:${two(m)}:${two(s)}`
        : `${two(m)}:${two(s)}`;
    }

    function parseTimeInput(value) {
      const text = String(value || '').trim().replace(/^VOD\s+\d+\s+/i, '');
      if (!text) return NaN;
      if (!text.includes(':')) return Number(text);
      const parts = text.split(':').map((part) => Number(part.trim()));
      if (parts.some((part) => !Number.isFinite(part))) return NaN;
      if (parts.length === 2) return parts[0] * 60 + parts[1];
      if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
      return NaN;
    }

    function parsePreviewTimelineTarget(value) {
      const raw = String(value || '').trim();
      const target = { sec: NaN, videoNo: '', vodLabel: '', title: '' };
      const vodMatch = raw.match(/\b(VOD\s+\d+)\b/i);
      if (vodMatch) target.vodLabel = vodMatch[1].replace(/\s+/, ' ').toUpperCase();
      if (!raw) return target;
      try {
        const url = new URL(raw, window.location.href);
        const currentTime = url.searchParams.get('currentTime');
        const pathParts = url.pathname.split('/').filter(Boolean);
        const videoIndex = pathParts.indexOf('video');
        if (videoIndex >= 0 && pathParts[videoIndex + 1]) {
          target.videoNo = decodeURIComponent(pathParts[videoIndex + 1]);
        }
        if (currentTime !== null) {
          const sec = Number(currentTime);
          if (Number.isFinite(sec)) {
            target.sec = Math.max(0, sec);
            return target;
          }
        }
      } catch (_) {}
      const match = raw.match(/(?:[0-9]{1,2}:)?[0-9]{1,2}:[0-9]{2}/);
      if (match) target.sec = parseTimeInput(match[0]);
      return target;
    }

    function parsePreviewTimelineSeconds(value) {
      return parsePreviewTimelineTarget(value).sec;
    }

    function previewTimelineTargetFromClick(target, card) {
      if (!card) return { sec: NaN, videoNo: '', vodLabel: '', title: '' };
      const nearestTc = target && target.closest ? target.closest('.tc') : null;
      const timeEl = nearestTc || card.querySelector('.tc');
      if (!timeEl) return { sec: NaN, videoNo: '', vodLabel: '', title: '' };
      const title = String(card.querySelector('.t-title') && card.querySelector('.t-title').textContent || '').trim();
      if (timeEl.getAttribute) {
        const href = timeEl.getAttribute('href') || '';
        const fromHref = parsePreviewTimelineTarget(href);
        if (Number.isFinite(fromHref.sec)) {
          const fromText = parsePreviewTimelineTarget(timeEl.textContent || card.textContent || '');
          return {
            sec: fromHref.sec,
            videoNo: fromHref.videoNo || fromText.videoNo,
            vodLabel: fromHref.vodLabel || fromText.vodLabel,
            title,
          };
        }
      }
      return { ...parsePreviewTimelineTarget(timeEl.textContent || ''), title };
    }

    function sourceLocalSec(row) {
      const seek = row && row.seek_sec != null ? Number(row.seek_sec) : NaN;
      return Number.isFinite(seek) ? seek : Number(row && row.start_sec);
    }

    function normalizedPreviewText(value) {
      return String(value || '').replace(/\s+/g, '').trim();
    }

    function eventMatchesPreviewTitle(event, title) {
      const needle = normalizedPreviewText(title);
      if (!needle) return false;
      const haystack = normalizedPreviewText([
        event && event.label,
        event && event.title,
        event && event.summary,
      ].filter(Boolean).join(' '));
      return !!haystack && (haystack.includes(needle) || needle.includes(haystack));
    }

    function findPreviewTimelineEvent(sec, segmentId, target = {}) {
      const events = Array.isArray(signalTimelineState.events) ? signalTimelineState.events : [];
      if (segmentId) {
        const bySegment = events.find((event) => String(event.segment_id || '') === String(segmentId));
        if (bySegment) {
          const targetVideoNo = String(target.videoNo || '').trim();
          if (!targetVideoNo || rowVideoNo(bySegment) === targetVideoNo) return bySegment;
        }
      }
      const targetVideoNo = String(target.videoNo || '').trim();
      const targetVodLabel = String(target.vodLabel || '').trim().toUpperCase();
      if (Number.isFinite(sec) && (targetVideoNo || targetVodLabel)) {
        const sourceMatches = events.filter((event) => {
          const sameVideo = targetVideoNo ? rowVideoNo(event) === targetVideoNo : true;
          const sameLabel = targetVodLabel ? String(event.vod_label || '').trim().toUpperCase() === targetVodLabel : true;
          return sameVideo && sameLabel && Math.abs(sourceLocalSec(event) - sec) <= 2;
        });
        if (sourceMatches.length) {
          const titledMatch = sourceMatches.find((event) => eventMatchesPreviewTitle(event, target.title));
          if (titledMatch) return titledMatch;
          for (const lane of ['existing_segments', 'timeline', 'highlight', 'comment_replay']) {
            const laneMatches = sourceMatches.filter((event) => String(event.lane || '') === lane);
            const titled = laneMatches.find((event) => eventMatchesPreviewTitle(event, target.title));
            if (titled) return titled;
            const preferred = laneMatches[0];
            if (preferred) return preferred;
          }
          return sourceMatches[0];
        }
      }
      const nearExisting = events.find((event) => {
        const start = Number(event.start_sec);
        return event.lane === 'existing_segments' && Number.isFinite(start) && Math.abs(start - sec) <= 2;
      });
      if (nearExisting) return nearExisting;
      return events.find((event) => {
        const start = Number(event.start_sec);
        return Number.isFinite(start) && Math.abs(start - sec) <= 2;
      }) || null;
    }

    // #1440 split view: 플레이어는 cross-origin이라 JS로 제어할 수 없고(#1163),
    // seek는 iframe src 를 ?currentTime=초 로 교체하는 방식만 동작한다.
    function splitPlayerUrl(sec) {
      if (!splitVideoNo) return '';
      const url = `https://chzzk.naver.com/video/${encodeURIComponent(splitVideoNo)}`;
      if (!Number.isFinite(sec)) return url;
      return `${url}?currentTime=${Math.max(0, Math.floor(sec))}`;
    }

    function setSplitView(on, opts) {
      splitViewOn = !!on && !!splitVideoNo;
      const mainEl = document.querySelector('.main');
      if (mainEl) mainEl.classList.toggle('split-view', splitViewOn);
      if (splitViewToggleBtn) {
        splitViewToggleBtn.setAttribute('aria-pressed', splitViewOn ? 'true' : 'false');
        splitViewToggleBtn.textContent = splitViewOn ? '🎬 장면 보기 닫기' : '🎬 장면 보며 편집';
      }
      if (!splitViewOn) {
        // 닫으면 소리/재생도 함께 멈추도록 플레이어를 비운다.
        if (splitPlayerFrame) splitPlayerFrame.src = 'about:blank';
      } else {
        const url = splitPlayerUrl(NaN);
        if (splitPlayerNewTabEl && url) splitPlayerNewTabEl.href = url;
        if (splitPlayerFrame && (!splitPlayerFrame.src || splitPlayerFrame.src === 'about:blank')) {
          splitPlayerFrame.src = url;
          if (splitPlayerStatusEl) {
            splitPlayerStatusEl.textContent = '플레이어 로딩 중… 광고가 먼저 나오면 본편부터 장면 위치가 적용됩니다.';
          }
        }
      }
      if (!opts || !opts.silent) {
        try { localStorage.setItem('reportAdmin.splitView', splitViewOn ? '1' : '0'); } catch (_) {}
      }
    }

    function splitPlayerSeek(sec) {
      if (!splitViewOn || !splitPlayerFrame) return false;
      const url = splitPlayerUrl(sec);
      if (!url) return false;
      splitPlayerFrame.src = url;
      if (splitPlayerNewTabEl) splitPlayerNewTabEl.href = url;
      if (splitPlayerStatusEl) {
        splitPlayerStatusEl.textContent = `${fmtSeconds(sec)} 장면으로 이동 중 — 화면이 안 바뀌면 우측 '새 탭에서 열기'를 누르세요.`;
      }
      return true;
    }

    function setupSplitPlayerResizer() {
      const mainEl = document.querySelector('.main');
      const paneEl = splitPlayerPaneEl;
      const resizer = splitPlayerResizerEl;
      if (!mainEl || !paneEl || !resizer) return;
      const storageKey = 'reportAdmin.splitPlayerHeight';
      const defaultHeight = 300;
      function clampHeight(value) {
        const mainHeight = mainEl.getBoundingClientRect().height || window.innerHeight || 720;
        const max = Math.max(220, Math.min(620, Math.round(mainHeight * 0.62)));
        return Math.min(max, Math.max(180, Math.round(value)));
      }
      function readHeight(fallback) {
        const cssValue = parseInt(getComputedStyle(mainEl).getPropertyValue('--split-player-h'), 10);
        return Number.isFinite(cssValue) ? cssValue : fallback;
      }
      function applyHeight(value, persist) {
        const next = clampHeight(value);
        mainEl.style.setProperty('--split-player-h', next + 'px');
        if (persist) {
          try { localStorage.setItem(storageKey, String(next)); } catch (_) {}
        }
        return next;
      }
      try {
        const saved = parseInt(localStorage.getItem(storageKey) || '', 10);
        applyHeight(Number.isFinite(saved) ? saved : defaultHeight, false);
      } catch (_) {
        applyHeight(defaultHeight, false);
      }

      let dragging = false;
      let pendingClientY = NaN;
      let frame = 0;
      function heightFromClientY(clientY) {
        const rect = paneEl.getBoundingClientRect();
        return clientY - rect.top;
      }
      function flushMove() {
        frame = 0;
        if (!dragging || !Number.isFinite(pendingClientY)) return;
        applyHeight(heightFromClientY(pendingClientY), false);
      }
      function queueMove(clientY) {
        pendingClientY = clientY;
        if (!frame) frame = requestAnimationFrame(flushMove);
      }
      function finishDrag() {
        if (!dragging) return;
        dragging = false;
        resizer.classList.remove('dragging');
        document.body.classList.remove('row-resizing');
        if (frame) {
          cancelAnimationFrame(frame);
          frame = 0;
          flushMove();
        }
        applyHeight(readHeight(defaultHeight), true);
      }
      resizer.addEventListener('pointerdown', (event) => {
        dragging = true;
        pendingClientY = event.clientY;
        resizer.setPointerCapture(event.pointerId);
        resizer.classList.add('dragging');
        document.body.classList.add('row-resizing');
        queueMove(event.clientY);
        event.preventDefault();
      });
      resizer.addEventListener('pointermove', (event) => {
        if (!dragging) return;
        queueMove(event.clientY);
        event.preventDefault();
      });
      resizer.addEventListener('pointerup', finishDrag);
      resizer.addEventListener('pointercancel', finishDrag);
      resizer.addEventListener('dblclick', (event) => {
        if (event.ctrlKey || event.metaKey) applyHeight(defaultHeight, true);
      });
      resizer.addEventListener('keydown', (event) => {
        const cur = readHeight(defaultHeight);
        const step = event.shiftKey ? 48 : 16;
        const direction = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
        if (direction) {
          applyHeight(cur + direction * step, true);
          event.preventDefault();
        }
      });
      window.addEventListener('resize', () => {
        applyHeight(readHeight(defaultHeight), true);
      });
    }

    function bindPreviewTimelineCardClicks() {
      let doc;
      try {
        doc = previewFrame && previewFrame.contentDocument;
      } catch (_) {
        return;
      }
      if (!doc) return;
      if (doc.__reportAdminTimelineCardClickBound) return;
      doc.__reportAdminTimelineCardClickBound = true;
      doc.addEventListener('click', (event) => {
        const target = event.target;
        if (!target || !target.closest) return;
        const card = target.closest('.timeline .t-item');
        if (!card) return;
        const timelineTarget = previewTimelineTargetFromClick(target, card);
        if (!Number.isFinite(timelineTarget.sec)) return;
        event.preventDefault();
        const segmentId = card.dataset ? card.dataset.segmentId || '' : '';
        const matchedEvent = findPreviewTimelineEvent(timelineTarget.sec, segmentId, timelineTarget);
        const selectSec = matchedEvent && Number.isFinite(Number(matchedEvent.start_sec))
          ? Number(matchedEvent.start_sec)
          : timelineTarget.sec;
        selectCockpitTime(selectSec, matchedEvent && matchedEvent.event_id || '', {
          updateLegacyDraft: false,
        });
        selectManagerOutlineAtTime(selectSec);
        openCockpitSection('selectedEvidenceSection');
        setStatus(`${displayTime(matchedEvent || timelineTarget, selectSec)} 카드와 방송 목차, 근거를 함께 선택했습니다.`, 'ok');
      });
    }

    function laneMarkerColor(kind) {
      const colors = signalTimelineState && signalTimelineState.colors || {};
      return colors[kind] || 'rgba(122,162,247,0.86)';
    }

    function evidenceKindLabel(kind) {
      return ({
        segment: '요약 항목',
        chat: '채팅 반응',
        comment_replay: '댓글 시간표 후보',
        viewer_clip: '시청자 클립',
        audio_peak: '오디오 신호',
        subtitle: '자막',
        source: '근거',
      }[kind] || kind || '근거');
    }

    function rangeKey(item) {
      const start = Math.round(Number(item && item.start_sec || 0) * 1000) / 1000;
      const end = Math.round(Number(item && item.end_sec || 0) * 1000) / 1000;
      return [
        start,
        end,
        item && item.action || '',
        item && item.segment_id || '',
        item && item.source_event_id || '',
      ].join('|');
    }

    function setDraftFormMode(rangeId = '') {
      signalDraftEditingRangeId = rangeId || '';
      const saveBtn = document.getElementById('saveSignalDraftBtn');
      const cancelBtn = document.getElementById('cancelSignalDraftEditBtn');
      if (saveBtn) saveBtn.textContent = signalDraftEditingRangeId ? '선택 범위 수정' : '선택 범위 저장';
      if (cancelBtn) cancelBtn.style.display = signalDraftEditingRangeId ? '' : 'none';
    }

    function clearDraftForm() {
      ['signalDraftStart', 'signalDraftEnd', 'signalDraftEventId', 'signalDraftSegmentId', 'signalDraftNote'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = '';
      });
      const actionInput = document.getElementById('signalDraftAction');
      if (actionInput) actionInput.value = 'note';
      setDraftFormMode('');
    }

    async function copyAdminToolText(kind) {
      const preview = editorToolsSectionEl && editorToolsSectionEl._preview || {};
      const text = kind === 'json'
        ? JSON.stringify(preview.otio || {}, null, 2)
        : String(preview.edl || '');
      if (!text.trim()) {
        setStatus('복사할 컷 목록 데이터가 없습니다.', 'err');
        return;
      }
      try {
        await navigator.clipboard.writeText(text);
        setStatus(kind === 'json' ? 'JSON 미리보기 복사 완료' : '컷 목록 복사 완료', 'ok');
      } catch (err) {
        setStatus('브라우저가 복사를 막았습니다. 오른쪽 패널에서 직접 복사해 주세요.', 'err');
      }
    }

    function renderEditorTools(preview) {
      if (!editorToolsSectionEl || !editorToolsBodyEl) return;
      editorToolsSectionEl._preview = preview || {};
      const hasPreview = preview && typeof preview === 'object' && Array.isArray(preview.clips);
      editorToolsSectionEl.style.display = hasPreview ? '' : 'none';
      if (!hasPreview) {
        editorToolsBodyEl.innerHTML = '<div class="meta-note">복사할 컷 목록이 없어서 이 보조 패널은 숨깁니다.</div>';
        return;
      }
      const clips = preview.clips || [];
      const failures = preview.validation && Array.isArray(preview.validation.failures)
        ? preview.validation.failures
        : [];
      const rows = clips.slice(0, 20).map((clip) => `
        <div class="tool-row">
          <strong>${escapeHtml(clip.title || clip.segment_id || '제목 없음')}</strong><br>
          ${escapeHtml(clip.kind || 'segment')} · ${fmtSeconds(clip.start_sec)}–${fmtSeconds(clip.end_sec)}
          ${clip.segment_id ? ` · <span class="mono">${escapeHtml(clip.segment_id)}</span>` : ''}
        </div>
      `).join('');
      const more = clips.length > 20
        ? `<div class="meta-note">외 ${clips.length - 20}개 컷은 복사한 원본 데이터에 포함됩니다.</div>`
        : '';
      const failureText = failures.length
        ? `<div class="meta-note">확인 필요: ${escapeHtml(failures.map((f) => f.code || f.message || 'unknown').join(', '))}</div>`
        : '<div class="meta-note">요약을 고치는 필수 단계는 아닙니다. 영상 편집 프로그램이나 외부 도구로 컷 후보를 복사할 때만 사용하세요. 자동 렌더링이나 업로드는 하지 않습니다.</div>';
      editorToolsBodyEl.innerHTML = `
        <div class="meta-note">현재 보고서의 타임라인/하이라이트를 컷 목록 형태로 바꾼 보조 자료입니다.</div>
        <div class="tool-summary">
          <div class="tool-stat"><span class="tool-label">상태</span><span class="tool-value">${escapeHtml(preview.status || 'UNVERIFIED')}</span></div>
          <div class="tool-stat"><span class="tool-label">컷 수</span><span class="tool-value">${Number(preview.clip_count || clips.length || 0)}</span></div>
          <div class="tool-stat"><span class="tool-label">기준</span><span class="tool-value">${escapeHtml(preview.source || 'segment_ledger')}</span></div>
        </div>
        ${failureText}
        <div class="tool-list">${rows || '<div class="meta-note">표시할 컷이 없습니다.</div>'}</div>
        ${more}
        <div class="tool-actions">
          <button type="button" class="ghost" id="copyAdminEdlBtn">컷 목록 복사</button>
          <button type="button" class="ghost" id="copyAdminJsonBtn">JSON 복사</button>
        </div>
        <details>
          <summary>원본 컷 목록 보기</summary>
          <pre class="tool-pre">${escapeHtml(preview.edl || '')}</pre>
        </details>
      `;
      const edlBtn = document.getElementById('copyAdminEdlBtn');
      const jsonBtn = document.getElementById('copyAdminJsonBtn');
      if (edlBtn) edlBtn.addEventListener('click', () => copyAdminToolText('edl'));
      if (jsonBtn) jsonBtn.addEventListener('click', () => copyAdminToolText('json'));
    }

    function evidenceText(event) {
      const items = Array.isArray(event && event.evidence) ? event.evidence : [];
      if (!items.length) return '<div class="meta-note">표시할 근거가 없습니다.</div>';
      return items.slice(0, 8).map((item) => `
        <div class="tool-row">
          <strong>${escapeHtml(evidenceKindLabel(item.type))}${item.label ? ` · ${escapeHtml(item.label)}` : ''}</strong><br>
          ${escapeHtml(item.text || '')}
        </div>
      `).join('');
    }

    function isNearTime(row, sec, radius = 45) {
      const start = Number(row && row.start_sec);
      const endRaw = row && row.end_sec != null ? Number(row.end_sec) : start;
      const end = Number.isFinite(endRaw) ? endRaw : start;
      if (!Number.isFinite(start) || !Number.isFinite(sec)) return false;
      return start - radius <= sec && sec <= end + radius;
    }

    function rowVideoNo(row) {
      return String(row && (row.video_no || row.source_video_no || '') || '').trim();
    }

    function sameVod(row, source) {
      const sourceVideoNo = rowVideoNo(source);
      if (!sourceVideoNo) return true;
      const targetVideoNo = rowVideoNo(row);
      return !targetVideoNo || targetVideoNo === sourceVideoNo;
    }

    function displayTime(row, fallbackSec) {
      const vodLabel = String(row && row.vod_label || '').trim();
      const seek = row && row.seek_sec != null ? Number(row.seek_sec) : NaN;
      const local = Number.isFinite(seek) ? seek : Number(fallbackSec);
      if (vodLabel) return `${vodLabel} ${fmtSeconds(local)}`.trim();
      if (row && row.timecode) return String(row.timecode);
      return `${vodLabel ? `${vodLabel} ` : ''}${fmtSeconds(local)}`.trim();
    }

    function seekUrl(row, fallbackSec) {
      const videoNo = String(row && (row.video_no || row.source_video_no) || '').trim();
      if (!videoNo) return '';
      const seek = row && row.seek_sec != null ? Number(row.seek_sec) : Number(fallbackSec);
      if (!Number.isFinite(seek)) return '';
      return `https://chzzk.naver.com/video/${encodeURIComponent(videoNo)}?currentTime=${Math.max(0, Math.floor(seek))}`;
    }

    function timeLink(row, fallbackSec) {
      const label = displayTime(row, fallbackSec);
      const url = seekUrl(row, fallbackSec);
      return url
        ? `<a class="time-link" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`
        : escapeHtml(label);
    }

    function timeChip(row, fallbackSec) {
      const label = displayTime(row, fallbackSec);
      const url = seekUrl(row, fallbackSec);
      return url
        ? `<a class="viewer-editor-chip" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`
        : `<span class="viewer-editor-chip">${escapeHtml(label)}</span>`;
    }

    function timecodeToSec(value) {
      const parts = String(value || '').split(':').map((part) => Number(part));
      if (parts.some((part) => !Number.isFinite(part))) return NaN;
      if (parts.length === 2) return parts[0] * 60 + parts[1];
      if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
      return NaN;
    }

    function originStartSec(event) {
      const value = event && event.anchor_origin_start_sec != null ? Number(event.anchor_origin_start_sec) : Number(event && event.start_sec);
      return Number.isFinite(value) ? value : NaN;
    }

    function originSeekSec(event) {
      const value = event && event.anchor_origin_seek_sec != null ? Number(event.anchor_origin_seek_sec) : Number(event && event.seek_sec);
      return Number.isFinite(value) ? value : NaN;
    }

    function sourceTimeTarget(localOrGlobalSec, event) {
      const sec = Number(localOrGlobalSec);
      if (!Number.isFinite(sec)) return null;
      const rootStart = originStartSec(event);
      const rootSeek = originSeekSec(event);
      const hasSourceLocal = event && event.vod_label && Number.isFinite(rootStart) && Number.isFinite(rootSeek);
      const startSec = hasSourceLocal ? rootStart + (sec - rootSeek) : sec;
      const row = {
        ...(event || {}),
        start_sec: startSec,
        sec: startSec,
        timecode: hasSourceLocal ? (event && event.timecode) : fmtSeconds(startSec),
        anchor_origin_start_sec: Number.isFinite(rootStart) ? rootStart : startSec,
        anchor_origin_seek_sec: Number.isFinite(rootSeek) ? rootSeek : (event && event.seek_sec),
      };
      if (hasSourceLocal) row.seek_sec = sec;
      return row;
    }

    function explicitEvidenceTarget(item, event) {
      const explicit = item && item.start_sec != null ? Number(item.start_sec) : item && item.sec != null ? Number(item.sec) : NaN;
      if (!Number.isFinite(explicit)) return null;
      return {
        ...(event || {}),
        ...(item || {}),
        start_sec: explicit,
        sec: explicit,
        timecode: item && item.timecode ? item.timecode : fmtSeconds(explicit),
        anchor_origin_start_sec: originStartSec(event),
        anchor_origin_seek_sec: originSeekSec(event),
      };
    }

    function pushEvidenceTarget(targets, row, label) {
      if (!row) return;
      const sec = Number(row.start_sec != null ? row.start_sec : row.sec);
      if (!Number.isFinite(sec)) return;
      const videoNo = String(row.video_no || row.source_video_no || '').trim();
      const key = `${videoNo}|${Math.round(sec * 10) / 10}`;
      if (targets.some((target) => target.key === key)) return;
      targets.push({ key, label: label || '근거 시각', row });
    }

    function evidenceTargetsForEvent(event, selectedSec) {
      const targets = [];
      const cardRow = explicitEvidenceTarget({ start_sec: Number(selectedSec) }, event) || sourceTimeTarget(selectedSec, event);
      pushEvidenceTarget(targets, cardRow, '카드 시각');
      const rows = []
        .concat(Array.isArray(event && event.evidence) ? event.evidence : [])
        .concat(Array.isArray(event && event.guidance) ? event.guidance : []);
      rows.forEach((item) => {
        const label = friendlyEvidenceLabel(item && (item.label || item.title || item.type) || '근거');
        pushEvidenceTarget(targets, explicitEvidenceTarget(item, event), label);
        const text = `${item && item.timecode || ''} ${item && item.text || ''}`;
        const re = /\b(\d{1,2}:\d{2}(?::\d{2})?)\b/g;
        for (const match of text.matchAll(re)) {
          const sec = timecodeToSec(match[1]);
          pushEvidenceTarget(targets, sourceTimeTarget(sec, event), `${label} ${match[1]}`);
        }
      });
      return targets.slice(0, 8);
    }

    function renderEvidenceTargetPicker(targets, activeSec) {
      if (!Array.isArray(targets) || targets.length <= 1) return '';
      const buttons = targets.map((target, index) => {
        const sec = Number(target.row && target.row.start_sec);
        const active = Number.isFinite(sec) && Math.abs(sec - Number(activeSec || 0)) < 0.5;
        return `<button type="button" class="viewer-editor-chip ${active ? 'selected' : ''}" data-evidence-target-index="${index}">${escapeHtml(target.label)} · ${escapeHtml(displayTime(target.row, sec))}</button>`;
      }).join('');
      return `<div class="viewer-editor-target-picker" aria-label="근거 시각 선택"><strong>근거 시각 선택</strong><div class="viewer-editor-target-buttons">${buttons}</div></div>`;
    }

    function linkTimecodesText(text, sourceEvent = null) {
      const raw = String(text || '');
      const re = /\b(\d{1,2}:\d{2}(?::\d{2})?)\b/g;
      let out = '';
      let last = 0;
      for (const match of raw.matchAll(re)) {
        out += escapeHtml(raw.slice(last, match.index));
        const sec = timecodeToSec(match[1]);
        const row = sourceEvent
          ? sourceScopedRow({ start_sec: sec }, sourceEvent)
          : { start_sec: sec };
        out += Number.isFinite(sec) ? timeLink(row, sec) : escapeHtml(match[1]);
        last = match.index + match[1].length;
      }
      out += escapeHtml(raw.slice(last));
      return out;
    }

    function isTimeOnlyTitle(title, sec) {
      const raw = String(title || '').trim();
      if (!raw) return true;
      return raw === fmtSeconds(sec) || /^\d{1,2}:\d{2}(:\d{2})?$/.test(raw);
    }

    function friendlyVisualSceneTitle(rawValue) {
      const raw = String(rawValue || '').trim();
      const lowered = raw.toLowerCase();
      if (/ocr|generic_text|korean_text|loading_text|menu_text|numeric_text/.test(lowered)) {
        return '화면 글자 후보';
      }
      if (/game_|match_|round_|loading|menu|lobby|scoreboard|화면 상태/.test(lowered)) {
        return '화면 상태 변화 후보';
      }
      return '화면 전환·상태 변화 후보';
    }

    function friendlyVisualSceneEvidence() {
      return '화면에서 글자 또는 상태 변화가 감지된 보조 후보입니다.';
    }

    function selectedTitle(event, sec) {
      const raw = String(event && (event.label || event.title || event.summary || '') || '').trim();
      const lane = String(event && event.lane || '');
      if (lane === 'visual_scene') return friendlyVisualSceneTitle(raw);
      if (!isTimeOnlyTitle(raw, sec)) return raw;
      if (lane === 'timeline' || lane === 'existing_segments') return '요약에 포함된 장면';
      if (lane === 'highlight') return '하이라이트 추천 장면';
      const label = laneOptionLabel(lane);
      return label.endsWith('후보') ? label : `${label} 후보`;
    }

    function selectedSummaryStatus(event, nearbyEvents) {
      const lane = String(event && event.lane || '');
      if (lane === 'timeline' || lane === 'existing_segments') {
        return { label: '요약에 포함됨', inSummary: true, warning: false };
      }
      if (lane === 'highlight') {
        return { label: '하이라이트 추천', inSummary: true, warning: false };
      }
      const nearSummary = nearbyEvents.some((row) => row.lane === 'timeline' || row.lane === 'existing_segments');
      if (nearSummary) return { label: '요약 근처 장면', inSummary: true, warning: false };
      const nearHighlight = nearbyEvents.some((row) => row.lane === 'highlight');
      if (nearHighlight) return { label: '하이라이트 근처', inSummary: true, warning: false };
      return { label: '요약 밖 참고 장면', inSummary: false, warning: true };
    }

    function sourceScopedRow(row, sourceEvent) {
      const scoped = { ...(row || {}) };
      if (!sourceEvent) return scoped;
      if (!scoped.video_no && sourceEvent.video_no) scoped.video_no = sourceEvent.video_no;
      if (!scoped.source_video_no && sourceEvent.source_video_no) scoped.source_video_no = sourceEvent.source_video_no;
      if (!scoped.vod_label && sourceEvent.vod_label) scoped.vod_label = sourceEvent.vod_label;
      const anchorStart = Number(sourceEvent.start_sec);
      const anchorSeek = sourceEvent.seek_sec != null ? Number(sourceEvent.seek_sec) : NaN;
      const rowStart = Number(scoped.start_sec);
      if (scoped.seek_sec == null && Number.isFinite(anchorStart) && Number.isFinite(anchorSeek) && Number.isFinite(rowStart)) {
        scoped.seek_sec = Math.max(0, anchorSeek + (rowStart - anchorStart));
      }
      const rowEnd = scoped.end_sec != null ? Number(scoped.end_sec) : NaN;
      if (scoped.end_seek_sec == null && Number.isFinite(anchorStart) && Number.isFinite(anchorSeek) && Number.isFinite(rowEnd)) {
        scoped.end_seek_sec = Math.max(0, anchorSeek + (rowEnd - anchorStart));
      }
      return scoped;
    }

    function contextLine(row, sourceEvent) {
      const scoped = sourceScopedRow(row, sourceEvent);
      return `<div class="viewer-editor-context-line"><span class="viewer-editor-context-time">${timeLink(scoped, scoped.start_sec)}</span><span class="viewer-editor-context-text">${linkTimecodesText(scoped.text || '', scoped)}</span></div>`;
    }

    function renderContextSummary(context) {
      const sourceEvent = context.source_event || null;
      const subtitleRows = (context.subtitles || [])
        .filter((row) => row && row.text)
        .slice(0, 3);
      const hiddenSubtitleRows = (context.subtitles || [])
        .filter((row) => row && row.text)
        .slice(3);
      const subtitleHtml = subtitleRows.length
        ? subtitleRows.map((row) => contextLine(row, sourceEvent)).join('') + (hiddenSubtitleRows.length ? `<details class="viewer-editor-context-more"><summary>자막 ${hiddenSubtitleRows.length}줄 더 보기</summary>${hiddenSubtitleRows.map((row) => contextLine(row, sourceEvent)).join('')}</details>` : '')
        : '<div class="viewer-editor-empty">근처 자막 없음</div>';
      const chatRows = (context.chat_buckets || [])
        .filter((row) => Number(row.count || 0) > 0 || (row.samples || []).length)
        .slice(0, 2);
      const chatHtml = chatRows.length
        ? chatRows.map((row) => {
            const scoped = sourceScopedRow(row, sourceEvent);
            const sample = (scoped.samples || []).map((item) => item.text).filter(Boolean).slice(0, 3).join(' / ');
            return `<div class="viewer-editor-context-line"><span class="viewer-editor-context-time">${timeLink(scoped, scoped.start_sec)}</span><span class="viewer-editor-context-text">채팅 ${Number(scoped.count || 0)}개${sample ? ` · ${linkTimecodesText(sample, scoped)}` : ''}</span></div>`;
          }).join('')
        : '<div class="viewer-editor-empty">근처 채팅 샘플 없음</div>';
      return `<div class="viewer-editor-context" aria-label="선택 시각 주변 방송 맥락">
        <div class="viewer-editor-context-card"><strong>자막 흐름</strong>${subtitleHtml}</div>
        <div class="viewer-editor-context-card"><strong>채팅 반응</strong>${chatHtml}</div>
      </div>`;
    }

    function friendlyEvidenceLabel(label) {
      const raw = String(label || '').trim();
      const lowered = raw.toLowerCase();
      if (!raw) return '근거';
      if (lowered.includes('summary_markdown')) return '요약에 적힌 장면';
      if (lowered.includes('chat_or_text_evidence')) return '채팅/자막 근거';
      if (lowered.includes('subtitle_chunk') || lowered.includes('timed_evidence')) return '자막 근거';
      if (lowered.includes('semantic_vector') || lowered.includes('timestamp_overlap')) return '가까운 방송 근거';
      if (lowered.includes('subtitle') || raw.includes('자막')) return '자막 근거';
      if (lowered.includes('chat') || raw.includes('채팅')) return raw.includes('대표') ? '대표 채팅 반응' : '채팅 반응';
      if (lowered.includes('viewer_clip') || raw.includes('클립')) return '시청자 클립';
      if (lowered.includes('audio') || raw.includes('오디오')) return '오디오 반응';
      if (lowered.includes('comment') || lowered.includes('chapter')) return '댓글 시간표 후보';
      if (lowered.includes('segment') || lowered.includes('timeline') || raw.includes('요약')) return '요약에 적힌 장면';
      return raw;
    }

    function friendlyEvidenceText(label, text) {
      const raw = String(text || '').trim();
      const joined = `${String(label || '')} ${raw}`;
      if (!raw) return '';
      if (/화면 전환[/]게임 시작|OCR 유형|OCR 해시|generic_text|korean_text|loading_text|menu_text|numeric_text|화면 상태 [a-z_]+/i.test(joined)) {
        return friendlyVisualSceneEvidence();
      }
      if (/summary_markdown|semantic_vector|subtitle_chunk|timed_evidence|timestamp_overlap|match_reason|evidence_id|source_type|chat_or_text_evidence/i.test(joined)) {
        return '장면 내용은 주변 자막과 요약으로 확인하세요.';
      }
      return raw;
    }

    function renderEvidenceRow(row, sourceEvent = null) {
      const scoped = sourceScopedRow(row, sourceEvent);
      const labelText = friendlyEvidenceLabel(scoped.label || '근거');
      const bodyText = friendlyEvidenceText(scoped.label || '근거', scoped.text || '');
      const label = scoped.sec != null || scoped.start_sec != null
        ? `${escapeHtml(labelText)} · ${timeLink(scoped, scoped.sec != null ? scoped.sec : scoped.start_sec)}`
        : escapeHtml(labelText);
      return `<div class="viewer-editor-row"><strong>${label}</strong><br>${linkTimecodesText(bodyText, scoped)}</div>`;
    }

    function evidenceContextForTime(sec, sourceEvent = null) {
      const events = Array.isArray(signalTimelineState.events) ? signalTimelineState.events : [];
      const context = signalTimelineState.context_index || {};
      const subtitles = Array.isArray(context.subtitles) ? context.subtitles : [];
      const chatBuckets = Array.isArray(context.chat_buckets) ? context.chat_buckets : [];
      const nearbyEvents = events.filter((event) => sameVod(event, sourceEvent) && isNearTime(event, sec, 45)).slice(0, 18);
      const nearbySubtitles = subtitles.filter((row) => sameVod(row, sourceEvent) && isNearTime(row, sec, 45)).slice(0, 12);
      const nearbyChats = chatBuckets.filter((row) => sameVod(row, sourceEvent) && isNearTime(row, sec, 45)).slice(0, 8);
      const modalities = new Set();
      nearbyEvents.forEach((event) => { if (event.lane) modalities.add(event.lane); });
      if (nearbySubtitles.length) modalities.add('subtitle_density');
      if (nearbyChats.length) modalities.add('chat_density');
      const existingSummary = nearbyEvents.some((event) => event.lane === 'existing_segments');
      const proofScore = modalities.size + (existingSummary ? 1 : 0);
      let strength = '근거 약함';
      if (proofScore >= 4 && nearbySubtitles.length) strength = '요약에 넣기 좋음';
      else if (proofScore >= 2) strength = '참고만';
      return {
        center_sec: sec,
        timecode: displayTime(sourceEvent, sec),
        video_no: rowVideoNo(sourceEvent),
        vod_label: String(sourceEvent && sourceEvent.vod_label || '').trim(),
        strength_label: strength,
        existing_summary: existingSummary,
        source_event: sourceEvent || null,
        events: nearbyEvents,
        subtitles: nearbySubtitles,
        chat_buckets: nearbyChats,
      };
    }

    function strengthClass(label) {
      if (label === '요약에 넣기 좋음') return 'strong';
      if (label === '참고만') return 'reference';
      return 'weak';
    }

    function renderEvidenceContext(context) {
      const target = document.getElementById('signalEvidenceBody');
      if (!target || !context) return;
      const selectedRow = context.source_event || context;
      const selectedSec = Number(selectedRow.start_sec != null ? selectedRow.start_sec : context.center_sec);
      const selectedTitleText = selectedTitle(selectedRow, selectedSec);
      const kind = String(selectedRow.lane || '');
      const selectionKind = timelineSelectionKind(selectedRow);
      const kindLabel = laneOptionLabel(kind);
      const summaryStatus = selectedSummaryStatus(selectedRow, context.events || []);
      const chipRows = [timeChip(selectedRow, context.center_sec)];
      if (kindLabel && summaryStatus.label !== kindLabel) chipRows.push(`<span class="viewer-editor-chip">${escapeHtml(kindLabel)}</span>`);
      chipRows.push(...adaptiveRangeHintChips(selectedRow));
      chipRows.push(`<span class="viewer-editor-chip ${summaryStatus.warning ? 'warning' : ''}">${escapeHtml(summaryStatus.label)}</span>`);
      const ownEvidenceRows = (selectedRow.evidence || []).map((item) => ({
        label: friendlyEvidenceLabel(item.label || evidenceKindLabel(item.type)),
        text: item.text || '',
        start_sec: item.start_sec,
        sec: item.sec,
        seek_sec: item.seek_sec,
        end_sec: item.end_sec,
        end_seek_sec: item.end_seek_sec,
        video_no: item.video_no,
        source_video_no: item.source_video_no,
        vod_label: item.vod_label,
        timecode: item.timecode,
      }));
      const nearbyRows = (context.events || [])
        .filter((row) => row && row.event_id !== selectedRow.event_id)
        .map((row) => ({
          label: `${row.vod_label ? `${row.vod_label} · ` : ''}${laneOptionLabel(row.lane)}`,
          text: isTimeOnlyTitle(row.label || row.title, row.start_sec) || selectedTitle(row, row.start_sec) === selectedTitleText ? '' : selectedTitle(row, row.start_sec),
          start_sec: row.start_sec,
          seek_sec: row.seek_sec,
          end_sec: row.end_sec,
          end_seek_sec: row.end_seek_sec,
          video_no: row.video_no,
          source_video_no: row.source_video_no,
          vod_label: row.vod_label,
          timecode: row.timecode,
        }))
        .filter((row) => row.text)
        .slice(0, 8);
      const evidenceRows = ownEvidenceRows.concat(nearbyRows).slice(0, 10);
      const evidenceTargets = evidenceTargetsForEvent(selectedRow, context.center_sec);
      const rangeActionLabel = adaptiveRangeActionLabel(selectedRow, selectionKind);
      const rangeGuidance = ['context_range', 'story_window'].includes(selectionKind)
        ? adaptiveRangeGuidance(selectedRow)
        : '';
      const rangeGuidanceHtml = rangeGuidance
        ? `<div class="meta-note" data-adaptive-range-guidance>${escapeHtml(rangeGuidance)}</div>`
        : '';
      const rangeActionHtml = ['context_range', 'story_window', 'moment_seed'].includes(selectionKind)
        ? `<div class="viewer-editor-selected-action"><button type="button" data-selected-range-start="${selectedSec}">${rangeActionLabel}</button></div>`
        : '';
      const draftApplyLabel = selectionKind === 'moment_seed'
        ? '사건 시점을 기존 30초 초안에 적용'
        : selectionKind === 'story_window'
          ? 'Story Window 시작을 기존 30초 초안에 적용'
          : '장 시작을 기존 30초 초안에 적용';
      const draftApplyHtml = ['context_range', 'story_window', 'moment_seed'].includes(selectionKind)
        ? `<div class="viewer-editor-selected-action"><button type="button" data-selected-draft-apply="${selectedSec}">${draftApplyLabel}</button></div>`
        : '';
      target.className = 'admin-viewer-evidence';
      target.innerHTML = `
        <div class="viewer-editor-selected-card" style="--viewer-marker-color:${escapeHtml(laneMarkerColor(kind))}">
          <span class="viewer-editor-selected-kicker">${escapeHtml(selectedEvidenceKicker(selectedRow))}</span>
          <div class="viewer-editor-selected-title">${escapeHtml(selectedTitleText)}</div>
          <div class="viewer-editor-selected-meta">${chipRows.join('')}</div>
          ${rangeGuidanceHtml}
          ${rangeActionHtml}
          ${draftApplyHtml}
        </div>
        ${renderEvidenceTargetPicker(evidenceTargets, context.center_sec)}
        ${renderContextSummary(context)}
        ${seekUrl(selectedRow, context.center_sec) ? `<div class="viewer-editor-row"><strong>원본 확인</strong><br><a class="viewer-editor-evidence-link" href="${escapeHtml(seekUrl(selectedRow, context.center_sec))}" target="_blank" rel="noopener">치지직에서 이 시각 열기</a></div>` : ''}
        <div class="viewer-editor-list">${evidenceRows.map((row) => renderEvidenceRow(row, selectedRow)).join('') || '<div class="viewer-editor-empty">근처 근거가 없습니다.</div>'}</div>
      `;
      target.querySelectorAll('[data-evidence-target-index]').forEach((button) => {
        button.addEventListener('click', () => {
          const index = Number(button.getAttribute('data-evidence-target-index'));
          const evidenceTarget = evidenceTargets[index];
          if (!evidenceTarget || !evidenceTarget.row) return;
          const nextSec = Number(evidenceTarget.row.start_sec);
          renderEvidenceContext(evidenceContextForTime(nextSec, evidenceTarget.row));
        });
      });
      const rangeStartButton = target.querySelector('[data-selected-range-start]');
      if (rangeStartButton) {
        rangeStartButton.addEventListener('click', () => {
          const nextSec = Number(rangeStartButton.getAttribute('data-selected-range-start'));
          if (!Number.isFinite(nextSec)) return;
          setSplitView(true, { silent: true });
          splitPlayerSeek(nextSec);
          setStatus(`${displayTime(selectedRow, nextSec)}로 이동했습니다. 저장 초안은 기존 30초 범위로 유지됩니다.`, 'ok');
        });
      }
      const draftApplyButton = target.querySelector('[data-selected-draft-apply]');
      if (draftApplyButton) {
        draftApplyButton.addEventListener('click', () => {
          const nextSec = Number(draftApplyButton.getAttribute('data-selected-draft-apply'));
          if (!Number.isFinite(nextSec)) return;
          selectCockpitTime(nextSec, selectedRow.event_id || '', {
            displayEvent: selectedRow,
            selectedMarkerId: selectedRow.event_id || '',
            preserveSemanticSelection: true,
            preserveEvidencePanel: true,
            updateLegacyDraft: true,
            legacyDraftDurationSec: 30,
          });
          setStatus(`${displayTime(selectedRow, nextSec)}을 30초 저장 폼에 적용했습니다. 저장 버튼을 누르기 전에는 저장된 범위나 공개 결과가 바뀌지 않습니다.`, 'ok');
        });
      }
    }

    function selectCockpitTime(sec, eventId = '', options = {}) {
      if (!Number.isFinite(sec)) return;
      const event = (signalTimelineState.events || []).find((item) => item.event_id === eventId) || {};
      const displayEvent = options.displayEvent || event;
      const effectiveSec = event && Number.isFinite(Number(event.start_sec)) ? Number(event.start_sec) : sec;
      const selectionKind = timelineSelectionKind(displayEvent);
      if (!options.preserveEvidencePanel) {
        selectedCockpitSec = effectiveSec;
        selectedCockpitEvent = event;
        if (selectionKind === 'moment_seed') {
          selectedExactMomentEvent = displayEvent;
          selectedEvidenceKind = 'moment_seed';
        } else if (!options.preserveSemanticSelection) {
          selectedEvidenceKind = 'exact_time';
        }
        if (cockpitTimecodeEl) cockpitTimecodeEl.value = displayTime(displayEvent, effectiveSec);
        renderEvidenceContext(evidenceContextForTime(effectiveSec, displayEvent));
      }
      const startInput = document.getElementById('signalDraftStart');
      const endInput = document.getElementById('signalDraftEnd');
      const eventInput = document.getElementById('signalDraftEventId');
      const segInput = document.getElementById('signalDraftSegmentId');
      const updateLegacyDraft = options.updateLegacyDraft === true;
      if (updateLegacyDraft) {
        if (startInput) startInput.value = fmtSeconds(effectiveSec);
        if (endInput) {
          const fallbackEnd = Number.isFinite(Number(options.legacyDraftDurationSec))
            ? effectiveSec + Number(options.legacyDraftDurationSec)
            : event.end_sec != null
              ? Number(event.end_sec)
              : effectiveSec + 30;
          endInput.value = fmtSeconds(fallbackEnd);
        }
        if (eventInput) eventInput.value = eventId || '';
        if (segInput) segInput.value = event.segment_id || '';
        setDraftFormMode('');
      }
      if (!options.preserveEvidencePanel) {
        const selectedMarkerId = String(options.selectedMarkerId || eventId || '');
        document.querySelectorAll('.axis-marker').forEach((el) => {
          el.classList.toggle('selected', !!selectedMarkerId && el.dataset.eventId === selectedMarkerId);
        });
        openCockpitSection('selectedEvidenceSection');
      }
      updateCockpitSectionStates();
    }

    function selectSemanticRange(event) {
      if (!event) return;
      const selectionKind = timelineSelectionKind(event);
      if (selectionKind === 'story_window') {
        selectedStoryWindowEvent = event;
      } else {
        selectedContextRangeEvent = event;
      }
      selectedEvidenceKind = selectionKind;
      const sec = Number(event.start_sec);
      const displaySec = Number.isFinite(sec) ? sec : 0;
      renderEvidenceContext(evidenceContextForTime(displaySec, event));
      document.querySelectorAll('.axis-marker').forEach((el) => {
        el.classList.toggle('selected', el.dataset.eventId === String(event.event_id || ''));
      });
      openCockpitSection('selectedEvidenceSection');
      updateCockpitSectionStates();
    }

    function renderSignalEvidence(event) {
      if (!event) return;
      const selectionKind = timelineSelectionKind(event);
      if (selectionKind === 'context_range' || selectionKind === 'story_window') {
        selectSemanticRange(event);
        return;
      }
      const sec = Number(event.start_sec);
      selectCockpitTime(Number.isFinite(sec) ? sec : 0, event.event_id || '', {
        updateLegacyDraft: selectionKind === 'moment_seed' ? false : undefined,
      });
    }

    function renderDraftRanges(draft) {
      const target = document.getElementById('signalDraftRanges');
      if (!target) return;
      const ranges = Array.isArray(draft && draft.selected_ranges) ? draft.selected_ranges : [];
      if (!ranges.length) {
        target.innerHTML = '<div class="meta-note">아직 표시한 장면이 없습니다. 장면 찾기에서 시각을 고른 뒤 중요 장면/제외/추가 검토 같은 판단을 저장하면 여기에 남습니다.</div>';
        return;
      }
      const rows = ranges.slice(0, 12).map((item) => `
        <div class="tool-row">
          <strong>${escapeHtml(item.action_label || actionLabel(item.action))}</strong><br>
          ${fmtSeconds(item.start_sec)}–${fmtSeconds(item.end_sec)}
          ${item.note ? `<br>${escapeHtml(item.note)}` : ''}
          <div class="tool-actions">
            <button type="button" class="ghost" data-edit-range-id="${escapeHtml(item.range_id || '')}">수정</button>
            <button type="button" class="ghost" data-delete-range-id="${escapeHtml(item.range_id || '')}">삭제</button>
          </div>
        </div>
      `).join('');
      const hidden = ranges.length > 12 ? `<div class="meta-note">외 ${ranges.length - 12}개 선택 범위가 저장되어 있습니다.</div>` : '';
      const lead = '<div class="meta-note">여기에 저장해도 요약 본문은 바로 바뀌지 않습니다. 나중에 본문을 고칠 때 다시 보는 체크리스트입니다.</div>';
      target.innerHTML = lead + rows + hidden;
      target.querySelectorAll('[data-edit-range-id]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const item = ranges.find((row) => row.range_id === btn.dataset.editRangeId);
          if (!item) return;
          const startInput = document.getElementById('signalDraftStart');
          const endInput = document.getElementById('signalDraftEnd');
          const actionInput = document.getElementById('signalDraftAction');
          const noteInput = document.getElementById('signalDraftNote');
          const eventInput = document.getElementById('signalDraftEventId');
          const segInput = document.getElementById('signalDraftSegmentId');
          if (startInput) startInput.value = fmtSeconds(item.start_sec);
          if (endInput) endInput.value = fmtSeconds(item.end_sec);
          if (actionInput) actionInput.value = item.action || 'note';
          if (noteInput) noteInput.value = item.note || '';
          if (eventInput) eventInput.value = item.source_event_id || '';
          if (segInput) segInput.value = item.segment_id || '';
          setDraftFormMode(item.range_id || '');
        });
      });
      target.querySelectorAll('[data-delete-range-id]').forEach((btn) => {
        btn.addEventListener('click', () => saveSignalDraft({ deleteRangeId: btn.dataset.deleteRangeId || '' }));
      });
    }

    async function saveSignalDraft(options = {}) {
      const startInput = document.getElementById('signalDraftStart');
      const endInput = document.getElementById('signalDraftEnd');
      const actionInput = document.getElementById('signalDraftAction');
      const noteInput = document.getElementById('signalDraftNote');
      const eventInput = document.getElementById('signalDraftEventId');
      const segInput = document.getElementById('signalDraftSegmentId');
      const current = signalTimelineState.selected_ranges_draft || { selected_ranges: [] };
      let selected = Array.isArray(current.selected_ranges) ? current.selected_ranges.slice() : [];
      if (options.deleteRangeId) {
        selected = selected.filter((item) => item.range_id !== options.deleteRangeId);
      } else {
        const start = parseTimeInput(startInput && startInput.value);
        const end = parseTimeInput(endInput && endInput.value);
        if (!Number.isFinite(start) || !Number.isFinite(end)) {
          setStatus('시작/끝 시각을 1:23 또는 83처럼 입력해 주세요.', 'err');
          return;
        }
        const nextItem = {
          range_id: signalDraftEditingRangeId || '',
          start_sec: start,
          end_sec: end,
          action: actionInput ? actionInput.value : 'note',
          note: noteInput ? noteInput.value : '',
          source_event_id: eventInput ? eventInput.value : '',
          segment_id: segInput ? segInput.value : '',
        };
        const nextKey = rangeKey(nextItem);
        const duplicate = selected.some((item) => item.range_id !== signalDraftEditingRangeId && rangeKey(item) === nextKey);
        if (duplicate) {
          setStatus('이미 같은 선택 범위가 저장되어 있습니다.', 'warn');
          return;
        }
        if (signalDraftEditingRangeId) {
          selected = selected.map((item) => item.range_id === signalDraftEditingRangeId ? nextItem : item);
        } else {
          selected.push(nextItem);
        }
      }
      try {
        const res = await fetch('/api/signal-timeline-draft', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ base, selected_ranges: selected }),
        });
        const data = await res.json();
        if (!res.ok || data.ok === false) throw new Error(data.error || 'save failed');
        signalTimelineState.selected_ranges_draft = data.selected_ranges_draft || { selected_ranges: [] };
        renderDraftRanges(signalTimelineState.selected_ranges_draft);
        clearDraftForm();
        openCockpitSection('savedDecisionsSection');
        updateCockpitSectionStates();
        const removed = Number(signalTimelineState.selected_ranges_draft.summary && signalTimelineState.selected_ranges_draft.summary.duplicate_removed_count || 0);
        setStatus(options.deleteRangeId ? '선택 범위를 삭제했습니다.' : (removed ? '중복을 정리하고 선택 범위를 저장했습니다.' : '선택 범위를 저장했습니다.'), 'ok');
      } catch (err) {
        setStatus('선택 범위 저장 실패: ' + err.message, 'err');
      }
    }
