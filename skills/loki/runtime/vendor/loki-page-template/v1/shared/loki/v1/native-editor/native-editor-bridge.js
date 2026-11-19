/*
 * Thin same-origin embedding bridge; the supplied native runtime is unmodified.
 * Inspection uses chzz-result-inspect, and never clicks a source/video link.
 */
(() => {
  'use strict';
  const CHANNEL = 'loki-native-editor.v1';
  const params = new URLSearchParams(location.search);
  let theme = params.get('theme') === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = theme;
  const embedded = window.parent !== window;
  const targetOrigin = location.origin === 'null' ? '*' : location.origin;
  let initialized = false;
  let duration = 0;
  let videoNo = '';
  let lastHeight = 0;
  let heightFrame = 0;
  let currentRequestId = null;
  let inspectionTime = null;
  let pendingInspect = null;
  const emit = (type, detail = {}) => {
    if (embedded) window.parent.postMessage({ channel: CHANNEL, type, ...detail }, targetOrigin);
  };
  const validInspection = detail => {
    if (!detail || typeof detail.sec !== 'number' || !Number.isFinite(detail.sec) || detail.sec < 0) return null;
    if (duration > 0 && detail.sec > duration) return null;
    const value = { sec: detail.sec };
    if (typeof detail.highlightFragment === 'string' && /^editorial-highlight-[a-zA-Z0-9_-]{1,220}$/.test(detail.highlightFragment)) {
      value.highlightFragment = detail.highlightFragment;
    }
    if (typeof detail.requestId === 'string' && detail.requestId.length <= 100) value.requestId = detail.requestId;
    return value;
  };
  const activateEditor = () => {
    const button = document.getElementById('cva-result-tab-editor');
    if (button && button.getAttribute('aria-selected') !== 'true') button.click();
  };
  const reportHeight = () => {
    heightFrame = 0;
    const root = document.querySelector('.main.bleed-inner');
    if (!root) return;
    // Do not measure documentElement.scrollHeight: an iframe's previous height
    // would prevent shrinking when native evidence/details are collapsed.
    const height = Math.ceil(Math.max(root.scrollHeight, root.getBoundingClientRect().height));
    if (height > 0 && Math.abs(height - lastHeight) > 1) {
      lastHeight = height;
      emit('loki-editor:height', { height });
    }
  };
  const scheduleHeight = () => {
    if (!heightFrame) heightFrame = requestAnimationFrame(reportHeight);
  };
  const applyTheme = value => {
    if (value !== 'light' && value !== 'dark') return false;
    theme = value;
    document.documentElement.dataset.theme = value;
    return true;
  };
  const applyChartPresentation = () => {
    const canvas = document.getElementById('chatChart');
    const chart = canvas && window.Chart?.getChart?.(canvas);
    if (!chart?.options || typeof chart.update !== 'function') return;
    const palette = getComputedStyle(document.documentElement);
    const token = (name, fallback) => palette.getPropertyValue(name).trim() || fallback;
    const muted = token('--native-muted', '#A9B6C7');
    const text = token('--text', '#EEF1F5');
    const border = token('--border', '#344155');
    const grid = token('--border-soft', '#28364A');
    const options = chart.options;
    for (const scale of Object.values(options.scales || {})) {
      if (scale.ticks) scale.ticks.color = muted;
      if (scale.grid) scale.grid.color = grid;
    }
    if (options.plugins?.legend?.labels) options.plugins.legend.labels.color = muted;
    if (options.plugins?.tooltip) {
      options.plugins.tooltip.backgroundColor = token('--surface-2', '#1D2B3C');
      options.plugins.tooltip.borderColor = border;
      options.plugins.tooltip.titleColor = text;
      options.plugins.tooltip.bodyColor = text;
    }
    // Reuse the native instance and all its data, axes and event callbacks.
    chart.update('none');
  };
  // Presentation only. Keep native links, listeners, raw data and track geometry.
  const tidyPresentation = () => {
    const workspace = document.querySelector('.viewer-editor-tools');
    const head = workspace?.querySelector('.viewer-editor-head');
    const guide = workspace?.querySelector('[data-editor-companion-workspace-guide]');
    if (head && guide && guide.parentElement !== head) head.appendChild(guide);
    const heading = head?.querySelector('.viewer-editor-title');
    if (heading?.textContent === '편집자 워크스페이스') heading.textContent = '편집 도구';
    const helpSummary = guide?.querySelector('[data-editor-companion-help] > summary');
    if (helpSummary?.textContent === '영상 연결 도움말') helpSummary.textContent = '연결 도움말';
    document.querySelectorAll('[data-real-heatmap-row] .viewer-editor-density-label').forEach(label => {
      const title = label.querySelector('strong');
      if (title?.textContent === '먼저 볼 구간') title.textContent = '근거 밀도';
      const copy = label.querySelector(':scope > span');
      if (!copy) return;
      const details = document.createElement('details');
      details.className = 'native-measure-help';
      const summary = document.createElement('summary');
      summary.setAttribute('aria-label', '근거 밀도 표시 기준');
      summary.textContent = 'i';
      details.append(summary, copy);
      label.appendChild(details);
    });
    document.querySelectorAll('.viewer-editor-density-label > span').forEach(copy => {
      const label = copy.parentElement;
      const heading = label.querySelector('strong');
      if (heading) heading.title = copy.textContent;
    });
  };
  const inspect = detail => {
    const request = validInspection(detail);
    if (!request) return;
    if (!initialized) { pendingInspect = request; return; }
    activateEditor();
    // Reveal an externally requested scene through the original select listener.
    const filter = document.getElementById('viewerEditorFilter');
    if (filter && filter.value) {
      filter.value = '';
      filter.dispatchEvent(new Event('change', { bubbles: true }));
    }
    currentRequestId = request.requestId || null;
    inspectionTime = request.sec;
    document.dispatchEvent(new CustomEvent('chzz-result-inspect', {
      detail: { sec: request.sec, highlightFragment: request.highlightFragment || '' }
    }));
    currentRequestId = null;
    inspectionTime = null;
    scheduleHeight();
  };
  const ready = payload => {
    if (initialized) return;
    duration = Number(payload?.duration_sec) || 0;
    videoNo = String(payload?.video_no || '');
    // Native data is emitted before its remaining listeners are installed.
    // A microtask lets that original initialization finish before inspection.
    queueMicrotask(() => {
      if (initialized) return;
      initialized = true;
      activateEditor();
      applyChartPresentation();
      document.body.dataset.nativeEditorReady = 'true';
      const reportContext = document.getElementById('viewerEditorToolsData')?.lokiReportContext || {};
      emit('loki-editor:ready', {
        videoNo, duration, theme, supportedThemes: ['light', 'dark'],
        runtime: 'viewer_editor_tools.v1', source: 'shared_report_json',
        reportURL: reportContext.reportURL || '',
        sourcePlatform: reportContext.sourcePlatform || '',
        sourceId: reportContext.sourceId || '',
        sourceUrl: reportContext.sourceUrl || ''
      });
      if (pendingInspect) {
        const request = pendingInspect;
        pendingInspect = null;
        inspect(request);
      }
      scheduleHeight();
    });
  };
  document.addEventListener('chzz-result-data', event => ready(event.detail));
  document.addEventListener('chzz-result-selection', event => {
    const detail = event.detail;
    if (!detail || !Number.isFinite(detail.sec)) return;
    // The original first render selects its first event before an inspection.
    // Do not send that transient default back over the requested viewer time.
    if (inspectionTime !== null && Math.floor(detail.sec) !== Math.floor(inspectionTime)) return;
    emit('loki-editor:selection', {
      sec: detail.sec, videoNo: String(detail.videoNo || videoNo),
      requestId: currentRequestId
    });
    scheduleHeight();
  });
  window.addEventListener('message', event => {
    if (!embedded || event.source !== window.parent || event.origin !== location.origin) return;
    const message = event.data;
    if (!message || typeof message !== 'object') return;
    if (message.channel && message.channel !== CHANNEL) return;
    if (message.type === 'loki-editor:inspect') inspect(message);
    if (message.type === 'loki-editor:resize') {
      window.dispatchEvent(new Event('resize'));
      scheduleHeight();
    }
    if (message.type === 'loki-editor:theme' && applyTheme(message.theme)) {
      emit('loki-editor:theme', { theme, supportedThemes: ['light', 'dark'] });
    }
  });
  const start = () => {
    activateEditor();
    if (params.has('t') && params.get('t') !== '') {
      pendingInspect = validInspection({ sec: Number(params.get('t')), highlightFragment: params.get('highlight') || '' });
    }
    const data = document.getElementById('viewerEditorToolsData');
    if (data?.chzzResultPayload) ready(data.chzzResultPayload);
    else document.dispatchEvent(new Event('chzz-result-data-request'));
    const root = document.querySelector('.main.bleed-inner');
    tidyPresentation();
    const axis = document.getElementById('viewerEditorAxis');
    if (axis && typeof MutationObserver === 'function') {
      new MutationObserver(tidyPresentation).observe(axis, { childList: true, subtree: true });
    }
    if (root && typeof ResizeObserver === 'function') new ResizeObserver(scheduleHeight).observe(root);
    document.addEventListener('toggle', scheduleHeight, true);
    document.addEventListener('load', scheduleHeight, true);
    document.fonts?.ready.then(scheduleHeight);
    window.addEventListener('resize', scheduleHeight);
    window.addEventListener('hashchange', () => { activateEditor(); scheduleHeight(); });
    scheduleHeight();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
