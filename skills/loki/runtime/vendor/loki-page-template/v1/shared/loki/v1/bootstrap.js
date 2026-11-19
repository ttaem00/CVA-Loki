/* One page URL selects one JSON; all presentation files are shared. */
(() => {
  'use strict';
  const scriptURL = new URL(document.currentScript.src);
  const runtimeBase = new URL('.', scriptURL).href;
  const runtimeVersion = scriptURL.searchParams.get('v') || '';
  const root = document.documentElement;
  const main = document.getElementById('top');
  const status = document.getElementById('reportStatus');
  const fail = error => {
    root.dataset.lokiStatus='error';
    main.setAttribute('aria-busy','false');
    status.hidden=false;
    status.setAttribute('role','alert');
    document.getElementById('reportStatusTitle').textContent='방송 기록을 불러오지 못했어요.';
    document.getElementById('reportStatusMessage').textContent=error?.name==='LokiReportError' ? error.message : '보고서 표시 중 문제가 생겼습니다. JSON 형식과 공용 파일 구성을 확인해 주세요.';
    document.getElementById('reportRetry').hidden=false;
    document.getElementById('reportRetry').onclick=()=>location.reload();
    document.dispatchEvent(new CustomEvent('loki:report-error',{detail:{code:error?.code || 'render_error'}}));
    console.error('[Loki]',error);
  };
  const query = new URL(location.href).searchParams;
  const input = query.has('data') ? query.get('data') : root.dataset.defaultReport || 'reports/default.json';
  root.dataset.lokiStatus='loading';
  window.LokiReportReady = window.LokiReportSource.load(input).then(environment => {
    environment.runtimeBase=runtimeBase;
    environment.runtimeVersion=runtimeVersion;
    const view=window.LokiReportAdapter.create(environment.packet,environment);
    // Measure the real layout before fitting the timeline, without exposing a
    // partially rendered report or zero-width geometry from display:none.
    root.dataset.lokiStatus='rendering';
    status.hidden=true;
    main.style.visibility='hidden';
    window.LokiApp.mount(view,environment);
    root.dataset.lokiStatus='ready';
    main.style.removeProperty('visibility');
    main.setAttribute('aria-busy','false');
    status.hidden=true;
    document.dispatchEvent(new CustomEvent('loki:report-ready',{detail:{reportURL:environment.reportURL}}));
    return environment;
  }).catch(error=>{fail(error);return null;});
})();
