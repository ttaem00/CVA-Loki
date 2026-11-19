/* Load one report before the unchanged native editor captures its data. */
(() => {
  'use strict';

  const bootURL = new URL(document.currentScript.src);
  const runtimeBase = new URL('./', bootURL);
  const buildVersion = bootURL.searchParams.get('v');
  const params = new URLSearchParams(location.search);
  const dataNode = document.getElementById('viewerEditorToolsData');
  const chartNode = document.getElementById('reportDocumentData');
  const main = document.querySelector('.main.bleed-inner');
  const status = document.getElementById('nativeEditorLoadState');
  const retry = document.getElementById('nativeEditorRetry');
  let reportURL = '';

  document.body.dataset.nativeEditorDataStatus = 'loading';
  document.documentElement.dataset.theme = params.get('theme') === 'dark' ? 'dark' : 'light';
  retry.addEventListener('click', () => location.reload());

  const text = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = value == null ? '' : String(value);
  };

  function renderReportMetadata(packet, source) {
    const metadata = packet.metadata || {};
    const payload = packet.viewerOutput;
    const videoNo = payload.video_no == null ? '' : String(payload.video_no);
    let sourceURL = source.safeURL(payload.source_url || metadata.sourceUrl, reportURL);
    const sourceAtStart = source.sourceURLAt(packet, 0);
    if (sourceAtStart) {
      // The native companion accepts canonical platform hosts. Retain the raw
      // source_url in the payload; use the shared resolver for the same video.
      const canonical = new URL(sourceAtStart);
      for (const key of ['currentTime', 't', 'change_second']) canonical.searchParams.delete(key);
      sourceURL = canonical.href;
    }
    const title = typeof metadata.title === 'string' ? metadata.title : '';
    document.title = title ? `${title} | Loki 편집 도구` : 'Loki 편집 도구';
    text('nativeReportTitle', title);
    text('nativeReportCrumb', [metadata.streamer, videoNo ? `VOD ${videoNo}` : ''].filter(Boolean).join(' · '));
    text('nativeReportDate', metadata.date);
    text('nativeReportDuration', metadata.durationLabel);
    text('nativeReportGame', metadata.game);

    const entry = document.querySelector('[data-editor-entry]');
    entry.dataset.editorVideoNo = videoNo;
    if (sourceURL) entry.dataset.editorSourceUrl = sourceURL;
    else entry.removeAttribute('data-editor-source-url');
    if (payload.source_platform === 'chzzk' && sourceURL) entry.dataset.editorChzzkUrl = sourceURL;
    else entry.removeAttribute('data-editor-chzzk-url');
    document.querySelectorAll('a.top-vod-link, [data-editor-entry-primary]').forEach(link => {
      if (sourceURL) {
        link.href = sourceURL;
        link.removeAttribute('aria-disabled');
      } else {
        link.removeAttribute('href');
        link.setAttribute('aria-disabled', 'true');
      }
    });

    const summary = document.getElementById('nativeReportSummary');
    const paragraphs = Array.isArray(metadata.summaryParagraphs)
      ? metadata.summaryParagraphs.filter(value => typeof value === 'string')
      : typeof metadata.summary === 'string' && metadata.summary ? [metadata.summary] : [];
    for (const value of paragraphs) {
      const paragraph = document.createElement('p');
      paragraph.textContent = value;
      summary.appendChild(paragraph);
    }
    summary.closest('.broadcast-summary').hidden = !paragraphs.length;

    const identity = document.getElementById('summaryPipelineIdentity');
    const rawAttributes = metadata.sourceIdentityRawAttributes;
    if (rawAttributes && typeof rawAttributes === 'object') {
      for (const [name, value] of Object.entries(rawAttributes)) {
        if (/^data-[a-z0-9-]+$/.test(name) && typeof value === 'string') identity.setAttribute(name, value);
      }
    }
    if (metadata.lokiVersion) identity.textContent = `Loki ${metadata.lokiVersion}`;
  }

  function loadScript(path) {
    return new Promise((resolve, reject) => {
      const url = new URL(path, runtimeBase);
      if (buildVersion) url.searchParams.set('v', buildVersion);
      const script = document.createElement('script');
      script.src = url.href;
      script.async = false;
      script.onload = resolve;
      script.onerror = () => reject(new Error('편집 도구 파일을 불러오지 못했습니다.'));
      document.head.appendChild(script);
    });
  }

  function showError(error) {
    main.hidden = true;
    main.setAttribute('aria-busy', 'false');
    document.body.dataset.nativeEditorDataStatus = 'error';
    status.hidden = false;
    status.setAttribute('role', 'alert');
    text('nativeEditorLoadTitle', '편집 자료를 불러오지 못했습니다.');
    text('nativeEditorLoadMessage', error?.message || '보고서 주소와 JSON 데이터를 확인해 주세요.');
    retry.hidden = false;
    if (window.parent !== window) {
      window.parent.postMessage({
        channel: 'loki-native-editor.v1', type: 'loki-editor:error',
        reportURL, message: document.getElementById('nativeEditorLoadMessage').textContent
      }, location.origin === 'null' ? '*' : location.origin);
    }
  }

  async function boot() {
    const source = window.LokiReportSource;
    if (!source?.load) throw new Error('공용 보고서 로더를 불러오지 못했습니다.');
    const requested = params.get('data') || document.body.dataset.defaultReport;
    reportURL = new URL(requested, document.baseURI).href;
    const loaded = await source.load(reportURL);
    const packet = loaded.packet;
    reportURL = String(loaded.reportURL || reportURL);
    if (!packet?.viewerOutput || packet.viewerOutput.schema_version !== 'viewer_editor_tools.v1') {
      throw new Error('이 보고서에는 지원되는 편집 자료가 포함되어 있지 않습니다.');
    }

    // Keep the public projection and chart values intact. The original runtime
    // parses these nodes once, so neither can be swapped after initialization.
    const chart = packet.renderedOriginal?.reportDocumentData;
    dataNode.textContent = JSON.stringify(packet.viewerOutput);
    chartNode.textContent = JSON.stringify(chart || {});
    document.getElementById('summaryEvidencePackData').textContent = JSON.stringify(
      packet.renderedOriginal?.summaryEvidencePackData || {}
    );
    const sourceIdentity = source.sourceIdentity(packet);
    dataNode.lokiReportContext = Object.freeze({
      reportURL, assetBase: String(loaded.assetBase || ''),
      schemaVersion: packet.schema_version || '',
      sourcePlatform: sourceIdentity.platform,
      sourceId: sourceIdentity.id,
      sourceUrl: sourceIdentity.url
    });
    document.body.dataset.nativeReportUrl = reportURL;
    renderReportMetadata(packet, source);

    const hasChart = Array.isArray(chart?.labels) && chart.labels.length > 0;
    document.getElementById('chatChart').closest('.chart-wrap').hidden = !hasChart;
    const emptyChart = document.getElementById('nativeChartEmpty');
    emptyChart.hidden = hasChart;
    emptyChart.textContent = chart
      ? '표시할 채팅 밀도 구간이 없습니다.'
      : '이 보고서에는 채팅 밀도 차트 데이터가 포함되어 있지 않습니다.';

    // Make geometry measurable for native auto-fit while the loading message
    // remains visible. Original event handlers and chart options own behavior.
    main.style.visibility = 'hidden';
    main.hidden = false;
    await loadScript('assets/vendor/chart.umd.min.js');
    await loadScript('assets/report-document.js');
    await loadScript('assets/viewer-editor-tools.js');
    await loadScript('native-editor-bridge.js');
    main.style.removeProperty('visibility');
    main.setAttribute('aria-busy', 'false');
    document.body.dataset.nativeEditorDataStatus = 'ready';
    status.hidden = true;
  }

  boot().catch(showError);
})();
