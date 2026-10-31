(() => {
  'use strict';

  const S = window.ReportWorkspaceState;
  const params = new URLSearchParams(location.search);
  const base = String(params.get('base') || '').trim();
  const el = id => document.getElementById(id);
  let state = null;
  let player = null;
  let timePreviewFrame = 0;
  let pendingPreviewSeek = null;
  let boundaryPlayback = null, comparedRevision = null, compareOpener = null, saveOutcome = null;
  let savePending=false,composing=false,previewRequest=0,previewPhase='idle',previewDraftFingerprint='',previewOpener=null;
  const history = new window.ReportWorkspaceHistory(20);
  const timeline = new window.ReportWorkspaceTimeline(el('workspaceTimeline'), {
    getModel: () => state,
    onSelect: (type, id, spanIndex, seek) => select(type, id, spanIndex, seek),
    onSeek: seconds => seekScene(seconds),
    onChange: (type, id, spanIndex, start, end, label) => changeTime(type, id, spanIndex, start, end, label),
  });

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  }

  async function api(url, options = {}) {
    let response;
    try {
      response = await fetch(url, {headers: {'Content-Type': 'application/json'}, ...options});
    } catch (cause) {
      const error=new Error('서버에 연결하지 못했습니다. 입력은 화면에 남아 있습니다. 연결을 복구한 뒤 최신 저장본을 확인해 주세요.', {cause});
      error.outcomeUnknown=true;throw error;
    }
    let data;
    try { data=await response.json(); } catch(cause) {
      const error=new Error('서버 응답을 끝까지 확인하지 못했습니다. 입력은 화면에 남아 있습니다.',{cause});
      error.outcomeUnknown=true;throw error;
    }
    if (!response.ok) {
      const error = new Error(data.error || `요청 실패 (${response.status})`);
      error.code = data.code || '';
      error.status=response.status;
      error.outcomeUnknown=true;
      error.prewriteConflict=response.status===409 && data.error_code==='manager_edit_conflict'
        && data.mayHaveRun===false && typeof data.error==='string' && !!data.error.trim();
      error.definiteRefusal=response.status===400 && data.error_code==='manager_edit_validation_rejected'
        && data.mayHaveRun===false && typeof data.error==='string' && !!data.error.trim();
      if(error.definiteRefusal)error.message='저장되지 않았습니다. 입력을 고친 뒤 다시 저장하세요.';
      throw error;
    }
    return data;
  }

  function setStatus(message, kind = '') {
    const unresolved=saveOutcome && kind!=='saving' && kind!=='error';
    el('status').textContent = message + (unresolved
      ? ' 이전 요청의 결과 확인이 남아 있습니다. 저장 요청은 다시 보내지 않습니다.' : '');
    el('status').dataset.kind = unresolved ? 'error' : kind;
    if(kind==='dirty') invalidatePreview('편집 내용이 바뀌었습니다. 현재 편집안을 다시 확인하세요.');
  }

  function draftPayload() {
    const payload=S.serialize(state);
    const markdown=state.report.base.startsWith('manager-outline:')
      ? state.report.manager_markdown || state.report.md || ''
      : state.report.md || state.report.manager_markdown || '';
    return {base:state.report.base,md:markdown,
      structured_outline:payload.outline,editorial_review_packet:payload.editorial_review_packet};
  }

  function invalidatePreview(message) {
    if(!['loading','rendering','ready'].includes(previewPhase))return;
    previewRequest++;previewPhase='stale';
    el('previewFrame').hidden=true;el('previewFrame').srcdoc='';
    el('previewStatus').textContent=message;el('retryPreviewButton').hidden=false;
  }

  function updateSelectionLinks() {
    if(!state)return;
    const selected=S.selectionParams(state.selection);
    const link=(path,history=false)=>{
      const query=new URLSearchParams({base:state.report.base});
      for(const [key,value] of selected)query.set(key,value);
      if(history)query.set('history','1');return `${path}?${query}`;
    };
    el('legacyLink').href=link('/report');el('advancedLink').href=link('/report');
    el('revisionLink').href=link('/report',true);
    document.querySelectorAll('#reportVersions a').forEach(anchor=>{
      const target=new URL(anchor.href,location.href);
      if(target.origin!==location.origin||!['/report','/report-workspace'].includes(target.pathname))return;
      for(const key of ['type','id','span'])target.searchParams.delete(key);
      for(const [key,value] of selected)target.searchParams.set(key,value);
      anchor.href=target.pathname+target.search;
    });
    document.querySelectorAll('a[href="/reports"], a[href^="/reports?"]').forEach(anchor=>{anchor.href=link('/reports');});
    const query=new URLSearchParams(location.search);
    for(const key of ['type','id','span'])query.delete(key);
    for(const [key,value] of selected)query.set(key,value);
    window.history?.replaceState(null,'',`${location.pathname}?${query}${location.hash || ''}`);
  }

  function setEditorBusy(busy) {
    el('editorForm').inert=busy;el('workspaceTimeline').inert=busy;
    el('editorForm').setAttribute('aria-busy',String(busy));
  }

  async function previewCurrent() {
    if(!state||savePending)return;
    if(!el('editorForm').reportValidity()){setStatus('시간 형식을 확인한 뒤 미리보기를 다시 여세요. 입력은 유지합니다.','error');return;}
    const model=state,payload=draftPayload(),fingerprint=JSON.stringify(payload),revision=model.report.manager_edit_revision;
    const request=++previewRequest;
    const current=()=>request===previewRequest&&state===model&&JSON.stringify(draftPayload())===fingerprint;
    previewDraftFingerprint=fingerprint;previewPhase='loading';previewOpener=document.activeElement || el('previewButton');
    el('previewFrame').hidden=true;el('previewFrame').removeAttribute('src');el('previewFrame').srcdoc='';
    el('previewStatus').textContent='현재 편집안을 검증하고 미리보기를 만드는 중입니다. 저장·공개하지 않습니다.';
    el('retryPreviewButton').hidden=true;
    if(!el('previewDialog').open)el('previewDialog').showModal();
    try {
      const checkRevision=async()=>{
        const fresh=await api(`/api/report?base=${encodeURIComponent(payload.base)}&view=revision`);
        if(!current())return false;
        if(!revision||fresh.base!==payload.base||fresh.manager_edit_revision!==revision){
          const error=new Error('다른 저장본이 생겨 미리보기를 중단했습니다. 입력을 보존했으니 상세의 수정 이력에서 먼저 확인하세요.');
          error.code='manager_edit_conflict';throw error;
        }
        return true;
      };
      if(!await checkRevision())return;
      const result=await api('/api/preview',{method:'POST',body:JSON.stringify(payload)});
      if(!current())return;
      if(typeof result.html!=='string'||!result.html.trim())throw Error('미리보기 화면을 받지 못했습니다.');
      if(!await checkRevision())return;
      const marker=`<meta name="report-workspace-preview-request" content="${request}">`;
      const html=/<head(?:\s[^>]*)?>/i.test(result.html)
        ? result.html.replace(/<head(?:\s[^>]*)?>/i,match=>match+marker) : marker+result.html;
      previewPhase='rendering';el('previewFrame').srcdoc=html;
    } catch(error) {
      if(!current())return;
      previewPhase='error';el('previewFrame').hidden=true;el('previewFrame').srcdoc='';
      el('previewStatus').textContent=`${error.message || '미리보기를 만들지 못했습니다.'} 수정값과 선택은 유지합니다.`;
      el('retryPreviewButton').hidden=false;
    }
  }

  function closePreview() {
    previewRequest++;previewPhase='idle';el('previewFrame').hidden=true;el('previewFrame').srcdoc='';
    if(el('previewDialog').open)el('previewDialog').close();
    (previewOpener?.isConnected!==false?previewOpener:el('previewButton'))?.focus({preventScroll:true});
  }

  // Navigation is ready before the expensive saved report read completes.
  const detailUrl = base ? `/report?base=${encodeURIComponent(base)}` : '';
  ['legacyLink', 'advancedLink'].forEach(id => {
    if (detailUrl) {
      el(id).href = detailUrl;
      el(id).removeAttribute('aria-disabled');
    }
  });
  el('previewButton').disabled = true;
  if (detailUrl) {
    el('revisionLink').href = `${detailUrl}&history=1`;
    el('revisionLink').removeAttribute('aria-disabled');
  }

  function updateSourceLink(seconds) {
    try {
      const href = S.sourceSceneUrl(state && state.report, seconds);
      if (href === null) return false;
      el('originalLink').href = href;
      el('originalLink').removeAttribute('aria-disabled');
      el('originalLink').textContent = '선택 시각의 영상·전사 보기';
      el('playbackState').textContent = '라이브 원본 시각 ' + S.hms(seconds);
    } catch (error) {
      el('originalLink').removeAttribute('href');
      el('originalLink').setAttribute('aria-disabled', 'true');
      el('originalLink').textContent = '원본 시각 확인 필요';
      el('playbackState').textContent = error.message;
    }
    return true;
  }

  function seekScene(seconds) {
    if (updateSourceLink(seconds)) return;
    if (player && Number.isFinite(seconds)) player.seek(seconds);
  }

  function playbackReady() {
    const video=el('video');
    return !!player && !player.failed && state?.report.source_clock==null
      && video.readyState>=1 && !video.error && Number.isFinite(video.currentTime);
  }

  function updatePlaybackActions() {
    const ready=playbackReady();
    el('editorFields').querySelectorAll('[data-use-playhead], [data-boundary]').forEach(button=>{
      button.disabled=!ready;
      button.title=ready?'':state?.report.source_clock!=null
        ?'선택 시각의 영상·전사 보기에서 원본 경계를 확인하세요.'
        :'영상이 연결된 뒤 사용할 수 있습니다. 영상 다시 연결 또는 원본 보기를 이용하세요.';
    });
  }

  function timeLabel(type, item) {
    if (type === 'Point') return item.timestamp || '시각 없음';
    if (type === 'Highlight') {
      const spans = item.source_spans || [];
      if (!spans.length) return '구간 없음';
      return spans.map(span => `${S.hms(span.start_sec)}–${S.hms(span.end_sec)}`).join(' · ');
    }
    return `${item.start || '—'}–${item.end || '—'}`;
  }

  function select(type, id, spanIndex = 0, seek = true) {
    stopBoundary();
    state.activeType = type;
    state.selection = {type, id: String(id), spanIndex};
    updateSelectionLinks();
    renderTabs();
    renderList();
    renderEditor();
    timeline.render();
    const item = S.selected(state);
    if (!item) return;
    el('selectedTitle').textContent = item.title || type;
    el('selectedTime').textContent = `${type} · ${timeLabel(type, item)}`;
    updateSelectedSummary();
    if (seek) seekScene(S.itemStart(type, item, spanIndex));
  }

  function renderTabs() {
    document.querySelectorAll('#typeTabs [data-type]').forEach(button => {
      button.disabled = false;
      const active = button.dataset.type === state.activeType;
      button.setAttribute('aria-selected', active ? 'true' : 'false');
      button.tabIndex=active ? 0 : -1;
    });
  }

  function renderList() {
    const rows = S.rows(state);
    el('sceneList').setAttribute('role','tabpanel');
    el('sceneList').setAttribute('aria-labelledby',`type-tab-${state.activeType}`);
    el('sceneCount').textContent = String(rows.length);
    el('sceneList').innerHTML = rows.map(item => {
      const active = state.selection && state.selection.type === state.activeType && state.selection.id === String(item.id || '');
      const spans = state.activeType === 'Highlight' ? (item.source_spans || []) : [];
      const spanButtons = spans.length > 1 ? `<div class="span-buttons" aria-label="Highlight 구성 구간">${spans.map((span, index) => `<button type="button" data-span="${index}">${index + 1}. ${S.hms(span.start_sec)}–${S.hms(span.end_sec)}</button>`).join('')}</div>` : '';
      return `<article class="scene-card${active ? ' active' : ''}" data-id="${escapeHtml(item.id || '')}" tabindex="0">
        <div class="scene-card-top"><span>${escapeHtml(state.activeType)}</span><time>${escapeHtml(timeLabel(state.activeType, item))}</time></div>
        <strong>${escapeHtml(item.title || '제목 없음')}</strong>
        <p>${escapeHtml(item.content || item.story_summary || item.story_why_notable || '')}</p>${spanButtons}
      </article>`;
    }).join('') || '<div class="empty-list">이 유형의 항목이 없습니다.</div>';
    el('sceneList').querySelectorAll('.scene-card').forEach(card => {
      const activate = event => {
        if(event.isComposing||event.keyCode===229||composing)return;
        if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
        if (event.type === 'keydown') event.preventDefault();
        const span = event.target.closest('[data-span]');
        select(state.activeType, card.dataset.id, span ? Number(span.dataset.span) : 0);
      };
      card.addEventListener('click', activate);
      card.addEventListener('keydown', activate);
    });
  }

  function field(label, name, value, type = 'text', help = '') {
    const id=`workspace-field-${name.replace(/[^a-z0-9_-]/gi,'-')}`;
    const attributes=`id="${id}" name="${escapeHtml(name)}" aria-labelledby="${id}-label"`;
    const control=type==='textarea' ? `<textarea ${attributes} rows="5">${escapeHtml(value ?? '')}</textarea>`
      : `<input ${attributes} type="${type}" value="${escapeHtml(value ?? '')}" ${type === 'text' && name !== 'title' ? 'pattern="[0-9]{2,3}:[0-5][0-9]:[0-5][0-9]"' : ''}>`;
    return `<label for="${id}"><span id="${id}-label">${escapeHtml(label)}</span>${control}${help ? `<small>${escapeHtml(help)}</small>` : ''}</label>`;
  }

  function timeField(label, name, value) {
    const inputId = `time-${name.replace(/[^a-z0-9_-]/gi, '-')}`;
    return `<div class="time-field" data-time-field data-direct-input data-name="${escapeHtml(name)}" data-label="${escapeHtml(label)}">
      <div class="time-field-heading"><label for="${inputId}">${escapeHtml(label)}</label><small>시:분:초</small></div>
      <input id="${inputId}" name="${escapeHtml(name)}" type="text" value="${escapeHtml(value || '')}" pattern="[0-9]{2,3}:[0-5][0-9]:[0-5][0-9]" inputmode="numeric" data-time-input>
      <button class="time-scrub-display" type="button" data-time-display-button aria-controls="${inputId}" aria-keyshortcuts="ArrowLeft ArrowRight Shift+ArrowLeft Shift+ArrowRight">
        <span class="time-scrub-grip" aria-hidden="true"></span>
        <span class="time-scrub-content"><strong class="time-scrub-value" data-time-value>${escapeHtml(value || '')}</strong><small>잡고 좌우로 끌기</small></span>
        <span class="time-scrub-icon" aria-hidden="true">↔</span><span class="time-scrub-delta" data-time-delta hidden></span>
      </button>
      <div class="time-field-actions"><button type="button" data-use-playhead="${escapeHtml(name)}">현재 재생 위치로</button></div>
    </div>`;
  }

  function timeEditGuide() {
    return `<p class="time-edit-guide"><span><strong>시:분:초로 직접 입력</strong><small>잡고 좌우로 끌어도 조정됩니다 · Shift: 10초씩</small></span></p>`;
  }

  function renderEditor() {
    const item = S.selected(state);
    el('editorEmpty').hidden = !!item;
    el('editorFields').hidden = !item;
    el('typeBadge').textContent = item ? state.selection.type : '—';
    el('deleteItemButton').hidden = !item || state.selection.type === 'Highlight';
    if (!item) return;
    const type = state.selection.type;
    let html = field('제목', 'title', item.title);
    if (type === 'D1' || type === 'D2') {
      html += `${timeEditGuide()}<div class="time-grid">${timeField('시작', 'start', item.start)}${timeField('끝', 'end', item.end)}</div>`;
      html += field('장면 설명','content',item.content,'textarea');
      if (type === 'D2') html += `<p class="data-note">상위 D1 연결 <code>${escapeHtml(item.parent_id || '없음')}</code>은 그대로 유지합니다.</p>`;
    } else if (type === 'Point') {
      html += timeEditGuide() + timeField('중요 내용 시작 시각', 'timestamp', item.timestamp);
      html += '<p class="data-note">Point는 시작·끝 범위가 아니라 정확한 한 시각입니다.</p>';
      html += field('왜 중요한가','story_why_notable',item.story_why_notable,'textarea');
    } else {
      html += field('Highlight 설명','story_summary',item.story_summary,'textarea');
      html += `<fieldset><legend>구성 구간 ${item.source_spans.length}개</legend>${timeEditGuide()}${item.source_spans.map((span, index) => `<div class="span-editor" data-span-index="${index}"><button type="button" data-seek-span="${index}">▶ ${index + 1}번 보기</button>${timeField('시작', `span_${index}_start`, S.hms(span.start_sec))}${timeField('끝', `span_${index}_end`, S.hms(span.end_sec))}</div>`).join('')}</fieldset>`;
      html += '<p class="data-note">Highlight 하나는 서로 떨어진 여러 구간을 가질 수 있습니다. 각 구간을 독립적으로 유지합니다.</p>';
    }
    const boundaries=(type==='Highlight' ? item.source_spans.map((_span,index)=>index) : [0]);
    html += `<div class="boundary-actions" aria-label="컷 경계 확인">${boundaries.map(index=>`<button type="button" data-boundary="start" data-boundary-span="${index}">${type==='Highlight'?`${index+1}번 `:''}시작 주변 6초</button>${type==='Point'?'':`<button type="button" data-boundary="end" data-boundary-span="${index}">${type==='Highlight'?`${index+1}번 `:''}끝 주변 6초</button>`}`).join('')}</div>`;
    el('editorFields').innerHTML = html;
    bindEditor(item, type);
  }

  function bindEditor(item, type) {
    const fields = el('editorFields');
    fields.querySelectorAll('input:not([data-time-input]), textarea').forEach(control => {
      let beforeEdit = null;
      control.addEventListener('focus', () => { beforeEdit = S.snapshot(state); });
      control.addEventListener('input', () => {
        if(savePending)return;
        stopBoundary();
        const spanMatch = control.name.match(/^span_(\d+)_(start|end)$/);
        if (spanMatch) {
          const parsed = S.seconds(control.value);
          if (Number.isFinite(parsed)) S.updateSpan(state, Number(spanMatch[1]), {[`${spanMatch[2]}_sec`]: parsed});
        } else {
          S.update(state, {[control.name]: control.value});
        }
        el('saveButton').disabled = !!saveOutcome || savePending;
        setStatus('수정 중입니다. 아직 저장되지 않았습니다.', 'dirty');
        renderList();
        if (control.validity.valid) timeline.render();
      });
      control.addEventListener('change', () => {
        if (!beforeEdit || JSON.stringify(beforeEdit) === JSON.stringify(S.snapshot(state))) return;
        const label = control.closest('label')?.querySelector('span')?.textContent?.trim() || '내용';
        history.commit(beforeEdit, `${label} 수정`);
        beforeEdit = null;
        updateHistoryControls();
      });
    });
    fields.querySelectorAll('[data-time-field]').forEach(root => bindTimeScrubber(root, item, type));
    fields.querySelectorAll('[data-use-playhead]').forEach(button=>{
      button.disabled=!playbackReady();
      button.addEventListener('click',()=>{
        if(!playbackReady() || savePending)return;
        const target=timeFieldTarget(button.dataset.usePlayhead,type);
        const before=S.snapshot(state),result=previewTime(type,item.id,target,Number(el('video').currentTime));
        if(result.ok){commitTimeEdit(before,'현재 재생 위치로 시간 조정');renderEditor();}
      });
    });
    fields.querySelectorAll('[data-boundary]').forEach(button=>{
      button.disabled=!playbackReady();
      if(button.disabled)button.title='선택 시각의 영상·전사 보기에서 원본 경계를 확인하세요.';
      button.addEventListener('click',()=>previewBoundary(type,item.id,Number(button.dataset.boundarySpan),button.dataset.boundary));
    });
    updatePlaybackActions();
    fields.querySelectorAll('[data-seek-span]').forEach(button => button.addEventListener('click', () => {
      const index = Number(button.dataset.seekSpan);
      state.selection.spanIndex = index;
      updateSelectionLinks();
      seekScene(S.itemStart(type, item, index));
    }));
  }

  function timeFieldTarget(name, type) {
    const spanMatch = name.match(/^span_(\d+)_(start|end)$/);
    if (spanMatch) return {spanIndex: Number(spanMatch[1]), edge: spanMatch[2]};
    if (type === 'Point') return {spanIndex: 0, edge: 'point'};
    return {spanIndex: 0, edge: name === 'end' ? 'end' : 'start'};
  }

  function selectedTimeValue(type, item, target) {
    return target.edge === 'end' ? S.itemEnd(type, item, target.spanIndex) : S.itemStart(type, item, target.spanIndex);
  }

  function updateSelectedSummary() {
    const selected = S.selected(state);
    el('selectedTitle').textContent = selected ? (selected.title || state.activeType) : '장면을 선택하세요';
    el('selectedTime').textContent = selected
      ? `${state.activeType} · ${timeLabel(state.activeType, selected)}`
      : 'D1 · D2 · Point · Highlight를 같은 데이터로 탐색합니다.';
    el('taskSelection').textContent=selected ? `${selected.title || state.activeType} · ${timeLabel(state.activeType,selected)}` : '장면을 선택하세요';
    if (state.report.source_clock !== undefined && state.report.source_clock !== null)
      updateSourceLink(selected ? S.itemStart(state.selection.type, selected, state.selection.spanIndex) : NaN);
  }

  function cancelTimePreviewFrame(flushSeek = false) {
    const seek = pendingPreviewSeek;
    if (timePreviewFrame) cancelAnimationFrame(timePreviewFrame);
    timePreviewFrame = 0;
    pendingPreviewSeek = null;
    if (flushSeek && Number.isFinite(seek)) seekScene(seek);
  }

  function scheduleTimePreview(seconds) {
    pendingPreviewSeek = seconds;
    if (timePreviewFrame) return;
    timePreviewFrame = requestAnimationFrame(() => {
      timePreviewFrame = 0;
      const seek = pendingPreviewSeek;
      pendingPreviewSeek = null;
      renderList();
      timeline.render();
      updateSelectedSummary();
      if (Number.isFinite(seek)) seekScene(seek);
    });
  }

  function previewTime(type, id, target, nextValue) {
    if(savePending)return {ok:false,error:'저장 중에는 편집을 잠시 기다려 주세요.'};
    stopBoundary();
    const current = S.rows(state, type).find(row => String(row.id || '') === String(id || ''));
    if (!current) return {ok: false, error: '선택한 항목을 찾을 수 없습니다.'};
    const currentStart = S.itemStart(type, current, target.spanIndex);
    const currentEnd = S.itemEnd(type, current, target.spanIndex);
    const roundedValue = Math.round(Number(nextValue));
    const currentValue = target.edge === 'end' ? currentEnd : currentStart;
    if (roundedValue === currentValue) return {ok: true, value: currentValue};
    const start = target.edge === 'end' ? currentStart : roundedValue;
    const end = target.edge === 'start' ? currentEnd : roundedValue;
    const result = S.setTime(state, type, id, target.spanIndex, start, end);
    if (!result.ok) {
      setStatus(result.error, 'error');
      return result;
    }
    const value = target.edge === 'end' ? result.end : result.start;
    el('saveButton').disabled = !!saveOutcome || savePending;
    setStatus(`${type} ${target.edge === 'end' ? '끝' : '시작'} 시각을 조정 중입니다. 아직 저장되지 않았습니다.`, 'dirty');
    scheduleTimePreview(value);
    return {...result, value};
  }

  function commitTimeEdit(before, label) {
    cancelTimePreviewFrame(true);
    const changed = JSON.stringify(before) !== JSON.stringify(S.snapshot(state));
    if (changed) history.commit(before, label);
    renderList();timeline.render();updateSelectedSummary();updateHistoryControls();
    el('saveButton').disabled=!state.dirty || !!saveOutcome;
    if (changed) setStatus(`${label}을 반영했습니다. 저장하면 로컬 수정본에 기록됩니다.`, 'dirty');
  }

  function cancelTimeEdit(before, announce = true) {
    cancelTimePreviewFrame();
    S.restore(state, before);
    renderAll();
    if (announce) setStatus('시간 조정을 취소했습니다.', state.dirty ? 'dirty' : 'ok');
  }

  function bindTimeScrubber(root, item, type) {
    const target = timeFieldTarget(root.dataset.name, type);
    const id = String(item.id || '');
    const label = `${type} ${root.dataset.label} 시각 조정`;
    new window.ReportWorkspaceTimeScrubber(root, {
      begin: () => S.snapshot(state),
      getValue: () => selectedTimeValue(type, item, target),
      format: S.hms,
      parse: S.seconds,
      preview: value => previewTime(type, id, target, value),
      commit: before => commitTimeEdit(before, label),
      cancel: (before, announce = true) => {
        cancelTimeEdit(before, announce);
        if(announce)el('editorFields').querySelector(`[name="${root.dataset.name}"]`)?.focus({preventScroll:true});
      },
      invalid: () => setStatus('시간은 시:분:초 형식이며 현재 장면의 허용 범위 안이어야 합니다.', 'error'),
    });
  }

  function stopBoundary(message = '') {
    if (!boundaryPlayback) return;
    clearTimeout(boundaryPlayback.timer);boundaryPlayback=null;
    if(player) { player.pendingSeek=null;el('video').pause(); }
    el('stopBoundaryButton').hidden=true;
    el('boundaryStatus').textContent=message || '경계 확인을 중지했습니다. 편집값은 바뀌지 않았습니다.';
  }

  function previewBoundary(type,id,spanIndex,edge) {
    stopBoundary();
    const item=S.rows(state,type).find(row=>String(row.id || '')===String(id));
    const window=item && S.boundaryWindow(state,type,item,spanIndex,edge);
    if(!playbackReady() || !window || window.end<=window.start) {
      el('boundaryStatus').textContent='영상 연결을 확인하거나 선택 시각의 원본 영상에서 경계를 확인하세요.';return;
    }
    boundaryPlayback={...window,timer:setTimeout(()=>stopBoundary('영상 응답을 기다리다 경계 확인을 중지했습니다. 다시 확인할 수 있습니다.'),30000)};
    el('stopBoundaryButton').hidden=false;
    el('boundaryStatus').textContent=`${spanIndex+1}번 ${edge==='end'?'끝':'시작'} 경계 ${S.hms(window.at)} · ${S.hms(window.start)}–${S.hms(window.end)} 재생 · 저장하지 않습니다.`;
    player.seek(window.start);
  }

  function closeRevisionComparison() {
    comparedRevision=null;
    if(el('revisionCompareDialog').open)el('revisionCompareDialog').close();
    (compareOpener?.isConnected ? compareOpener : el('revisionLink'))?.focus({preventScroll:true});
  }

  function compareRevision(revisionId) {
    if(savePending)return;
    const revision=(state.report.manager_outline_revisions || []).find(row=>row.revision_id===revisionId);
    if(!revision)return;
    comparedRevision=revisionId;compareOpener=document.activeElement;
    const differences=S.revisionDiff(state,revision);
    el('revisionCompareSummary').textContent=`이전 저장본 ${revisionId} · 현재 ${state.dirty?'미저장 편집안':'저장본'}과 ${differences.length}곳이 다릅니다.`;
    if(!revision.qg1_review_packet && state.highlights.length)
      el('revisionCompareSummary').textContent+=' 이 저장본은 편집 구간의 세부 기록이 없어 해당 비교는 확인할 수 없습니다.';
    el('revisionCompareChanges').innerHTML=differences.map(row=>{
      const title=`${escapeHtml(row.item)} · ${escapeHtml(row.field)}`;
      const values=`<dl><div><dt>현재 편집안</dt><dd>${escapeHtml(row.current || '(비어 있음)')}</dd></div><div><dt>이전 저장본</dt><dd>${escapeHtml(row.previous || '(비어 있음)')}</dd></div></dl>`;
      return row.item==='요약 전체 본문' ? `<details class="revision-change"><summary>${title}</summary>${values}</details>`
        : `<section class="revision-change"><h3>${title}</h3>${values}</section>`;
    }).join('') || '<p>제목·본문·시간에 차이가 없습니다.</p>';
    el('restoreComparedButton').disabled=!!saveOutcome || !differences.length;
    el('revisionCompareDialog').showModal();
  }

  function renderHistory() {
    const revisions = state.report.manager_outline_revisions || [];
    el('historyCount').textContent = String(revisions.length);
    el('historyList').innerHTML = revisions.map(row => `<div><span><strong>${escapeHtml(row.revision_id || '')}</strong><small>${escapeHtml(row.created_at || row.saved_at || '')}</small></span><button type="button" data-compare="${escapeHtml(row.revision_id || '')}">현재와 비교</button></div>`).join('') || '<p>아직 이전 저장본이 없습니다.</p>';
    el('historyList').querySelectorAll('[data-compare]').forEach(button => button.addEventListener('click', () => compareRevision(button.dataset.compare)));
  }

  function adjacent(delta) {
    const rows = S.rows(state);
    if (!rows.length) return;
    const current = state.selection ? rows.findIndex(row => String(row.id || '') === state.selection.id) : -1;
    const next = rows[(current + delta + rows.length) % rows.length];
    select(state.activeType, next.id);
  }

  function updateHistoryControls() {
    el('undoButton').disabled = !history.canUndo;
    el('redoButton').disabled = !history.canRedo;
    el('undoButton').title = history.canUndo ? `${history.undoLabel} 되돌리기 (Ctrl+Z)` : '되돌릴 작업이 없습니다.';
    el('redoButton').title = history.canRedo ? `${history.redoLabel} 다시 실행 (Ctrl+Y)` : '다시 실행할 작업이 없습니다.';
  }

  function changeTime(type, id, spanIndex, start, end, label) {
    if(savePending)return {ok:false,error:'저장 중에는 편집을 잠시 기다려 주세요.'};
    const before = S.snapshot(state);
    const result = S.setTime(state, type, id, spanIndex, start, end);
    if (!result.ok) {
      setStatus(result.error, 'error');
      return result;
    }
    history.commit(before, label || `${type} 시간 조정`);
    renderAll();
    setStatus(`${type} 시간을 조정했습니다. 저장하면 로컬 수정본에 반영됩니다.`, 'dirty');
    return result;
  }

  function addItem(type) {
    if(!state||savePending)return;
    const before = S.snapshot(state);
    const selected = S.selected(state);
    const currentTime = player ? Number(el('video').currentTime) || 0
      : selected ? S.itemStart(state.selection.type, selected, state.selection.spanIndex) : 0;
    const result = S.addItem(state, type, currentTime);
    if (!result.ok) {
      setStatus(result.error, 'error');
      return;
    }
    history.commit(before, `${type} 추가`);
    el('addItemMenu').open = false;
    renderAll();
    setStatus(`${type}을(를) 현재 재생 위치에 추가했습니다. 내용을 확인한 뒤 저장해 주세요.`, 'dirty');
    el('editorFields').querySelector('[name="title"]')?.focus();
  }

  function deleteItem() {
    if(!state||savePending)return;
    const item = S.selected(state);
    if (!item) return;
    const label = state.selection.type;
    const before = S.snapshot(state);
    const result = S.deleteSelected(state);
    if (!result.ok) {
      setStatus(result.error, 'error');
      return;
    }
    history.commit(before, `${label} 제거`);
    renderAll();
    setStatus(`${label}을(를) 제거했습니다. 저장하기 전까지 공개 결과에는 영향이 없습니다.`, 'dirty');
  }

  function undo() {
    if(savePending)return;
    const entry = history.undo(S.snapshot(state));
    if (!entry) return;
    S.restore(state, entry.snapshot);
    renderAll();
    setStatus(`${entry.label} 작업을 되돌렸습니다.`, state.dirty ? 'dirty' : 'ok');
  }

  function redo() {
    if(savePending)return;
    const entry = history.redo(S.snapshot(state));
    if (!entry) return;
    S.restore(state, entry.snapshot);
    renderAll();
    setStatus(`${entry.label} 작업을 다시 실행했습니다.`, state.dirty ? 'dirty' : 'ok');
  }

  function acceptSavedReport(data, selection) {
    state=S.create(data);S.restoreSelection(state,S.selectionParams(selection));history.clear();renderAll();
  }

  function editableSignature(model) {
    // Legacy outline serialization trims only the outside of editable text.
    return JSON.stringify(JSON.parse(S.editSignature(model)).map(row=>({...row,
      title:row.title.trim(),body:row.body.trim(),why:row.why.trim()})));
  }

  function checkedReportResult(data, attempt, mutation=false) {
    const object=value=>!!value && typeof value==='object' && !Array.isArray(value);
    const time=value=>(typeof value==='number' && value>=0 || typeof value==='string' && !!value.trim()) && Number.isFinite(S.seconds(value));
    const outline=data?.structured_outline,packet=data?.qg1_review_packet;
    if(!object(data) || data.base!==attempt.base || String(data.video_no)!==attempt.videoNo
      || typeof data.manager_edit_revision!=='string' || !data.manager_edit_revision.trim()
      || mutation && attempt.changed && data.manager_edit_revision===attempt.revision
      || typeof data.md!=='string' || typeof data.manager_markdown!=='string'
      || !object(outline) || !Array.isArray(outline.ranges) || !Array.isArray(outline.points)
      || outline.ranges.some(row=>!object(row) || typeof row.id!=='string' || !row.id || typeof row.title!=='string'
        || !['D1','D2'].includes(row.level) || !time(row.start) || !time(row.end))
      || outline.points.some(row=>!object(row) || typeof row.id!=='string' || !row.id || typeof row.title!=='string' || !time(row.timestamp))
      || attempt.model.packet && !object(packet)) throw Error('같은 방송·버전의 전체 편집 결과를 확인하지 못했습니다.');
    if(packet!=null && (!object(packet) || !Array.isArray(packet.stories) || !Array.isArray(packet.editorial_highlights)
      || packet.stories.some(row=>!object(row) || typeof row.story_ref!=='string' || row.why_notable!==undefined && typeof row.why_notable!=='string')
      || packet.editorial_highlights.some(row=>!object(row) || typeof row.highlight_id!=='string' || !row.highlight_id
        || typeof row.title!=='string' || !Array.isArray(row.source_spans)
        || row.source_spans.some(span=>!object(span) || !Number.isFinite(span.start_sec) || !Number.isFinite(span.end_sec)))))
      throw Error('편집 구간과 근거가 포함된 전체 결과를 확인하지 못했습니다.');
    if(String(attempt.base).startsWith('manager-outline:')) {
      if(!Array.isArray(outline.candidates) || outline.candidates.some(row=>!object(row)
        || typeof row.id!=='string' || !row.id || typeof row.title!=='string' || typeof row.content!=='string'
        || !time(row.start) || !time(row.end) || S.seconds(row.end)<=S.seconds(row.start)))
        throw Error('읽기 전용 검토 범위를 포함한 전체 저장본을 확인하지 못했습니다.');
      if(mutation && Array.isArray(attempt.candidates)) {
        // These rows are carried through serialization without workspace edits.
        // A restore uses its selected history, while a GET may show newer rows.
        const key=rows=>JSON.stringify(rows.map(row=>JSON.stringify([row.id,S.seconds(row.start),S.seconds(row.end),
          row.title.trim(),row.content.trim()])).sort());
        if(key(outline.candidates)!==key(attempt.candidates))
          throw Error('응답의 읽기 전용 검토 범위가 요청한 저장·복원 대상과 일치하지 않습니다.');
      }
    }
    const model=S.create(data);
    if(mutation) {
      const authority=data.manager_outline_authority || {};
      const selected=authority.saved_revision?.revision_id || authority.revision_id;
      if(typeof data.revision_id!=='string' || !data.revision_id || selected && selected!==data.revision_id)
        throw Error('기록된 수정본의 식별 정보를 확인하지 못했습니다.');
    }
    if(mutation && (attempt.kind==='restore' && data.restored_from_revision_id!==attempt.targetId
      || attempt.signature && editableSignature(model)!==attempt.signature)) throw Error('응답의 편집 내용이 요청한 저장·복원 대상과 일치하지 않습니다.');
    return model;
  }

  function updateSaveOutcomeControls() {
    el('saveButton').disabled=savePending || !!saveOutcome || !state?.dirty;
    el('checkSaveButton').hidden=!saveOutcome;el('checkSaveButton').disabled=savePending;
    el('checkSaveButton').textContent=saveOutcome?.kind==='restore'?'복원 결과 다시 확인':'저장 결과 다시 확인';
    el('adoptCurrentSaveButton').hidden=!saveOutcome?.observed;
    el('adoptCurrentSaveButton').disabled=savePending;
    el('previewButton').disabled=savePending;
  }

  function confirmedShrink(data) {
    const change=data?.destructive_change;
    return data?.error_code==='report_save_confirmation_required' && data.mayHaveRun===false
      && data.requires_destructive_confirmation===true && typeof data.confirmation_token==='string' && !!data.confirmation_token
      && change && ['before_chars','after_chars','removed_chars','remaining_percent'].every(key=>Number.isFinite(change[key]) && change[key]>=0);
  }

  async function confirmWorkspaceChange(title,description,acceptLabel) {
    const dialog=el('workspaceConfirmDialog');
    if(dialog.open)return false;
    const opener=document.activeElement;
    el('workspaceConfirmTitle').textContent=title;
    el('workspaceConfirmDescription').textContent=description;
    el('workspaceConfirmAccept').textContent=acceptLabel;
    dialog.returnValue='';
    return new Promise((resolve,reject)=>{
      const closed=()=>{
        if(opener?.isConnected)opener.focus({preventScroll:true});
        resolve(dialog.returnValue==='confirm');
      };
      dialog.addEventListener('close',closed,{once:true});
      try { dialog.showModal();el('workspaceConfirmCancel').focus({preventScroll:true}); }
      catch(error) { dialog.removeEventListener('close',closed);reject(Object.assign(error,{definiteRefusal:true})); }
    });
  }

  async function requestReportWrite(path,payload) {
    let data=await api(path,{method:'POST',body:JSON.stringify(payload)});
    if(!confirmedShrink(data))return data;
    const change=data.destructive_change;
    if(!await confirmWorkspaceChange('본문이 크게 줄어듭니다',`대상: 이 방송의 로컬 수정본\n현재 ${change.before_chars.toLocaleString()}자 → 처리 후 ${change.after_chars.toLocaleString()}자\n${change.removed_chars.toLocaleString()}자 삭제 · ${change.remaining_percent}% 남음\n\n편집 유지를 선택하면 입력과 저장본은 그대로입니다. 확인한 변경만 로컬 수정본에 기록하며 공개 사이트는 바뀌지 않습니다.`,path.endsWith('/restore')?'축소한 내용으로 복원':'축소한 수정본 저장'))
      throw Object.assign(Error('본문 축소 확인을 취소했습니다. 입력과 저장본은 그대로입니다.'),{definiteRefusal:true});
    data=await api(path,{method:'POST',body:JSON.stringify({...payload,destructive_confirmation_token:data.confirmation_token})});
    if(confirmedShrink(data))throw Object.assign(Error('확인 조건이 바뀌어 기록하지 않았습니다. 입력을 유지했습니다. 다시 저장·복원을 선택해 변경량을 확인하세요.'),{definiteRefusal:true});
    return data;
  }

  async function checkSaveOutcome() {
    if(!saveOutcome || savePending)return;
    const attempt=saveOutcome;
    attempt.observed=null;savePending=true;setEditorBusy(true);updateSaveOutcomeControls();
    setStatus('현재 저장본을 조회하고 있습니다. 저장·복원 요청은 다시 보내지 않습니다.','saving');
    try {
      const fresh=await api(`/api/report?base=${encodeURIComponent(attempt.base)}&view=editor`);
      if(saveOutcome!==attempt)return;
      const current=checkedReportResult(fresh,attempt);
      attempt.observed=fresh;
      const changed=fresh.manager_edit_revision!==attempt.revision;
      const matches=attempt.signature && editableSignature(current)===attempt.signature;
      const observation=!changed?'현재 저장본은 이전 revision 그대로입니다.':matches?'현재 저장본의 편집 내용이 요청한 내용과 일치합니다.':'현재 저장본이 이전 revision과 다릅니다.';
      setStatus(`${observation} ${attempt.prewriteConflict?'충돌한 요청은 기록되지 않았습니다.':'이 조회만으로 이전 요청의 성공을 확정하지 않습니다.'} 입력·선택·편집 이력과 원래 저장 기준은 유지했습니다. 확인한 저장본을 채택하려면 다시 열기를 선택하세요.`, 'error');
    } catch(error) {
      setStatus(`현재 저장본을 확인하지 못했습니다. ${error.message || ''} 입력·선택·편집 이력은 유지하며 요청을 다시 보내지 않습니다.`,'error');
    } finally {
      savePending=false;setEditorBusy(false);updateSaveOutcomeControls();
    }
  }

  async function adoptCurrentSave() {
    const attempt=saveOutcome;
    if(savePending || !attempt?.observed)return;
    const observed=attempt.observed;
    checkedReportResult(observed,attempt);
    savePending=true;setEditorBusy(true);updateSaveOutcomeControls();
    try {
      if(!await confirmWorkspaceChange('확인한 현재 저장본으로 다시 열기',`대상: 같은 방송에서 방금 조회한 현재 저장본\n\n화면의 미저장 수정과 최대 20단계 편집 되돌리기 이력을 버리고 이 저장본으로 교체합니다. 현재 장면 선택은 가능한 경우 유지합니다.\n편집 유지를 선택하면 지금 입력과 이력을 그대로 둡니다.\n\n이전 요청의 성공을 확정하는 동작은 아닙니다. 조회 이후 저장본이 바뀌면 다음 저장에서 다시 확인해야 합니다. 공개 사이트는 바뀌지 않습니다.`,'확인한 저장본으로 다시 열기'))return;
      if(saveOutcome!==attempt || attempt.observed!==observed)return;
      const selection=state.selection;
      saveOutcome=null;acceptSavedReport(observed,selection);
      setStatus('확인한 현재 저장본을 명시적으로 채택했습니다. 미저장 수정과 편집 되돌리기 이력을 비웠으며 공개 사이트는 바뀌지 않았습니다.','ok');
      el('status').focus({preventScroll:true});
    } finally {
      savePending=false;setEditorBusy(false);updateSaveOutcomeControls();
      if(saveOutcome)el('adoptCurrentSaveButton').focus({preventScroll:true});
    }
  }

  async function save() {
    if (!state || !state.dirty || savePending || saveOutcome) return;
    if (!el('editorForm').reportValidity()) {
      setStatus('시간은 시:분:초 형식으로 입력해 주세요. 예: 00:20:39', 'error');
      return;
    }
    const payload = S.serialize(state);
    const signature=editableSignature(state);
    const attempt={kind:'save',base:state.report.base,videoNo:String(state.report.video_no),revision:state.report.manager_edit_revision,
      signature,changed:signature!==editableSignature(S.create(state.report)),candidates:payload.outline.candidates,model:state};
    stopBoundary();
    savePending=true;setEditorBusy(true);
    updateSaveOutcomeControls();
    setStatus('저장 중입니다. 구조를 검증하고 로컬 수정본에 기록합니다.','saving');
    let confirmed=false,refused=false;
    try {
      const data = await requestReportWrite('/api/manager-outline/save', {
        video_no: state.report.video_no,
        base: state.report.base,
        manager_markdown: state.report.manager_markdown,
        outline: payload.outline,
        editorial_review_packet: payload.editorial_review_packet,
        expected_edit_revision: state.report.manager_edit_revision || '',
      });
      checkedReportResult(data,attempt,true);confirmed=true;
      acceptSavedReport(data,state.selection);
      setStatus('로컬 수정본을 저장했습니다. 공개 사이트는 바뀌지 않았습니다.', 'ok');
    } catch (error) {
      if(confirmed)setStatus('로컬 수정본 저장 응답은 확인했습니다. 화면 갱신을 마치지 못했습니다. 저장 요청은 다시 보내지 않습니다.','error');
      else if(error.definiteRefusal){refused=true;setStatus(error.message,'error');}
      else {saveOutcome={...attempt,prewriteConflict:error.prewriteConflict===true};setStatus(error.prewriteConflict?'다른 수정본이 먼저 저장되어 요청을 기록하지 않았습니다. 입력과 기존 기준을 유지하며 현재 저장본을 확인합니다.':'저장 결과 미확인입니다. 입력·선택·편집 이력을 보존하며 요청은 다시 보내지 않습니다.','error');}
    } finally {
      savePending=false;setEditorBusy(false);
      updateSaveOutcomeControls();el('previewButton').disabled=false;
      if(refused)el('saveButton').focus({preventScroll:true});
    }
    if(saveOutcome)await checkSaveOutcome();
  }

  async function restore(revisionId) {
    if(!state||savePending||saveOutcome)return;
    const opener=document.activeElement;
    const selection=state.selection;
    const target=(state.report.manager_outline_revisions || []).find(row=>row.revision_id===revisionId);
    const complete=Array.isArray(target?.outline?.ranges) && Array.isArray(target?.outline?.points)
      && (!state.packet || Array.isArray(target.qg1_review_packet?.stories) && Array.isArray(target.qg1_review_packet?.editorial_highlights));
    const signature=complete?editableSignature(S.create({structured_outline:target.outline,qg1_review_packet:target.qg1_review_packet})):null;
    const attempt={kind:'restore',base:state.report.base,videoNo:String(state.report.video_no),revision:state.report.manager_edit_revision,
      model:state,targetId:revisionId,signature,candidates:target?.outline?.candidates,
      changed:!signature || signature!==editableSignature(S.create(state.report))};
    savePending=true;setEditorBusy(true);el('saveButton').disabled=true;el('previewButton').disabled=true;
    updateSaveOutcomeControls();
    let confirmed=false,refused=false;
    try {
      if(!await confirmWorkspaceChange('이전 저장본으로 복원',`선택한 저장본: ${revisionId}\n\n화면의 미저장 수정과 최대 20단계 편집 되돌리기 이력을 선택한 저장본의 내용으로 교체합니다. 현재 장면 선택은 가능한 경우 유지합니다.\n복원하면 새 로컬 수정본이 기록됩니다. 공개 사이트는 바뀌지 않습니다.\n편집 유지를 선택하면 현재 입력·선택·이력과 저장본을 그대로 둡니다.`,'선택한 저장본으로 복원')) {
        refused=true;setStatus('복원을 취소했습니다. 화면의 편집 내용·선택·되돌리기 이력과 저장본은 그대로입니다.');return;
      }
      setStatus('이전 버전을 복원하는 중…','saving');
      const data = await requestReportWrite('/api/manager-outline/restore', {
        video_no: state.report.video_no, base: state.report.base, revision_id: revisionId,
        expected_edit_revision: state.report.manager_edit_revision || '',
      });
      checkedReportResult(data,attempt,true);confirmed=true;
      acceptSavedReport(data,selection);
      setStatus('이전 버전을 새 로컬 수정본으로 복원했습니다.', 'ok');
    } catch (error) {
      if(confirmed)setStatus('복원 응답은 확인했습니다. 화면 갱신을 마치지 못했습니다. 복원 요청은 다시 보내지 않습니다.','error');
      else if(error.definiteRefusal){refused=true;setStatus(error.message,'error');}
      else {saveOutcome={...attempt,prewriteConflict:error.prewriteConflict===true};setStatus(error.prewriteConflict?'다른 수정본이 먼저 저장되어 복원을 기록하지 않았습니다. 입력과 기존 기준을 유지합니다.':'복원 결과 미확인입니다. 입력·선택·편집 이력을 유지하며 복원 요청은 다시 보내지 않습니다.','error');}
    } finally {
      savePending=false;setEditorBusy(false);
      updateSaveOutcomeControls();el('previewButton').disabled=false;
      if(refused)(opener?.isConnected?opener:el('revisionLink')).focus({preventScroll:true});
    }
    if(saveOutcome)await checkSaveOutcome();
  }

  function renderAll() {
    stopBoundary();
    updateSelectionLinks();
    el('publicationState').textContent=S.publicationNotice(state.report);
    el('previewButton').disabled=savePending;
    if(['loading','rendering','ready'].includes(previewPhase)&&previewDraftFingerprint!==JSON.stringify(draftPayload()))
      invalidatePreview('편집 내용 또는 저장본이 바뀌었습니다. 현재 편집안을 다시 확인하세요.');
    const pendingNotice = S.completionNotice(state);
    el('completionNotice').textContent = pendingNotice;
    el('completionNotice').hidden = !pendingNotice;
    renderTabs();
    renderList();
    renderEditor();
    renderHistory();
    updateSelectedSummary();
    el('saveButton').disabled = savePending || !!saveOutcome || !state.dirty;
    updateSaveOutcomeControls();
    updateHistoryControls();
    document.querySelectorAll('[data-add-type="D2"]').forEach(button => {
      button.disabled = !(state.selection && state.selection.type === 'D1');
      button.title = button.disabled ? '먼저 소속 D1을 선택해 주세요.' : '';
    });
    timeline.render();
  }

  async function loadPlayback(videoNo) {
    el('reconnectButton').disabled = true;
    if (state.report.source_clock !== undefined && state.report.source_clock !== null) {
      el('video').hidden = true;
      el('video').style.display = 'none';
      el('videoEmpty').hidden = false;
      el('videoEmpty').querySelector('strong').textContent = '같은 저장 방송의 원본으로 확인합니다.';
      el('videoEmpty').querySelector('span').textContent = '장면을 선택한 뒤 영상·전사 보기 버튼을 누르세요. 제목과 구간 편집은 이 작업실에서 계속할 수 있습니다.';
      const item = S.selected(state);
      updateSourceLink(item ? S.itemStart(state.selection.type, item, state.selection.spanIndex) : NaN);
      return;
    }
    el('video').hidden = false;
    el('video').style.display = '';
    el('originalLink').textContent = 'CHZZK 원본';
    el('originalLink').removeAttribute('aria-disabled');
    player = new window.ReportPlayer(el('video'), (kind, message) => {
      el('playbackState').textContent = message;
      el('videoEmpty').hidden = kind !== 'error';
      if(kind==='error')stopBoundary(message);
      updatePlaybackActions();
    });
    try {
      const data = await api(`/api/report-playback?video_no=${encodeURIComponent(videoNo)}&base=${encodeURIComponent(base)}&quality=720`);
      el('originalLink').href = data.playback.original_url;
      await player.load(data.playback.stream_url);
      updatePlaybackActions();
      el('reconnectButton').disabled = false;
      el('pipButton').disabled = !(document.pictureInPictureEnabled && typeof el('video').requestPictureInPicture === 'function');
    } catch (error) {
      el('playbackState').textContent = error.code === 'auth_required' ? '로그인 필요' : '원본으로 확인';
      el('videoEmpty').hidden = false;
      el('originalLink').href = `https://chzzk.naver.com/video/${encodeURIComponent(videoNo)}`;
    }
  }

  async function reconnectPlayback() {
    if (!player || !state || state.report.source_clock != null) return;
    el('reconnectButton').disabled = true;
    try {
      await player.reconnect();
    } catch (error) {
      el('playbackState').textContent = '영상 연결 실패 · 원본으로 확인해 주세요.';
      el('videoEmpty').hidden = false;
    } finally {
      el('reconnectButton').disabled = false;
    }
  }

  async function load() {
    if (!base) throw new Error('base 파라미터가 없습니다. 리포트 목록에서 다시 여세요.');
    const report = await api(`/api/report?base=${encodeURIComponent(base)}&view=editor`);
    if (report.kind === 'bundle' || !report.structured_outline) throw new Error('이 화면은 방송 구조가 있는 단일 VOD 리포트에서 사용할 수 있습니다.');
    state = S.create(report);
    const legacy = `/report?base=${encodeURIComponent(report.base)}`;
    el('legacyLink').href = legacy;
    el('advancedLink').href = legacy;
    const liveClock = report.source_clock !== undefined && report.source_clock !== null;
    el('reportTitle').textContent = liveClock && report.title === `VOD ${report.video_no}`
      ? '저장 방송 편집 초안' : report.title || `VOD ${report.video_no}`;
    el('reportMeta').textContent = liveClock ? '원래 방송 시작 기준 시각 · 다시보기 위치 미확정 · 저장과 공개 분리'
      : `VOD ${report.video_no} · 현재 관리자 수정본 · 저장과 공개 분리`;
    const firstType = ['D1', 'D2', 'Point', 'Highlight'].find(type => S.rows(state, type).length);
    const first = firstType ? S.rows(state, firstType)[0] : null;
    if (!S.restoreSelection(state,params)&&first) {
      state.activeType = firstType;
      state.selection = {type: firstType, id: String(first.id || ''), spanIndex: 0};
    }
    renderAll();
    setStatus(liveClock ? '관리자 수정본을 불러왔습니다. 장면을 선택한 뒤 원본 버튼으로 영상·전사를 확인하세요.'
      : '관리자 수정본을 불러왔습니다. 장면을 누르면 영상이 해당 시각으로 이동합니다.', 'ok');
    loadPlayback(report.video_no);
  }

  document.querySelectorAll('#typeTabs [data-type]').forEach(button => {
    button.disabled = true;
    button.addEventListener('click', () => {
      if (!state) return;
      stopBoundary();
      state.activeType = button.dataset.type;
      const first = S.rows(state)[0];
      state.selection = first ? {type: state.activeType, id: String(first.id || ''), spanIndex: 0} : null;
      renderAll();
    });
  });
  el('previousButton').addEventListener('click', () => adjacent(-1));
  el('nextButton').addEventListener('click', () => adjacent(1));
  el('saveButton').addEventListener('click', save);
  el('checkSaveButton').addEventListener('click', checkSaveOutcome);
  el('adoptCurrentSaveButton').addEventListener('click', adoptCurrentSave);
  el('stopBoundaryButton').addEventListener('click',()=>stopBoundary());
  el('closeRevisionCompareButton').addEventListener('click',closeRevisionComparison);
  el('restoreComparedButton').addEventListener('click',()=>{
    const revision=comparedRevision;if(!revision)return;closeRevisionComparison();restore(revision);
  });
  el('revisionCompareDialog').addEventListener('cancel',event=>{event.preventDefault();if(!composing)closeRevisionComparison();});
  el('revisionCompareDialog').addEventListener('close',()=>{if(comparedRevision)closeRevisionComparison();});
  el('reconnectButton').addEventListener('click', reconnectPlayback);
  document.querySelectorAll('[data-add-type]').forEach(button => button.addEventListener('click', () => addItem(button.dataset.addType)));
  el('undoButton').addEventListener('click', undo);
  el('redoButton').addEventListener('click', redo);
  el('deleteItemButton').addEventListener('click', deleteItem);
  el('pipButton').addEventListener('click', () => player.pictureInPicture().catch(error => setStatus(error.message, 'error')));
  el('previewButton').addEventListener('click', previewCurrent);
  el('retryPreviewButton').addEventListener('click', previewCurrent);
  el('previewFrame').addEventListener('load', () => {
    if(previewPhase!=='rendering'||!state||previewDraftFingerprint!==JSON.stringify(draftPayload()))return;
    const doc = el('previewFrame').contentDocument;
    if(doc?.querySelector('meta[name="report-workspace-preview-request"]')?.content!==String(previewRequest))return;
    const valid = doc && doc.body && doc.querySelector('title') && !doc.querySelector('.report-workspace-page');
    previewPhase=valid?'ready':'error';el('previewFrame').hidden=!valid;
    el('retryPreviewButton').hidden=valid;
    el('previewStatus').textContent = valid ? (state.dirty?'현재 미저장 편집안 미리보기 · 저장·공개하지 않았습니다.':'현재 저장본의 편집안 미리보기 · 공개하지 않았습니다.') : '미리보기 화면을 확인하지 못했습니다. 입력은 유지하며 다시 확인할 수 있습니다.';
  });
  el('closePreviewButton').addEventListener('click', closePreview);
  el('previewDialog').addEventListener('cancel',event=>{event.preventDefault();if(!composing)closePreview();});
  el('previewDialog').addEventListener('close',()=>{if(previewPhase!=='idle')closePreview();});
  document.addEventListener('compositionstart',()=>{composing=true;});
  document.addEventListener('compositionend',()=>{composing=false;});
  el('editorForm').addEventListener('submit',event=>event.preventDefault());
  el('editorFields').addEventListener('keydown',event=>{
    if((composing||event.isComposing||event.keyCode===229)&&['Enter','Escape'].includes(event.key))event.stopPropagation();
  },true);
  el('video').addEventListener('timeupdate', () => {
    timeline.setPlayhead(el('video').currentTime);
    if(boundaryPlayback && el('video').currentTime>=boundaryPlayback.end)stopBoundary('경계 주변 재생을 마쳤습니다. 시간과 편집 내용은 바뀌지 않았습니다.');
  });
  for(const event of ['error','ended'])el('video').addEventListener(event,()=>stopBoundary(event==='error'?'영상 연결 실패로 경계 확인을 중지했습니다.':'경계 확인을 마쳤습니다.'));
  el('video').addEventListener('pause',()=>{if(boundaryPlayback && el('video').paused && !el('video').seeking)stopBoundary();});
  for(const event of ['loadedmetadata','canplay','seeked','error','emptied','abort'])
    el('video').addEventListener(event,updatePlaybackActions);
  el('typeTabs').addEventListener('keydown',event=>{
    if(composing || event.isComposing || event.keyCode===229)return;
    const tabs=[...el('typeTabs').querySelectorAll('[data-type]')], index=tabs.indexOf(event.target);
    if(index<0 || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();
    const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    tabs[next].click();tabs[next].focus();
  });
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || composing || event.isComposing || event.keyCode===229 || event.altKey || !(event.ctrlKey || event.metaKey)) return;
    if (event.target.closest('input, textarea, [contenteditable="true"]')) return;
    const key = event.key.toLowerCase();
    const wantsUndo = key === 'z' && !event.shiftKey;
    const wantsRedo = key === 'y' || (key === 'z' && event.shiftKey);
    if (!wantsUndo && !wantsRedo) return;
    event.preventDefault();
    if (wantsRedo) redo();
    else undo();
  });
  window.addEventListener('beforeunload', event => {
    if (!state || !(state.dirty || saveOutcome || savePending)) return;
    event.preventDefault();
    event.returnValue = '';
  });

  load().catch(error => setStatus(error.message || '리포트를 불러오지 못했습니다.', 'error'));
})();
