/* Shared, read-only presentation. Bootstrap passes one report to this renderer. */
(() => {
  'use strict';
  const mount = (data, environment = {}) => {
  if (document.body.dataset.initReady === 'true') return;
  const $ = id => document.getElementById(id);
  const validTime = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const validRange = value => validTime(value.start) && validTime(value.end) && value.end > value.start;
  const hasTimeline = validTime(data.duration) && data.duration > 0;
  const setLink = (node, href) => {
    if (!node) return;
    if (href) { node.href=href; node.removeAttribute('aria-disabled'); }
    else { node.removeAttribute('href'); node.setAttribute('aria-disabled','true'); }
  };
  const disableEmptyLinks = (root=document) => root.querySelectorAll('a[href=""]').forEach(node=>setLink(node,''));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const time = seconds => Number.isFinite(seconds) && seconds >= 0 ? [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, Math.floor(seconds) % 60].map(n => String(n).padStart(2, '0')).join(':') : '시각 미확인';
  const length = seconds => {
    if (!Number.isFinite(seconds)) return '';
    const value = Math.max(0, Math.round(seconds));
    return value >= 60 ? `${Math.floor(value / 60)}분${value % 60 ? ` ${value % 60}초` : ''}` : `${value}초`;
  };
  const setText = (id, text) => { if ($(id)) $(id).textContent = text ?? ''; };
  const icon = name => `<svg class="icon tiny" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const bookmarkIcon = saved => `<svg viewBox="0 0 24 24" width="18" height="18" fill="${saved ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M6.5 4.5h11v16L12 17l-5.5 3.5z" stroke-linejoin="round"/></svg>`;
  const baseMoments = data.moments;
  const clips = data.clips || [];
  const pointItems = (data.points || []).map(point => baseMoments.find(item=>item.id===point.id) || {id:point.id,kind:'point',title:point.title,description:point.content||point.story,start:point.time,chapter:point.d1_ref,topic:point.d2_ref,point,image:null,sourceUrl:point.sourceUrl});
  const allItems = [...new Map([...baseMoments,...pointItems,...clips].map(item=>[item.id,item])).values()];
  const points = item => item?.kind === 'point' ? (item.point ? [item.point] : []) : (item?.points || []);
  const chapter = id => data.chapters.find(item => item.id === id);
  const topicAt = sec => validTime(sec) ? (data.topics || []).find(item => validRange(item) && item.start <= sec && sec < item.end) : null;
  const chapterAt = sec => validTime(sec) ? data.chapters.find(item => validRange(item) && item.start <= sec && sec < item.end) : null;
  const unavailableImages = new Set();
  const hasArtwork = item => Boolean(item?.image?.src) && !unavailableImages.has(item.image.src);
  const isImageExample = item => hasArtwork(item) && item.image.role === 'design_example_from_existing_public_clip_thumbnail';
  const imageLabel = item => item?.image?.label || '장면 이미지 없음';
  const sourceSpans = item => (item?.source_spans || []).filter(validRange);
  const editorFragment = item => item?.kind === 'highlight' ? 'editorial-highlight-' + item.id : item?.editorFragment || item?.fragment || '';
  const spanDuration = item => sourceSpans(item).reduce((sum, span) => sum + span.end - span.start, 0);
  const hintTime = item => { const span = sourceSpans(item)[0]; return span ? (span.start + span.end) / 2 : item.start; };
  const priorityOf = item => {
    const value=item?.editorial_priority ?? item?.point?.editorial_priority;
    const labels={focus:'먼저 검토',normal:'함께 검토',later:'뒤에 검토'};
    return value && Object.hasOwn(labels,value.tier) ? {...value,label:labels[value.tier]} : {tier:'unspecified',label:'미지정',reason:'',basis:''};
  };
  const priorityLabel = item => {
    const priority=priorityOf(item);
    return `<span class="priority-label" data-priority="${priority.tier}" title="${esc(priority.reason||'검토 우선도가 지정되지 않았습니다.')}">${priority.label}</span>`;
  };
  const priorityExplanation = item => {
    const priority=priorityOf(item);
    if(!priority.reason&&!priority.basis)return '';
    const basis={author:'작성자 판단',manager:'관리자 판단',display_review:'표시 검토'}[priority.basis]||priority.basis;
    return `<div class="priority-explanation"><span>검토 이유${basis?` · ${esc(basis)}`:''}</span>${priority.reason?`<p>${esc(priority.reason)}</p>`:''}</div>`;
  };
  const inRange = (item, range) => validRange(range) && (item.kind === 'highlight' ? sourceSpans(item).some(span => span.start < range.end && span.end > range.start) : validTime(item.start) && range.start <= item.start && item.start < range.end);
  const kindLabel = item => item.kind === 'highlight' ? '하이라이트' : item.kind === 'clip' ? '시청자 클립' : '핵심 지점';
  const defaultMoment = allItems.find(item => item.id === data.initialMomentId) || baseMoments.find(item => item.image?.role === 'original_saved_highlight_image') || allItems[0] || null;
  const state = { selected: defaultMoment?.id || '', currentTime: defaultMoment?.start ?? null, point: null, contextOpen: false, chapter: 'all', topic: 'all', filter: !baseMoments.length && clips.length ? 'clip' : 'all', query: '', editor: false, density: 'comfortable', bookmarks: new Set(), theme: 'light' };
  const bookmarkKey = `loki-bookmarks-v2:${data.sourcePlatform}:${data.videoNo}:${environment.reportURL || data.reportId}`;
  let toastTimer, lastModalFocus, initializing = true, editorReady = false, resizeTimer, timelineController;
  const rangePreview = {hover: null, focus: null, pinned: []};
  const linkedHighlights = item => !item ? [] : item.kind === 'highlight' ? [item.id] : item.kind === 'point'
    ? baseMoments.filter(highlight => highlight.kind === 'highlight' && (highlight.point_refs || []).includes(item.id)).map(highlight => highlight.id) : [];
  const previewIds = node => {
    if (!node) return null;
    if (node.dataset.hintMembers) return JSON.parse(node.dataset.hintMembers);
    if (node.dataset.clusterMembers) return [...new Set(JSON.parse(node.dataset.clusterMembers).flatMap(id => linkedHighlights(allItems.find(item => item.id === id))))];
    return linkedHighlights(allItems.find(item => item.id === node.dataset.moment));
  };
  const updateRangePreview = () => {
    const ids = new Set(rangePreview.hover ?? rangePreview.focus ?? rangePreview.pinned);
    const focusedRange = document.activeElement?.closest?.('#highlightRows .range-hit');
    if (focusedRange) ids.add(focusedRange.dataset.moment);
    $('highlightRows').querySelectorAll('.range-hit').forEach(node => {
      node.hidden = !ids.has(node.dataset.moment);
    });
    $('mapThumbnails').querySelectorAll('.map-hint-card').forEach(node => {
      const members = JSON.parse(node.querySelector('[data-hint-members]').dataset.hintMembers);
      node.classList.toggle('is-previewed', members.some(id => ids.has(id)));
    });
  };
  try {
    const saved = JSON.parse(localStorage.getItem(bookmarkKey) || '[]');
    if (Array.isArray(saved)) state.bookmarks = new Set(saved.filter(id => allItems.some(item => item.id === id)));
    state.theme = localStorage.getItem('loki-design-theme-v1') === 'dark' ? 'dark' : 'light';
  } catch (_) {}
  const selected = () => allItems.find(item => item.id === state.selected) || defaultMoment;
  const originalURL = () => data.sourceUrl || '';
  const vodURL = sec => validTime(sec) ? data.vodURL(sec) : '';
  const editorAvailable = Boolean(data.videoNo && originalURL());
  const readLocation = () => {
    const params = new URLSearchParams(location.hash.replace(/^#/, ''));
    if (!params.has('moment') && !params.has('view')) return false;
    const item = allItems.find(item => item.id === params.get('moment'));
    if (item) {
      state.selected = item.id;
      const sec = Number(params.get('time'));
      state.currentTime = params.has('time') && Number.isFinite(sec) && sec >= 0 && sec <= data.duration ? sec : item.start;
      state.point = points(item).find(point => point.id === params.get('point'))?.id || null;
      state.contextOpen = Boolean(state.point);
      if (item.kind === 'clip') state.filter = 'clip';
    }
    state.editor = params.get('view') === 'editor';
    return true;
  };
  readLocation();
  const persistLocation = () => {
    if (initializing) return;
    try { history.replaceState(null, '', `${location.pathname}${location.search}#${new URLSearchParams({moment:state.selected,time:String(state.currentTime),view:state.editor?'editor':'watch',...(state.point?{point:state.point}:{})})}`); } catch (_) {}
  };
  const announce = text => {
    clearTimeout(toastTimer);
    setText('toast', text); $('toast').hidden = false; $('toast').classList.add('is-visible');
    toastTimer = setTimeout(() => { $('toast').classList.remove('is-visible'); $('toast').hidden = true; }, 3200);
  };
  const captureFocus = () => {
    const node = document.activeElement;
    const attribute = ['data-cluster-key','data-source-link','data-bookmark','data-point','data-span','data-moment','data-chapter','data-topic','data-scene-cluster'].find(name => node?.hasAttribute?.(name));
    return attribute ? {node,attribute,value:node.getAttribute(attribute),scope:node.closest('#momentGrid,#pointList,#sourceSpans,#overviewChapters,#overviewMarkers,#clipMarkers,#highlightRows,#highlightAnchors,#mapThumbnails,#flowContext')} : null;
  };
  const restoreFocus = saved => {
    if (!saved || document.contains(saved.node)) return;
    const replacement = [...(saved.scope || document).querySelectorAll(`[${saved.attribute}]`)].find(node => node.getAttribute(saved.attribute) === saved.value);
    const mapFocus=['overviewMarkers','clipMarkers','highlightRows','highlightAnchors','mapThumbnails'].includes(saved.scope?.id);
    (replacement || (mapFocus?$('mapScroll'):$('bookmarkButton')))?.focus({preventScroll:true});
  };
  const openModal = (title, html) => {
    if (!$('modal').open) lastModalFocus = document.activeElement;
    setText('modalTitle', title); $('modalBody').innerHTML = html; disableEmptyLinks($('modalBody'));
    if (!$('modal').open) $('modal').showModal();
    $('modalClose').focus();
  };
  const closeModal = () => { $('modal').close(); if (lastModalFocus && document.contains(lastModalFocus)) lastModalFocus.focus(); };
  const copy = async (text, confirmation) => {
    try { await navigator.clipboard.writeText(text); announce(confirmation); }
    catch (_) { openModal('직접 복사하기', `<p>아래 내용을 선택해 복사해 주세요.</p><textarea class="copy-fallback" aria-label="복사할 내용" readonly rows="5">${esc(text)}</textarea>`); $('modalBody').querySelector('textarea')?.select(); }
  };
  const filtered = (kind=state.filter,{ignoreScope=false}={}) => {
    const items = kind === 'clip' ? clips : kind === 'point' ? pointItems : kind === 'saved' ? allItems : baseMoments;
    return items.filter(item => {
      if (!ignoreScope && state.chapter !== 'all' && chapter(state.chapter) && !inRange(item, chapter(state.chapter))) return false;
      const topic = (data.topics || []).find(topic => topic.id === state.topic);
      if (!ignoreScope && topic && !inRange(item, topic)) return false;
      if (kind === 'saved' && !state.bookmarks.has(item.id)) return false;
      if (['highlight','point'].includes(kind) && item.kind !== kind) return false;
      const haystack = `${item.title} ${item.description} ${item.selectionReason || ''} ${chapter(item.chapter)?.title || ''} ${points(item).map(point => `${point.title} ${point.story} ${time(point.time)}`).join(' ')} ${time(item.start)}`.toLocaleLowerCase('ko');
      return state.query.trim().toLocaleLowerCase('ko').split(/\s+/).every(term => haystack.includes(term));
    }).sort((a,b) => (validTime(a.start)?a.start:Infinity)-(validTime(b.start)?b.start:Infinity));
  };
  const renderCards = () => {
    const focus = captureFocus(), visible = filtered();
    $('momentGrid').dataset.density = state.density;
    $('momentGrid').innerHTML = visible.map(item => {
      const saved = state.bookmarks.has(item.id);
      const duration = item.kind === 'highlight' ? length(spanDuration(item)) : item.kind === 'clip' ? length(item.duration || (item.end - item.start)) : '';
      return `<article class="moment-card${item.id === state.selected ? ' is-selected' : ''}${hasArtwork(item) ? '' : ' no-artwork'}" data-card="${esc(item.id)}">
        <div class="moment-leading"><a class="row-time" data-source-link="${esc(item.id)}" href="${esc(vodURL(item.start))}" target="_blank" rel="noopener" aria-label="${time(item.start)}${vodURL(item.start)?'부터 원본 방송 보기':' · 원본 링크 없음'}, ${esc(item.title)}">${item.kind==='point'?`<i class="row-point-mark" data-priority="${priorityOf(item).tier}" aria-hidden="true"></i>`:''}<time>${time(item.start)}</time>${vodURL(item.start)?icon('out'):''}</a>${hasArtwork(item) ? `<button class="moment-image" type="button" data-moment="${esc(item.id)}" aria-label="${esc(item.title)} 이미지로 장면 선택${isImageExample(item)?', 공개 클립 썸네일 배치 예시':''}"><img src="${esc(item.image.src)}" alt="" loading="lazy" width="480" height="270">${isImageExample(item) ? '<span class="image-source-note">배치 예시</span>' : ''}</button>` : ''}</div>
        <button class="moment-select" type="button" data-moment="${esc(item.id)}" aria-pressed="${item.id === state.selected}" aria-label="${time(item.start)} ${esc(item.title)} 선택${isImageExample(item) ? '. 이미지는 이 시각의 원본 화면이 아닌 공개 클립 썸네일 배치 예시입니다.' : ''}">
          <span class="moment-copy"><span class="moment-meta"><span class="card-kind">${kindLabel(item)}</span>${duration ? `<span class="card-duration">${esc(duration)}</span>` : item.kind==='point'?priorityLabel(item):''}</span><span class="moment-title">${esc(item.title)}</span><span class="moment-excerpt">${esc(item.description)}</span><span class="card-context">${esc(topicAt(item.start)?.title || chapter(item.chapter)?.title || '방송 기록')} ${icon('arrow')}</span>${isImageExample(item) ? '<span class="sr-only">이미지는 공개 클립 썸네일의 배치 예시이며 이 하이라이트 시각의 원본 프레임이 아닙니다.</span>' : ''}</span>
        </button><button class="moment-bookmark${saved ? ' is-saved' : ''}" type="button" data-bookmark="${esc(item.id)}" aria-label="${esc(item.title)} ${saved ? '북마크 취소' : '북마크'}" aria-pressed="${saved}">${bookmarkIcon(saved)}</button>
      </article>`;
    }).join('');
    setText('resultCount', visible.length); setText('bookmarkedCount', state.bookmarks.size);
    setText('countAll',filtered('all').length); setText('countHighlights',filtered('highlight').length); setText('countPoints',filtered('point').length); setText('countClips',filtered('clip').length);
    $('clearSearch').hidden = !state.query; $('emptyState').hidden = visible.length > 0;
    $('emptyState').querySelector('h3').textContent = state.filter === 'saved' && !state.bookmarks.size ? '아직 담아 둔 장면이 없어요.' : '이 조건에 맞는 장면이 없어요.';
    $('emptyState').querySelector('p').textContent = state.filter === 'saved' && !state.bookmarks.size ? '장면의 책갈피를 눌러 다시 찾아보세요.' : '검색어를 바꾸거나 방송 전체에서 찾아보세요.';
    $('filterBar').querySelectorAll('[data-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.filter === state.filter)));
    const index = visible.findIndex(item => item.id === state.selected);
    $('prevMoment').disabled = !visible.length || index <= 0;
    $('nextMoment').disabled = !visible.length || index === visible.length-1;
    $('nextMoment').setAttribute('aria-label',index<0?'이 탐색 범위의 첫 장면':'다음 장면');
    const topic = (data.topics || []).find(topic => topic.id === state.topic);
    setText('activeChapterName', `${topic?.title || chapter(state.chapter)?.title || '방송 전체'} · 시간순`);
    setText('momentsTitle', topic ? '이 주제의 장면' : chapter(state.chapter) ? '이 활동의 장면' : '장면 탐색');
    $('clearScope').hidden=state.chapter==='all'&&state.topic==='all';
    const alternatives=[['highlight','하이라이트'],['point','주요 지점'],['clip','시청자 클립']].filter(([kind])=>kind!==state.filter&&filtered(kind).length);
    if(!visible.length&&activeScope()&&!state.query.trim()&&state.filter!=='saved'){ $('emptyState').querySelector('h3').textContent=state.filter==='highlight'?'이 범위에 기록된 하이라이트가 없어요.':state.filter==='point'?'이 범위에 기록된 주요 지점이 없어요.':'이 범위에 요약된 장면이 없어요.'; $('emptyState').querySelector('p').textContent=alternatives.length?'아래 자료에서 장면을 찾아보세요.':'위의 주제 설명과 원본 방송에서 이 구간을 확인할 수 있어요.'; }
    $('emptyAlternatives').innerHTML=visible.length?'':alternatives.map(([kind,label])=>`<button type="button" class="button button-outline" data-filter="${kind}">${label} ${filtered(kind).length}개 보기 ${icon('arrow')}</button>`).join('');
    if(!allItems.length){$('emptyState').querySelector('h3').textContent='아직 등록된 장면이 없어요.';$('emptyState').querySelector('p').textContent='방송 요약과 목차에서 내용을 확인할 수 있어요.';}
    renderScope(); disableEmptyLinks(); restoreFocus(focus);
  };
  const activeScope = () => (data.topics||[]).find(item=>item.id===state.topic) || chapter(state.chapter) || null;
  const rawCounts = range => ({highlights:baseMoments.filter(item=>item.kind==='highlight'&&inRange(item,range)).length,points:pointItems.filter(item=>inRange(item,range)).length,clips:clips.filter(item=>inRange(item,range)).length});
  const countWords = counts => [`하이라이트 ${counts.highlights}`,`주요 지점 ${counts.points}`,`클립 ${counts.clips}`].join(' · ');
  const renderScope = () => {
    const range=activeScope(); $('scopeContext').hidden=!range;
    setText('scopeTitle',range?.title||'');
    if(!range){$('scopeBody').innerHTML='';return;}
    const frame=range.image,frameURL=typeof frame?.data_url==='string'&&/^data:image\/(?:png|jpeg|webp);base64,/.test(frame.data_url)?frame.data_url:null;
    $('scopeBody').innerHTML=`<div class="scope-reading"><p class="scope-description">${esc(range.content||'')}</p><div class="scope-actions"><span class="scope-clock"><time>${time(range.start)}</time><span aria-hidden="true">—</span><time>${time(range.end)}</time></span><a class="text-button" href="${esc(vodURL(range.start))}" target="_blank" rel="noopener" aria-label="${time(range.start)}부터 원본 방송 보기, 새 탭">원본에서 보기 ${icon('out')}</a></div></div>${frameURL?`<figure class="scope-preview"><a href="${esc(vodURL(frame.source_sec))}" target="_blank" rel="noopener" aria-label="${esc(frame.alt||range.title)}, ${time(frame.source_sec)} 원본 이미지 시각"><img src="${esc(frameURL)}" alt="${esc(frame.alt||range.title)}" width="176" height="99"></a><figcaption>저장된 장면 · ${time(frame.source_sec)}</figcaption></figure>`:''}`;
  };
  const clearScopeSelection = ({fromBand=null}={}) => {
    state.chapter='all';state.topic='all';renderCards();renderOverview();
    const target=fromBand?[...$('hierarchyBands').querySelectorAll('[data-chapter],[data-topic]')].find(button=>(fromBand.chapter&&button.dataset.chapter===fromBand.chapter)||(fromBand.topic&&button.dataset.topic===fromBand.topic)):null;
    (target||$('momentsHeading')).focus({preventScroll:true});
    setText('scopeStatus','범위 선택을 해제했습니다. 방송 전체의 장면을 보여줍니다.');
  };
  const renderOutline = () => {
    const currentChapter=chapterAt(state.currentTime),currentTopic=topicAt(state.currentTime);
    $('flowContext').innerHTML=data.chapters.map((item,index)=>{
      const count=rawCounts(item),isCurrent=currentChapter?.id===item.id;
      return `<section class="tree-branch${isCurrent?' is-current':''}" aria-label="${esc(item.title)}"><button type="button" class="tree-d1" data-chapter="${esc(item.id)}" aria-pressed="${state.chapter===item.id&&state.topic==='all'}" aria-label="D1 큰 활동 ${index+1}, ${esc(item.title)}, ${time(item.start)}부터 ${time(item.end)}"><span class="tree-level">D1 · ${String(index+1).padStart(2,'0')}</span><span class="tree-d1-title">${esc(item.title)}</span><span class="tree-d1-meta"><time>${time(item.start)} – ${time(item.end)}</time><span>${(data.topics||[]).filter(topic=>topic.parent_id===item.id||topic.chapter===item.id).length}개 주제</span></span></button><div class="tree-d2-list">${(data.topics||[]).filter(topic=>topic.parent_id===item.id||topic.chapter===item.id).map(topic=>{
        const c=rawCounts(topic),here=currentTopic?.id===topic.id;
        const counts=[c.highlights?`하이라이트 ${c.highlights}`:'',c.points?`지점 ${c.points}`:'',c.clips?`클립 ${c.clips}`:''].filter(Boolean).join(' · ')||'목차와 원본 방송';
        return `<button type="button" class="tree-d2${here?' is-current':''}" data-topic="${esc(topic.id)}" aria-pressed="${state.topic===topic.id}" aria-label="D2 세부 주제 ${esc(topic.title)}, ${time(topic.start)}, ${counts}${here?', 선택한 장면이 속한 주제':''}"><span class="tree-d2-main"><strong>${esc(topic.title)}</strong>${here?'<span class="current-dot" aria-hidden="true"></span>':''}</span><span class="tree-d2-meta"><time>${time(topic.start)}</time><span>${counts}</span></span></button>`;
      }).join('')}</div></section>`;
    }).join('');
    $('allChapters').setAttribute('aria-pressed',String(state.chapter==='all'&&state.topic==='all'));
    setText('chapterNavCount',`${data.chapters.length}개 활동 · ${(data.topics||[]).length}개 주제`);
    setText('sidebarTotal',`${baseMoments.filter(item=>item.kind==='highlight').length} 하이라이트 · ${pointItems.length} 지점`);
  };
  const renderOverview = () => {
    if(!hasTimeline){document.querySelector('.broadcast-overview').hidden=true;renderOutline();return;}
    const focus=captureFocus(),range=activeScope(),viewStart=0,viewEnd=data.duration,viewDuration=data.duration;
    rangePreview.hover = null;
    const width=$('timeMap').clientWidth||1260,hintGap=16,hintWidth=108;
    const percent=sec=>(sec-viewStart)/viewDuration*100;
    const intersects=item=>validRange(item)&&item.start<viewEnd&&item.end>viewStart;
    const parent=state.topic!=='all'?chapter(range?.parent_id||range?.chapter):null;
    $('mapActivityNav').hidden=!range;
    $('mapActivityNav').innerHTML=range?`<button type="button" data-chapter="all">방송 전체</button>${parent?`${icon('chev')}<button type="button" data-chapter="${esc(parent.id)}">${esc(parent.title)}</button>`:''}<span class="sr-only" aria-current="page">${esc(range.title)}</span>`:'';
    const band=(item,kind)=>{
      const start=Math.max(viewStart,item.start),end=Math.min(viewEnd,item.end),active=kind==='chapter'?state.chapter===item.id&&state.topic==='all':state.topic===item.id;
      return `<button type="button" class="hierarchy-band${active?' is-active':''}" data-${kind}="${esc(item.id)}" style="left:${percent(start)}%;width:${(end-start)/viewDuration*100}%" aria-pressed="${active}" aria-label="${kind==='chapter'?'D1 큰 활동':'D2 세부 주제'} ${esc(item.title)}, ${time(item.start)}부터 ${time(item.end)}, ${active?'선택됨, 다시 누르면 해제':'이 범위로 탐색'}" title="${esc(item.title)} · ${time(item.start)}–${time(item.end)}"><span>${esc(item.title)}</span></button>`;
    };
    $('hierarchyBands').innerHTML=`<div class="hierarchy-row d1-row"><div class="hierarchy-row-label" aria-label="D1 큰 활동">큰 활동</div><div class="hierarchy-band-track">${data.chapters.filter(intersects).map(item=>band(item,'chapter')).join('')}</div></div><div class="hierarchy-row d2-row"><div class="hierarchy-row-label" aria-label="D2 세부 주제">세부 주제</div><div class="hierarchy-band-track">${(data.topics||[]).filter(intersects).map(item=>band(item,'topic')).join('')}</div></div>`;
    setText('mapDuration',`${Math.floor(data.duration/3600)}시간 ${Math.floor(data.duration%3600/60)}분`);
    $('clipMapLegend').hidden=state.filter!=='clip';
    // The time map is a stable broadcast overview. List filters only narrow the cards.
    const shownHighlights=baseMoments.filter(item=>item.kind==='highlight'&&validTime(item.start)&&sourceSpans(item).length).sort((a,b)=>a.start-b.start);
    const shownPoints=pointItems.filter(item=>validTime(item.start)&&item.start<=data.duration).sort((a,b)=>a.start-b.start);
    const list=shownHighlights,groups=[];
    const canZoom=(timelineController?.getZoom()||1)<(timelineController?.getMaxZoom()||256);
    const hintAnchor=item=>hintTime(item)/viewDuration*width;
    const clusterAnchor=hintAnchor;
    // Covers indicate nearby material. Source Points and spans remain independently addressable below.
    list.filter(hasArtwork).sort((a,b)=>hintTime(a)-hintTime(b)).forEach(item=>{
      const group=groups[groups.length-1];
      if(group&&clusterAnchor(item)-clusterAnchor(group[0])<hintWidth+hintGap)group.push(item);else groups.push([item]);
    });
    const capacity=Math.max(1,Math.floor((width+hintGap)/(hintWidth+hintGap)));
    while(groups.length>capacity){
      let closest=0,cost=Infinity;
      for(let i=0;i<groups.length-1;i++){
        const gap=hintAnchor(groups[i+1][0])-hintAnchor(groups[i][groups[i].length-1]);
        if(gap<cost){cost=gap;closest=i;}
      }
      groups.splice(closest,2,[...groups[closest],...groups[closest+1]]);
    }
    const hints=groups.map(group=>{
      const representative=group.find(item=>item.id===state.selected)||group[0];
      const center=(hintAnchor(group[0])+hintAnchor(group[group.length-1]))/2;
      return {group,representative,anchor:group.length>1?center:hintAnchor(representative),left:Math.min(width-hintWidth,Math.max(0,center-hintWidth/2))};
    });
    for(let i=1;i<hints.length;i++)hints[i].left=Math.max(hints[i].left,hints[i-1].left+hintWidth+hintGap);
    for(let i=hints.length-1;i>=0;i--)hints[i].left=Math.min(hints[i].left,i===hints.length-1?width-hintWidth:hints[i+1].left-hintWidth-hintGap);
    const zoneHeight=groups.length?86:12;
    $('timeMap').style.setProperty('--hint-zone-height',`${zoneHeight}px`);
    $('mapThumbnails').hidden=!hints.length;
    $('mapThumbnails').innerHTML=hints.length?hints.map(({group,representative,anchor,left})=>{
      const item=representative,multiple=group.length>1,active=group.some(entry=>entry.id===state.selected);
      const ids=esc(JSON.stringify(group.map(entry=>entry.id))),tailX=Math.max(8,Math.min(hintWidth-8,anchor-left));
      const title=multiple?`이 근처 ${group.every(entry=>entry.kind==='highlight')?'하이라이트':'장면'} ${group.length}개`:item.title;
      const duration=item.kind==='highlight'?length(spanDuration(item)):'';
      const groupTime=(hintTime(group[0])+hintTime(group[group.length-1]))/2;
      const action=multiple?`data-scene-cluster="${ids}" data-cluster-key="image:${esc(group.map(entry=>entry.id).join('|'))}" data-zoom-time="${groupTime}"${canZoom?'':' aria-haspopup="dialog"'}`:`data-moment="${esc(item.id)}" aria-pressed="${active}"`;
      const label=multiple?`${time(group[0].start)} 근처 ${group.length}개 장면 ${canZoom?'확대해서 보기':'고르기'}`:`${kindLabel(item)}, ${time(item.start)}, ${item.title} 선택`;
      return `<article class="map-hint-card${multiple?' is-cluster':''}${active?' has-selection':''}" aria-label="${esc(title)}" style="left:${(left+hintWidth/2)/width*100}%;--hint-width:${hintWidth}px;--hint-height:61px"><div class="hint-entry${active?' is-selected':''}" data-hint-id="${esc(item.id)}" data-hint-members="${ids}" data-hint-count="${group.length}"><button type="button" class="hint-picture" ${action} aria-label="${esc(label)}${isImageExample(item)?'. 이미지는 공개 클립 썸네일 배치 예시입니다.':''}"><img src="${esc(item.image.src)}" alt="" width="108" height="61" loading="lazy">${isImageExample(item)?'<span class="image-source-note">예시</span>':''}${multiple?`<span class="hint-count" aria-hidden="true">${group.length}</span>`:''}</button><div class="hint-copy"><strong class="hint-title">${esc(title)}</strong><div class="hint-footer">${multiple?`<span class="hint-cluster-time">${canZoom?'확대해서 보기':'장면 고르기'} ${icon('chev')}</span>`:`<a class="hint-watch" href="${esc(vodURL(item.start))}" target="_blank" rel="noopener" aria-label="${esc(item.title)}, ${time(item.start)}부터 원본 방송 보기"><time>${time(item.start)}</time></a>${duration?`<span class="hint-duration">${duration}</span>`:''}`}</div></div></div><svg class="hint-connector" aria-hidden="true" style="width:${hintWidth}px" viewBox="0 0 ${hintWidth} 22"><path d="M ${tailX} 0 L ${tailX} 12 L ${anchor-left} 22"/></svg></article>`;
    }).join(''):'';

    // Points and editing intervals are separate source objects, even when their stories overlap.
    const markerMarkup=axisItems=>{
    const pointGroups=[];
    axisItems.forEach(item=>{
      const x=item.start/viewDuration*width,previous=pointGroups[pointGroups.length-1];
      if(previous&&x-previous.lastX<26&&x-previous.firstX<64){previous.items.push(item);previous.lastX=x;}
      else pointGroups.push({items:[item],firstX:x,lastX:x});
    });
    return pointGroups.map(group=>{
      if(group.items.length>1){
        const items=group.items,kind=items[0].kind,ids=esc(JSON.stringify(items.map(item=>item.id)));
        const sec=(items[0].start+items[items.length-1].start)/2;
        const displayX=Math.max(20,Math.min(width-20,sec/viewDuration*width));
        const focusCount=items.filter(item=>priorityOf(item).tier==='focus').length;
        const active=items.some(item=>item.id===state.selected);
        const related=selected()?.kind==='highlight'&&items.some(item=>(selected().point_refs||[]).includes(item.id));
        const label=`${kind==='clip'?'클립':'지점'} ${items.length}개${kind==='point'&&focusCount?` 중 먼저 검토 ${focusCount}개`:''}`;
        return `<button type="button" class="map-marker ${kind==='clip'?'clip-cluster':'point-cluster'}${active?' is-selected':''}${related?' is-related':''}" data-map-cluster="${kind}" data-cluster-members="${ids}" data-cluster-key="${kind}:${esc(items.map(item=>item.id).join('|'))}" data-scene-cluster="${ids}" data-focus-count="${focusCount}" data-zoom-time="${sec}" data-tip-align="${percent(sec)>82?'end':percent(sec)<18?'start':'center'}" style="left:${displayX/width*100}%;top:6px" aria-label="${time(sec)} 근처 ${esc(label)}, ${canZoom?'확대해서 각각 보기':'장면 고르기'}"${canZoom?'':' aria-haspopup="dialog"'}><span aria-hidden="true"><b class="cluster-number">${items.length}</b></span><span class="marker-tooltip">${esc(label)} · ${canZoom?'확대해서 보기':'장면 고르기'}</span></button>`;
      }
      const item=group.items[0];
      const priority=priorityOf(item),active=item.id===state.selected,related=selected()?.kind==='highlight'&&(selected().point_refs||[]).includes(item.id);
      const x=item.start/viewDuration*width,radius=priority.tier==='focus'?14:priority.tier==='normal'?12:9;
      const displayX=Math.max(radius+2,Math.min(width-radius-2,x));
      return `<button type="button" class="map-marker ${item.kind}${active?' is-selected':''}${related?' is-related':''}" ${item.kind==='point'?`data-priority="${priority.tier}"`:''} data-tip-align="${percent(item.start)>82?'end':percent(item.start)<18?'start':'center'}" data-moment="${esc(item.id)}" style="left:${percent(item.start)}%;top:${item.kind==='point'?6:0}px;--point-edge-shift:${displayX-x}px" aria-label="${kindLabel(item)}, ${time(item.start)}, ${esc(item.title)}${item.kind==='point'?`, ${priority.label}`:''}" aria-pressed="${active}"><span></span><span class="marker-tooltip">${time(item.start)} · ${esc(item.title)}${item.kind==='point'?` · ${priority.label}`:''}</span></button>`;
    }).join('');
    };
    $('overviewMarkers').innerHTML=markerMarkup(shownPoints);
    // Highlights without artwork remain visible as small interval symbols, never fake thumbnails.
    const bareGroups=[];
    shownHighlights.filter(item=>!hasArtwork(item)).sort((a,b)=>hintTime(a)-hintTime(b)).forEach(item=>{
      const previous=bareGroups[bareGroups.length-1];
      if(previous&&(hintTime(item)-hintTime(previous[previous.length-1]))/viewDuration*width<32)previous.push(item);
      else bareGroups.push([item]);
    });
    $('highlightAnchors').innerHTML=bareGroups.map(group=>{
      const ids=group.map(item=>item.id),multiple=group.length>1;
      const sec=(hintTime(group[0])+hintTime(group[group.length-1]))/2;
      const action=multiple?`data-map-cluster="highlight-anchor" data-cluster-members="${esc(JSON.stringify(ids))}" data-cluster-key="highlight-anchor:${esc(ids.join('|'))}" data-scene-cluster="${esc(JSON.stringify(ids))}" data-zoom-time="${sec}"`:`data-moment="${esc(group[0].id)}"`;
      const label=multiple?`하이라이트 ${group.length}개, 확대해서 보기`:`하이라이트, ${time(group[0].start)}, ${group[0].title}`;
      return `<button type="button" class="highlight-anchor${multiple?' is-cluster':''}" ${action} style="left:${percent(sec)}%" aria-label="${esc(label)}"><span class="highlight-anchor-symbol" aria-hidden="true"></span>${multiple?`<b aria-hidden="true">${group.length}</b>`:''}<span class="marker-tooltip">${esc(label)}</span></button>`;
    }).join('');
    $('clipRows').hidden=state.filter!=='clip';
    $('clipMarkers').innerHTML=state.filter==='clip'?markerMarkup(clips.filter(item=>validTime(item.start)&&item.start<=data.duration).sort((a,b)=>a.start-b.start)):'';
    setText('pointLaneLabel','장면');
    const visibleTiers=new Set(shownPoints.map(item=>priorityOf(item).tier));
    $('pointPriorityLegend').hidden=true;
    $('pointPriorityLegend').innerHTML=['focus','normal','later','unspecified'].filter(tier=>visibleTiers.has(tier)).map(tier=>`<span class="point-priority-key"><i data-priority="${tier}"></i>${{focus:'먼저 검토',normal:'함께 검토',later:'뒤에 검토',unspecified:'미지정'}[tier]}</span>`).join('');
    const spans=shownHighlights.flatMap(item=>sourceSpans(item).map((span,index)=>({item,span,index}))).sort((a,b)=>a.span.start-b.span.start);
    let minified=false;
    const rangeMarkup=spans.map(({item,span,index})=>{
      const startX=span.start/viewDuration*width,exactWidth=(span.end-span.start)/viewDuration*width;
      const displayWidth=Math.max(exactWidth,24);
      const displayLeft=Math.max(0,Math.min(width-displayWidth,startX+exactWidth/2-displayWidth/2));
      const caption=length(span.end-span.start),compressed=displayWidth>exactWidth+0.01;
      minified ||= compressed;
      const active=item.id===state.selected;
      const overlaps=spans.filter(other=>other.span.start<span.end&&span.start<other.span.end).map(other=>({id:other.item.id,index:other.index}));
      return `<button type="button" hidden class="range-hit${active?' is-selected':''}${compressed?' is-minified':''}" data-moment="${esc(item.id)}" data-start="${span.start}" data-source-span="${index}"${overlaps.length>1?` data-overlap-spans="${esc(JSON.stringify(overlaps))}"`:''} data-tip-align="${percent(span.start)>82?'end':percent(span.start)<18?'start':'center'}" style="left:${percent(span.start)}%;width:${(span.end-span.start)/viewDuration*100}%;top:38px;--range-display-width:${displayWidth}px;--range-display-left:${displayLeft-startX}px;--range-layer:${compressed?2:1}" aria-label="하이라이트 ${esc(item.title)}, 원본 ${index+1}/${sourceSpans(item).length}구간, ${time(span.start)}부터 ${time(span.end)}, ${caption}${compressed?', 축소 화면에서 최소 폭으로 표시':''}${overlaps.length>1?', 다른 하이라이트 구간과 겹침':''}" aria-pressed="${active}"><span class="range-fill"><span class="range-caption">${caption}</span></span><span class="marker-tooltip">${esc(item.title)}<br>${time(span.start)}–${time(span.end)} · ${caption}${overlaps.length>1?'<br>겹친 구간에서 눌러 장면 고르기':''}</span></button>`;
    }).join('');
    $('highlightRows').hidden=!spans.length;
    $('highlightRows').innerHTML=spans.length?`<div class="highlight-track" style="height:62px">${rangeMarkup}</div>`:'';
    $('rangeScaleNote').hidden=!minified;
    updateRangePreview();
    const step=[1,2,5,10,15,30,60,120,300,600,900,1800,3600,7200].find(sec=>sec>=viewDuration/Math.max(1,width/110))||7200,ticks=[0];
    for(let sec=step;sec<viewDuration;sec+=step)if((viewDuration-sec)/viewDuration*width>70)ticks.push(sec);
    ticks.push(viewDuration);
    $('overviewScale').innerHTML=ticks.map(sec=>`<time style="left:${percent(sec)}%">${time(sec)}</time>`).join('');
    $('mapSelection').hidden=true;
    $('mapSelection').innerHTML='';
    setText('mapTopic',range?`${state.topic!=='all'?'D2':'D1'} · ${range.title}`:topicAt(state.currentTime)?.title||'방송 전체');
    renderOutline();disableEmptyLinks();restoreFocus(focus);
  };
  const collaborationView=window.createLokiCollaborationView({data,esc,time,icon,openModal,container:$('collaborationCard')});
  const renderCollaboration=()=>collaborationView.render(state.currentTime);
  const postEditor = message => {
    const frame=$('nativeEditorFrame');
    if (frame?.contentWindow && editorReady) frame.contentWindow.postMessage(message,location.origin);
  };
  const inspectInEditor = () => { if(validTime(state.currentTime))postEditor({type:'loki-editor:inspect',sec:state.currentTime,highlightFragment:editorFragment(selected())}); };
  const setContextOpen = open => {
    state.contextOpen=Boolean(open);
    $('sceneContext').hidden=!open;
    $('contextToggle').setAttribute('aria-expanded',String(open));
  };
  const renderSelection = () => {
    const focus=captureFocus(), item=selected();
    document.querySelector('.focus-layout').hidden=!item;
    if(!item){$('bookmarkButton').disabled=true;renderCards();renderOverview();renderCollaboration();persistLocation();return;}
    $('heroArtwork').hidden=!hasArtwork(item);
    $('sceneHero').hidden=!hasArtwork(item);
    $('sceneHero').closest('.focus-layout').classList.toggle('without-artwork',!hasArtwork(item));
    const bookmarkHost=hasArtwork(item)?document.querySelector('.scene-topline'):document.querySelector('.detail-top');
    if($('bookmarkButton').parentElement!==bookmarkHost)bookmarkHost.append($('bookmarkButton'));
    const collaborationHost=hasArtwork(item)?document.querySelector('.scene-stage'):document.querySelector('.focus-layout');
    if($('collaborationCard').parentElement!==collaborationHost)collaborationHost.append($('collaborationCard'));
    if (hasArtwork(item)) { $('heroArtwork').src=item.image.src; $('heroArtwork').alt=item.image.alt || item.title; }
    $('heroWatch').hidden=!hasArtwork(item);
    setText('heroImageKind',imageLabel(item));
    setText('heroImageStatus',hasArtwork(item) ? `${isImageExample(item)?'공개 클립 썸네일 · 장면 배치 예시':item.image.role==='original_saved_highlight_image'?'저장된 대표 장면':item.kind==='clip'?'시청자 클립 썸네일':'하이라이트 이미지'}${Number.isFinite(item.image.source_sec)?` · 이미지 시각 ${time(item.image.source_sec)}`:''}` : '');
    setText('selectedTitle',item.title); setText('selectedStory',item.kind==='point'?(item.point?.story||item.description):item.description); $('selectedStory').hidden=!$('selectedStory').textContent;
    setText('selectedTime',time(state.currentTime)); setText('selectedKind',kindLabel(item));
    setText('selectedChapter',chapter(item.chapter)?.short || chapterAt(item.start)?.title || '방송 기록');
    setText('selectedTopic',topicAt(state.currentTime)?.title || '주요 장면');
    const selectedSet=item.kind==='clip'?clips:item.kind==='point'?pointItems:baseMoments.filter(value=>value.kind==='highlight');
    setText('selectedIndex',`${String(selectedSet.findIndex(v=>v.id===item.id)+1).padStart(2,'0')} / ${String(selectedSet.length).padStart(2,'0')}`);
    const duration=item.kind==='highlight'?spanDuration(item):item.duration || item.end-item.start;
    if(item.kind==='point')$('selectionMeta').innerHTML=priorityLabel(item);
    else setText('selectionMeta',item.kind==='highlight'?`${length(duration)} · 원본 ${sourceSpans(item).length}구간`:`${length(duration)} · 시청자가 만든 클립`);
    if(item.kind==='highlight'&&Object.hasOwn(item,'editorial_priority'))$('selectionMeta').insertAdjacentHTML('beforeend',` · ${priorityLabel(item)}`);
    setText('contextLabel','주요 내용들');
    setText('sourceHeading',item.kind==='highlight'?'하이라이트 구간':item.kind==='point'?'검토 정보':'시청자 클립');
    $('sourceHeading').parentElement.hidden=item.kind==='point';
    const href=vodURL(state.currentTime);
    setLink($('watchButton'),href); setLink($('heroWatch'),href); setLink($('selectedTimeLink'),href);
    $('watchButton').hidden=!href; $('heroWatch').hidden=!href||!hasArtwork(item);
    setText('watchLabel','원본 영상에서 보기');
    $('watchButton').setAttribute('aria-label',`${kindLabel(item)}, ${time(state.currentTime)}부터 원본 방송 보기, 새 탭`);
    $('heroWatch').setAttribute('aria-label',$('watchButton').getAttribute('aria-label'));
    const saved=state.bookmarks.has(item.id); $('bookmarkButton').setAttribute('aria-pressed',String(saved)); $('bookmarkButton').setAttribute('aria-label',saved?'선택한 장면 북마크 취소':'선택한 장면 북마크'); $('bookmarkButton').innerHTML=bookmarkIcon(saved);
    const spans=sourceSpans(item),multipleSpans=spans.length>1;
    $('sourceSpans').innerHTML=item.kind==='highlight'?spans.map((span,index)=>{
      const gap=index>0?span.start-sourceSpans(item)[index-1].end:0,active=span.start<=state.currentTime&&state.currentTime<span.end;
      return `${gap>0?`<div class="span-gap"><span></span>${length(gap)} 건너뜀<span></span></div>${span.exclusion_before?.reason?`<p class="span-reason">${esc(span.exclusion_before.reason)}</p>`:''}`:''}<div class="source-span-row"><button type="button" class="source-span${active?' is-active':''}" data-span="${esc(item.id)}:${index}" aria-label="원본 ${index+1}구간, ${time(span.start)}부터 ${time(span.end)} 선택" aria-pressed="${active}">${multipleSpans?`<span class="range-index">${String(index+1).padStart(2,'0')}</span>`:''}<span class="range-time">${time(span.start)} – ${time(span.end)}</span>${multipleSpans?`<span class="range-length">${length(span.end-span.start)}</span>`:''}${icon('chev')}</button></div>`;
    }).join(''):'';
    if(item.kind==='clip'&&item.sourceUrl)$('sourceSpans').innerHTML=`<a class="clip-secondary-link text-button" href="${esc(item.sourceUrl)}" target="_blank" rel="noopener">시청자 클립 보기 ${icon('out')}</a>`;
    if(item.kind==='point'||Object.hasOwn(item,'editorial_priority'))$('sourceSpans').insertAdjacentHTML('beforeend',priorityExplanation(item));
    const reason=item.selectionReason || item.selection_reason || '';
    $('highlightReason').hidden=item.kind!=='highlight'||!reason;
    $('highlightReason').innerHTML=item.kind==='highlight'&&reason?`<h4>볼 이유</h4><p>${esc(reason)}</p>`:'';
    $('sourceLabels').innerHTML=item.kind==='highlight'?spans.map((span,index)=>span.label?`<div class="source-label-note">${multipleSpans?`<h4>원본 ${index+1}구간</h4>`:''}<p class="span-reason">${esc(span.label)}</p></div>`:'').join(''):'';
    $('sourceNotes').hidden=!$('sourceLabels').textContent&&$('highlightReason').hidden;
    $('pointList').innerHTML=(item.kind==='point'?[]:points(item)).map((p,index)=>{
      const active=state.point===p.id;
      return `<article class="linked-point${active?' is-selected':''}"><button type="button" class="point-item${active?' is-selected':''}" data-point="${esc(p.id)}" data-owner="${esc(item.id)}" aria-pressed="${active}" aria-expanded="${active}" aria-controls="point-story-${index}"><time>${time(p.time)}</time><span><strong>${esc(p.title)}</strong>${priorityLabel(p)}</span>${icon('chev')}</button><div id="point-story-${index}" class="point-story-panel"${active?'':' hidden'}>${active?`<div id="selectedPointContext" class="point-context"><p>${esc(p.story||'')}</p>${priorityExplanation(p)}</div>`:''}</div></article>`;
    }).join('');
    $('detailPoints').hidden=item.kind==='point'||!points(item).length;
    $('sourceGroup').hidden=!$('sourceSpans').textContent&&$('highlightReason').hidden;
    $('sceneContext').classList.toggle('single-context',$('detailPoints').hidden||$('sourceGroup').hidden);
    $('contextToggle').hidden=$('detailPoints').hidden&&$('sourceGroup').hidden;
    setContextOpen(state.contextOpen&&!$('contextToggle').hidden);
    renderCards(); renderOverview(); renderCollaboration(); persistLocation(); restoreFocus(focus);
  };
  const selectMoment = (id,options={}) => {
    const item=allItems.find(item=>item.id===id); if(!item) return;
    if(state.selected!==id){$('sourceNotes').open=false;if(!options.openContext)state.contextOpen=false;}
    state.selected=id; state.currentTime=options.time??item.start; state.point=options.point||null;
    rangePreview.pinned=linkedHighlights(item); rangePreview.hover=null; rangePreview.focus=null;
    if(item.kind==='clip'&&state.filter!=='saved'&&!options.fromEditor)state.filter='clip';
    renderSelection();
    if(options.openContext)setContextOpen(true);
    if(state.editor&&!options.fromEditor)inspectInEditor();
    if(options.announce!==false)announce(`${time(state.currentTime)} · ${item.title}`);
    if(options.scroll){$('selectedTitle').focus({preventScroll:true});$('sceneHero').closest('.focus-layout').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}
  };
  const toggleBookmark = id => {
    if(!id||!allItems.some(item=>item.id===id))return;
    const removed=state.bookmarks.has(id); removed?state.bookmarks.delete(id):state.bookmarks.add(id);
    let saved=true;try{localStorage.setItem(bookmarkKey,JSON.stringify([...state.bookmarks]));}catch(_){saved=false;}
    renderSelection();announce(removed?'북마크에서 뺐어요.':saved?'이 브라우저에 장면을 담았어요.':'현재 화면에 장면을 담았어요. 브라우저 저장은 사용할 수 없어요.');
  };
  const setEditor = (active,options={}) => {
    active=Boolean(active&&editorAvailable);
    state.editor=active; $('editorPanel').hidden=!active; $('viewerWorkspace').hidden=active;
    if(!active)timelineController?.refreshWidth();
    document.body.classList.toggle('editor-open',active); $('editorToggle').setAttribute('aria-expanded',String(active));
    $('viewWatch').setAttribute('aria-pressed',String(!active)); $('viewEditor').setAttribute('aria-pressed',String(active));
    if(active){
      if(!$('nativeEditorFrame').getAttribute('src')){
        const editorURL=new URL('native-editor.html',environment.runtimeBase || document.baseURI);
        editorURL.search=new URLSearchParams({data:environment.reportURL,...(environment.runtimeVersion?{v:environment.runtimeVersion}:{}),...(validTime(state.currentTime)?{t:String(state.currentTime)}:{}),highlight:editorFragment(selected()),theme:state.theme}).toString();
        $('nativeEditorFrame').src=editorURL.href;
      }
      else {inspectInEditor();postEditor({type:'loki-editor:resize'});}
    }
    persistLocation();
    if(options.navigate){const node=active?$('editorHeading'):$('momentsHeading');node.setAttribute('tabindex','-1');node.focus({preventScroll:true});(active?$('editorPanel'):$('moments')).scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}
  };
  const setTheme = theme => {
    state.theme=theme;document.documentElement.dataset.theme=theme;
    $('themeButton').setAttribute('aria-label',theme==='light'?'어두운 화면으로 바꾸기':'밝은 화면으로 바꾸기');
    $('themeButton').querySelector('svg').innerHTML=theme==='light'?'<use href="#i-moon"/>':'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/>';
    try{localStorage.setItem('loki-design-theme-v1',theme);}catch(_){}
    postEditor({type:'loki-editor:theme',theme});
  };
  const step = direction => {const list=filtered(),index=list.findIndex(item=>item.id===state.selected);if(list.length)selectMoment(list[index<0?0:Math.max(0,Math.min(list.length-1,index+direction))].id);};
  const showCollaboration=()=>collaborationView.show(state.currentTime);
  const zoomCluster = (target, event) => {
    if (!timelineController || timelineController.getZoom() >= timelineController.getMaxZoom()) return false;
    const kind = target.dataset.mapCluster || (target.closest('#mapThumbnails') ? 'image' : '');
    const spanCluster = kind === 'highlight';
    let refs;
    try { refs = JSON.parse(spanCluster ? target.dataset.clusterSpans : target.dataset.clusterMembers || target.dataset.sceneCluster || '[]'); }
    catch (_) { return false; }
    if (!Array.isArray(refs) || refs.length < 2) return false;
    const keys = new Set(refs.map(ref => spanCluster ? `${ref.id}:${ref.index}` : ref));
    const highlights = baseMoments.filter(item => item.kind === 'highlight');
    const records = (spanCluster
      ? highlights.flatMap(item => sourceSpans(item).map((span, index) => ({key:`${item.id}:${index}`,time:(span.start+span.end)/2,start:span.start,end:span.end})))
      : (kind === 'image' ? highlights.filter(hasArtwork) : kind === 'highlight-anchor' ? highlights.filter(item=>!hasArtwork(item)) : kind === 'clip' ? clips : pointItems)
        .map(item => { const sec = kind === 'image' || kind === 'highlight-anchor' ? hintTime(item) : item.start; return {key:item.id,time:sec,start:sec,end:sec}; }))
      .filter(record => Number.isFinite(record.time) && record.time >= 0 && record.time <= data.duration)
      .sort((a, b) => a.time - b.time);
    const members = records.filter(record => keys.has(record.key));
    if (members.length < 2) return false;
    let minimumTimeGap = Infinity;
    for (let index = 1; index < records.length; index++) {
      const previous = records[index - 1], current = records[index];
      if (!keys.has(previous.key) && !keys.has(current.key)) continue;
      // Real overlapping source spans remain overlapping; their chooser is separate.
      if (spanCluster && previous.start < current.end && current.start < previous.end) continue;
      const gap = current.time - previous.time;
      if (gap <= 0) return false;
      minimumTimeGap = Math.min(minimumTimeGap, gap);
    }
    if (!Number.isFinite(minimumTimeGap)) return false;
    const rect = target.getBoundingClientRect();
    const imageWidth = kind === 'image' ? rect.width || 108 : 0;
    const separation = kind === 'image' ? imageWidth + 18 : 32;
    // Include immediate outside neighbours so a boundary member does not merely
    // move into the next cluster after the zoom.
    const minimumTrackWidth = Math.max(data.duration * separation / minimumTimeGap,
      kind === 'image' ? records.length * (imageWidth + 16) - 15 : 0);
    const result = timelineController.revealCluster({
      minimumTrackWidth,
      start: Math.min(...members.map(member => member.start)),
      end: Math.max(...members.map(member => member.end)),
      anchors: members.map(member => member.time),
      anchorTime: Number(target.dataset.zoomTime),
      clientX: event.detail > 0 && Number.isFinite(event.clientX) ? event.clientX : rect.left + rect.width / 2,
      padding: kind === 'image' ? imageWidth / 2 + 12 : 24
    });
    if (!result.separated) return false;
    const container = $(kind === 'image' ? 'mapThumbnails' : spanCluster ? 'highlightRows' : kind === 'highlight-anchor' ? 'highlightAnchors' : kind === 'clip' ? 'clipMarkers' : 'overviewMarkers');
    const attribute = spanCluster ? 'data-cluster-spans' : kind === 'image' ? 'data-scene-cluster' : 'data-cluster-members';
    return ![...container.querySelectorAll(`[${attribute}]`)].some(button => {
      const remaining = JSON.parse(button.getAttribute(attribute));
      return remaining.some(ref => keys.has(spanCluster ? `${ref.id}:${ref.index}` : ref));
    });
  };
  document.addEventListener('click',event=>{
    const target=event.target.closest('button,a');if(!target)return;
    if(target.getAttribute('aria-disabled')==='true'){event.preventDefault();return;}
    if(target.dataset.overlapSpans&&event.detail>0){
      const rect=target.closest('.highlight-track').getBoundingClientRect();
      const sec=rect.width>0?(event.clientX-rect.left)/rect.width*data.duration:NaN;
      const overlapping=JSON.parse(target.dataset.overlapSpans).map(ref=>{
        const item=allItems.find(item=>item.id===ref.id),span=item&&sourceSpans(item)[ref.index];
        return item&&span?{item,span,index:ref.index}:null;
      }).filter(value=>value&&(target.classList.contains('is-minified')||(value.span.start<=sec&&sec<value.span.end)));
      if(overlapping.length>1){
        openModal('겹치는 하이라이트',`<div class="cluster-options">${overlapping.map(({item,span,index})=>`<div class="cluster-scene"><button type="button" data-span="${esc(item.id)}:${index}"><span><small>하이라이트 · 원본 ${index+1}구간</small><strong>${esc(item.title)}</strong><em>${time(span.start)}–${time(span.end)} · ${length(span.end-span.start)}</em></span>${icon('chev')}</button><a class="text-button" href="${esc(vodURL(span.start))}" target="_blank" rel="noopener">${time(span.start)}부터 원본 보기 ${icon('out')}</a></div>`).join('')}</div>`);
        return;
      }
    }
    if(target.hasAttribute('data-zoom-time')&&zoomCluster(target,event))return;
    if(target.dataset.sceneCluster){const ids=JSON.parse(target.dataset.sceneCluster),items=allItems.filter(item=>ids.includes(item.id));openModal(`가까이 모인 장면 ${items.length}개`,`<div class="cluster-options">${items.map(item=>`<div class="cluster-scene"><button type="button" data-moment="${esc(item.id)}"><span><small>${kindLabel(item)}</small><strong>${esc(item.title)}</strong><em>${esc(item.description||'')}</em></span>${icon('arrow')}</button><a class="text-button" href="${esc(vodURL(item.start))}" target="_blank" rel="noopener">${icon('play')} ${time(item.start)}부터 원본 보기</a></div>`).join('')}</div>`);return;}
    if(target.dataset.member){collaborationView.openMember(target.dataset.period,target.dataset.member);return;}
    if(target.dataset.bookmark){toggleBookmark(target.dataset.bookmark);return;}
    if(target.dataset.point){const owner=allItems.find(item=>item.id===target.dataset.owner)||baseMoments.find(item=>points(item).some(p=>p.id===target.dataset.point)),point=owner&&points(owner).find(p=>p.id===target.dataset.point);if(point){if(state.selected===owner.id&&state.point===point.id){state.point=null;renderSelection();}else selectMoment(owner.id,{time:point.time,point:point.id,openContext:true});}return;}
    if(target.dataset.span){const split=target.dataset.span.lastIndexOf(':'),id=target.dataset.span.slice(0,split),index=Number(target.dataset.span.slice(split+1)),item=allItems.find(item=>item.id===id),span=sourceSpans(item||{})[index];if(span){if(target.closest('#modal'))closeModal();selectMoment(id,{time:span.start,openContext:true});}return;}
    if(target.dataset.moment){const inModal=Boolean(target.closest('#modal'));if(inModal)closeModal();selectMoment(target.dataset.moment,{...(target.hasAttribute('data-start')?{time:Number(target.dataset.start)}:{}),scroll:Boolean(target.closest('#momentGrid'))});return;}
    if(target.dataset.chapter){if(target.closest('#modal'))closeModal();if(target.dataset.chapter==='all'||(target.closest('#hierarchyBands')&&state.chapter===target.dataset.chapter&&state.topic==='all')){clearScopeSelection({fromBand:target.closest('#hierarchyBands')?{chapter:target.dataset.chapter}:null});return;}state.chapter=target.dataset.chapter;state.topic='all';renderCards();renderOverview();setText('scopeStatus',`${activeScope()?.title} 선택, ${filtered().length}개 장면`);return;}
    if(target.dataset.topic){const topic=data.topics.find(v=>v.id===target.dataset.topic);if(!topic)return;if(target.closest('#modal'))closeModal();if(target.closest('#hierarchyBands')&&state.topic===topic.id){clearScopeSelection({fromBand:{topic:topic.id}});return;}state.topic=topic.id;state.chapter=topic.parent_id||topic.chapter||'all';renderCards();renderOverview();setText('scopeStatus',`${topic.title} 선택, ${filtered().length}개 장면`);return;}
    if(target.dataset.filter){state.filter=target.dataset.filter;renderCards();renderOverview();return;}
    if(target.dataset.density){state.density=target.dataset.density;$('densityBar').querySelectorAll('[data-density]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.density===state.density)));renderCards();return;}
    if(target.dataset.action==='reset-filters'){state.filter='all';state.chapter='all';state.topic='all';state.query='';$('searchInput').value='';renderCards();renderOverview();$('searchInput').focus();}
    if(target.dataset.action==='show-results'){if(state.editor)setEditor(false);$('momentsHeading').setAttribute('tabindex','-1');$('momentsHeading').focus({preventScroll:true});$('moments').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}
    if(target.dataset.action==='collaboration')showCollaboration();
  });
  document.addEventListener('error',event=>{
    const src=event.target?.tagName==='IMG'?event.target.currentSrc||event.target.src:'';
    if(!src||unavailableImages.has(src)||!allItems.some(item=>item.image?.src===src))return;
    unavailableImages.add(src);renderSelection();
  },true);
  $('clearScope').addEventListener('click',()=>clearScopeSelection());
  $('contextToggle').addEventListener('click',()=>setContextOpen($('sceneContext').hidden));
  $('searchInput').addEventListener('input',event=>{state.query=event.target.value;renderCards();renderOverview();});
  const previewTarget=node=>node?.closest?.('.hint-entry, #overviewMarkers .map-marker, #highlightAnchors .highlight-anchor, #highlightRows .range-hit');
  $('mapCanvas').addEventListener('pointerover',event=>{
    const entry=previewTarget(event.target);
    if(!entry||entry.contains(event.relatedTarget))return;
    $('mapThumbnails').removeAttribute('data-hints-dismissed');
    rangePreview.hover=previewIds(entry); updateRangePreview();
  });
  $('mapCanvas').addEventListener('pointerout',event=>{
    const entry=previewTarget(event.target);
    if(!entry||entry.contains(event.relatedTarget))return;
    rangePreview.hover=previewIds(previewTarget(event.relatedTarget)); updateRangePreview();
  });
  $('mapCanvas').addEventListener('focusin',event=>{
    $('mapThumbnails').removeAttribute('data-hints-dismissed');
    rangePreview.focus=previewIds(previewTarget(event.target)); updateRangePreview();
  });
  $('mapCanvas').addEventListener('focusout',event=>{
    rangePreview.focus=previewIds(previewTarget(event.relatedTarget)); updateRangePreview();
  });
  $('mapCanvas').addEventListener('keydown',event=>{
    if(event.key!=='Escape'||![rangePreview.hover,rangePreview.focus,rangePreview.pinned].some(ids=>ids?.length))return;
    rangePreview.hover=null;rangePreview.focus=null;rangePreview.pinned=[];
    if(event.target.closest('#highlightRows'))$('mapScroll').focus({preventScroll:true});
    updateRangePreview();
    $('mapThumbnails').setAttribute('data-hints-dismissed','true');
    event.preventDefault();event.stopPropagation();
  });
  $('clearSearch').addEventListener('click',()=>{state.query='';$('searchInput').value='';renderCards();renderOverview();$('searchInput').focus();});
  $('bookmarkButton').addEventListener('click',()=>toggleBookmark(state.selected));
  $('editorToggle').addEventListener('click',()=>setEditor(false,{navigate:true}));
  $('viewWatch').addEventListener('click',()=>setEditor(false,{navigate:true}));
  $('viewEditor').addEventListener('click',()=>setEditor(true,{navigate:true}));
  $('themeButton').addEventListener('click',()=>setTheme(state.theme==='light'?'dark':'light'));
  $('prevMoment').addEventListener('click',()=>step(-1)); $('nextMoment').addEventListener('click',()=>step(1));
  $('shareButton').addEventListener('click',()=>{persistLocation();copy(location.href,'선택한 장면 링크를 복사했어요.');});
  $('outlineToggle').addEventListener('click',()=>openModal('방송 목차',`<div class="outline-dialog"><button class="text-button" type="button" data-chapter="all">방송 전체 보기 ${icon('arrow')}</button><nav class="chapter-tree" aria-label="D1 큰 활동과 D2 세부 주제">${$('flowContext').innerHTML}</nav></div>`));
  $('imageProvenance').addEventListener('click',()=>{
    const item=selected();
    const note=isImageExample(item)?'공개 시청자 클립 썸네일을 디자인 배치에 사용한 예시입니다. 이 하이라이트 시각에서 추출한 화면은 아닙니다.':item?.image?.role==='original_saved_highlight_image'?'결과에 저장된 하이라이트 대표 이미지입니다.':item?.kind==='clip'?'시청자 클립에 제공된 썸네일입니다.':item?.image?.caption||'';
    openModal('이 이미지의 출처',hasArtwork(item)?`<p><strong>${esc(imageLabel(item))}</strong></p><p>${esc(item.image.alt||item.title)}</p>${Number.isFinite(item.image.source_sec)?`<p>대표 이미지 시각: ${time(item.image.source_sec)}<br>원본 구간 시작: ${time(item.start)}</p>`:''}${note?`<p class="modal-note">${esc(note)}</p>`:''}`:'<p>대표 이미지가 제공되지 않았습니다.</p>');
  });
  $('summaryToggle').addEventListener('click',()=>{const summary=data.metadata?.summaryParagraphs||data.summary||'';const paragraphs=Array.isArray(summary)?summary:typeof summary==='string'?summary.split(/\n\s*\n/):[];openModal('방송 전체 요약',paragraphs.length?paragraphs.map(p=>`<p>${esc(typeof p==='string'?p:p.text||p.content||'')}</p>`).join(''):'<p>방송 전체 요약이 제공되지 않았습니다.</p>');});
  $('demoButton').addEventListener('click',()=>openModal('보고서 정보',`<p>하이라이트 ${data.highlights.length}개 · 핵심 지점 ${data.points.length}개 · 시청자 클립 ${clips.length}개</p>${data.previewMeta?.imageNotice?`<p>${esc(data.previewMeta.imageNotice)}</p>`:''}<p>북마크와 편집 도구의 저장은 이 브라우저에 보관됩니다.</p><p class="modal-note">${esc(data.sourceContract?.sourceSchema || '')} · ${esc(data.sourceContract?.pageSchema || '')}</p>`));
  $('shortcutsButton').addEventListener('click',()=>openModal('탐색 단축키','<dl class="shortcut-list"><div><dt><kbd>/</kbd></dt><dd>장면 검색</dd></div><div><dt><kbd>←</kbd><kbd>→</kbd></dt><dd>이전·다음 장면</dd></div><div><dt><kbd>B</kbd></dt><dd>북마크</dd></div><div><dt><kbd>E</kbd></dt><dd>편집 도구 열기·닫기</dd></div><div><dt><kbd>Esc</kbd></dt><dd>안내 닫기 · 범위 선택 해제</dd></div><div><dt><kbd>Ctrl</kbd> / <kbd>Alt</kbd> + 휠</dt><dd>시간 지도 확대·축소</dd></div><div><dt><kbd>Shift</kbd> + 휠</dt><dd>시간 지도 가로 이동</dd></div></dl><p class="modal-note">입력 중에는 동작하지 않습니다. 편집 도구 안에서는 기존 조작을 사용합니다.</p>'));
  $('modalClose').addEventListener('click',closeModal);$('modal').addEventListener('cancel',event=>{event.preventDefault();closeModal();});
  $('modal').addEventListener('click',event=>{if(event.target===$('modal')){const rect=$('modal').getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeModal();}});
  document.addEventListener('keydown',event=>{
    if(event.defaultPrevented||$('modal').open||event.target.matches('input,textarea,select,[contenteditable="true"]')||event.ctrlKey||event.metaKey||event.altKey)return;
    if(event.key==='Escape'&&activeScope()&&event.target.closest('#scopeContext,#mapScroll')){event.preventDefault();clearScopeSelection();return;}
    if(event.key==='/'){event.preventDefault();if(state.editor)setEditor(false);$('searchInput').focus();}
    else if(event.key.toLowerCase()==='b'){event.preventDefault();toggleBookmark(state.selected);}
    else if(event.key.toLowerCase()==='e'){event.preventDefault();setEditor(!state.editor,{navigate:true});}
    else if(event.key==='ArrowLeft'&&!event.target.closest('button,a,summary,#mapScroll')){event.preventDefault();step(-1);}
    else if(event.key==='ArrowRight'&&!event.target.closest('button,a,summary,#mapScroll')){event.preventDefault();step(1);}
  });
  window.addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==$('nativeEditorFrame').contentWindow||!event.data||typeof event.data!=='object')return;
    if(event.data.type==='loki-editor:ready'){
      if(event.data.reportURL!==environment.reportURL)return;
      editorReady=true;postEditor({type:'loki-editor:theme',theme:state.theme});inspectInEditor();
    }
    if(event.data.type==='loki-editor:height'&&Number.isFinite(event.data.height)){
      const frame=$('nativeEditorFrame'),style=getComputedStyle(frame);
      const borders=style.boxSizing==='border-box'?(parseFloat(style.borderTopWidth)||0)+(parseFloat(style.borderBottomWidth)||0):0;
      frame.style.height=`${Math.min(6000,Math.max(700,event.data.height))+borders}px`;
    }
    if(event.data.type==='loki-editor:selection'&&editorReady&&Number.isFinite(event.data.sec)){
      const sec=event.data.sec;
      const current=selected();
      const item=(current&&['point','clip'].includes(current.kind)&&validTime(current.start)&&Math.abs(current.start-sec)<0.5?current:null)||baseMoments.find(item=>item.kind==='highlight'&&sourceSpans(item).some(span=>span.start<=sec&&sec<span.end))||allItems.find(item=>validTime(item.start)&&Math.abs(item.start-sec)<0.5);
      if(item)selectMoment(item.id,{time:sec,fromEditor:true,announce:false});
    }
  });
  window.addEventListener('hashchange',()=>{if(readLocation()){renderSelection();setEditor(state.editor);}});
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(timelineController)timelineController.refreshWidth();else renderOverview();},150);});
  const title=data.title||data.metadata?.title||'방송 기록';
  setText('broadcastTitle',title);setText('headerStreamer',data.streamer||'방송 기록');
  document.title=`Loki · ${data.streamer?data.streamer+' · ':''}${title}`;
  $('broadcastMeta').innerHTML=`${data.streamer?`<span class="streamer-dot" aria-hidden="true"></span><strong>${esc(data.streamer)}</strong>`:''}${data.date?`<span>${esc(data.date.slice(0,10).replaceAll('-','. '))}</span>`:''}${data.game?`<span>${esc(data.game)}</span>`:''}${hasTimeline?`<span>${Math.floor(data.duration/3600)}시간 ${Math.floor(data.duration%3600/60)}분</span>`:''}${data.lokiVersion?`<span class="report-version">Loki ${esc(data.lokiVersion)}</span>`:''}`;
  setLink($('originalBroadcast'),originalURL());$('originalBroadcast').hidden=!originalURL();
  $('viewEditor').disabled=!editorAvailable;
  if(!editorAvailable)$('viewEditor').title='편집 도구에는 원본 영상 식별자가 필요합니다.';
  $('summaryToggle').disabled=!(data.summary || data.metadata?.summaryParagraphs?.length);
  setText('mapDuration',`00:00:00 — ${time(data.duration)}`);
  setText('reportOutline',`${baseMoments.filter(item=>item.kind==='highlight').length} 하이라이트 · ${clips.length} 시청자 클립`);
  setText('countAll',baseMoments.length);setText('countHighlights',baseMoments.filter(item=>item.kind==='highlight').length);setText('countPoints',baseMoments.filter(item=>item.kind==='point').length);setText('countClips',clips.length);
  setTheme(state.theme);renderSelection();
  if(hasTimeline)timelineController=window.createLokiTimelineController({scroller:$('mapScroll'),canvas:$('mapCanvas'),ruler:$('mapRuler'),seekLink:$('mapSeek'),seekHint:$('seekHint'),zoomIn:$('zoomIn'),zoomOut:$('zoomOut'),zoomReset:$('zoomReset'),duration:data.duration,formatTime:time,vodURL,getCurrentTime:()=>state.currentTime,onViewportChange:renderOverview});
  setEditor(state.editor);initializing=false;document.body.dataset.initReady='true';
  };
  window.LokiApp=Object.freeze({mount});
})();
