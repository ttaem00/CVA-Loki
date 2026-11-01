(() => {
  'use strict';

  // Read-only URL context. Editing, save outcomes and undo remain with their owners.
  const surfaces = ['read', 'source', 'share'];
  function view(query, source = new URLSearchParams(location.search)) {
    const tab = source.get('tab');
    if (surfaces.includes(tab)) query.set('tab', tab);
    return query;
  }
  function list(query, source = new URLSearchParams(location.search)) {
    for (const key of ['q', 'streamer']) {
      const value = source.get(key);
      if (value && !(key === 'streamer' && value === '__all__')) query.set(key, value);
    }
    return query;
  }

  function scene(source = new URLSearchParams(location.search)) {
    const query = new URLSearchParams();
    const type = source.get('type'), id = source.get('id') || '', span = source.get('span') || '0';
    if (['D1', 'D2', 'Point', 'Highlight'].includes(type) && id && id.length <= 256
        && !/[\x00-\x1f\x7f]/.test(id) && /^\d{1,6}$/.test(span)) {
      query.set('type', type); query.set('id', id); query.set('span', span);
    }
    return query;
  }

  function context(base, selected = scene()) {
    const query = list(new URLSearchParams({base}));
    for (const key of ['type', 'id', 'span']) if (selected.has(key)) query.set(key, selected.get(key));
    return query;
  }

  function report(path, base, selected = scene()) {
    return `${path}?${view(context(base, selected))}`;
  }

  function reader(path, base, selected, from) {
    const query = context(base, selected);
    if (path === '/vod-outputs') {
      query.set('base', base.split('_', 1)[0]);
      query.set('report_base', base);
    }
    query.set('from', from === 'workspace' ? 'workspace' : 'detail');
    view(query);
    return `${path}?${query}`;
  }

  function connectReader() {
    const root = document.querySelector('[data-report-reader]');
    if (!root) return;
    const current = new URLSearchParams(location.search), requested = current.get('report_base');
    const base = location.pathname === '/vod-outputs' && requested?.split('_', 1)[0] === current.get('base')
      ? requested : current.get('base');
    if (!base) return;
    const workspace = current.get('from') === 'workspace';
    const back = root.querySelector('[data-report-return]');
    back.href = report(workspace ? '/report-workspace' : '/report', base);
    back.textContent = workspace ? '← 선택한 장면으로 돌아가기' : '← 리포트로 돌아가기';
    const library = root.querySelector('[data-report-list]');
    if (library) library.href = `/reports?${list(new URLSearchParams())}`;
    document.querySelectorAll('form[data-report-context]').forEach(form => {
      const retained = list(scene());
      retained.set('from', workspace ? 'workspace' : 'detail');
      view(retained);
      for (const [key, value] of retained) {
        const input = document.createElement('input');
        input.type = 'hidden'; input.name = key; input.value = value; form.append(input);
      }
    });
  }

  function revisionTitle(row) {
    const date = new Date(row.created_at || row.saved_at || '');
    return `로컬 저장본 · ${Number.isNaN(date.getTime()) ? '저장 시각 미확인' : date.toLocaleString('ko-KR')}`;
  }

  function revisionSummary(row) {
    const outline = row.outline || {}, ranges = outline.ranges || [];
    const highlights = row.qg1_review_packet?.editorial_highlights || outline.highlights || [];
    return `저장 내용: 큰 흐름 ${ranges.filter(item => item.level === 'D1').length} · 세부 흐름 ${ranges.filter(item => item.level === 'D2').length} · Point ${(outline.points || []).length} · Highlight ${highlights.length}`;
  }

  window.ReportNavigation = Object.freeze({list, scene, view, report, reader, connectReader, revisionTitle, revisionSummary});
})();
