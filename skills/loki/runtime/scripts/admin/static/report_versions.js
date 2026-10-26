(() => {
  'use strict';
  const base = new URLSearchParams(location.search).get('base') || '';
  const videoNo = base.split('_', 1)[0];
  const host = document.getElementById('reportVersions');
  if (!host || !/^\d+$/.test(videoNo)) return;
  const route = location.pathname === '/report-workspace' ? '/report-workspace' : '/report';
  const link = (version, label) => {
    const a = document.createElement('a');
    const query=new URLSearchParams({base:version.base}),current=new URLSearchParams(location.search);
    const type=current.get('type'),id=current.get('id') || '',span=current.get('span') || '0';
    if(['D1','D2','Point','Highlight'].includes(type)&&id&&id.length<=256&&!/[\x00-\x1f\x7f]/.test(id)&&/^\d{1,6}$/.test(span)){
      query.set('type',type);query.set('id',id);query.set('span',span);
    }
    a.href = `${route}?${query}`;
    a.textContent = label;
    return a;
  };
  async function load() {
    host.textContent = '요약 버전 확인 중…';
    const response = await fetch(`/api/report-catalog?q=${encodeURIComponent(videoNo)}&limit=100`);
    if (!response.ok) throw new Error('요약 버전을 불러오지 못했습니다.');
    const data = await response.json();
    const report = (data.reports || []).find(row => String(row.video_no) === videoNo);
    const versions = report?.summary_versions || [];
    if (!versions.length) { host.textContent = ''; return; }
    host.replaceChildren();
    const current = versions.find(row => row.base === base);
    const time = row => row.saved_at ? new Date(row.saved_at).toLocaleString('ko-KR') : '시간 미확인';
    const status = document.createElement('span');
    status.textContent = `${base === versions[0].base ? '최신 저장본' : '이전 요약 버전'} · ${current ? time(current) : '선택한 저장본'} `;
    host.append(status);
    if (base !== versions[0].base) host.append(link(versions[0], '최신 요약 열기'));
    if (versions.length > 1) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = `요약 버전 ${versions.length}개`;
      details.append(summary);
      versions.forEach((row, index) => {
        const p = document.createElement('p');
        const label = `${index === 0 ? '최신' : '이전'} · ${row.summary_label || '요약'} · ${time(row)}${row.base === base ? ' · 현재 열림' : ''}`;
        p.append(link(row, label)); details.append(p);
      });
      host.append(details);
    }
  }
  load().catch(error => {
    host.textContent = `${error.message} `;
    const retry = document.createElement('button');
    retry.type = 'button'; retry.textContent = '버전 다시 확인';
    retry.addEventListener('click', () => load().catch(() => { host.textContent = error.message; }));
    host.append(retry);
  });
})();
