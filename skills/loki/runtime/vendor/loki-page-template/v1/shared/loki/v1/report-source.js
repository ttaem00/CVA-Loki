/* Shared, read-only entry for the viewer and native editor. No report is embedded here. */
(() => {
  'use strict';
  const PAGE_SCHEMA = 'loki_page.v1';
  const VIEWER_SCHEMA = 'viewer_editor_tools.v1';
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const text = value => typeof value === 'string' ? value : '';
  const finiteTime = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  class ReportError extends Error {
    constructor(code, message) { super(message); this.name = 'LokiReportError'; this.code = code; }
  }
  const fail = (code, message) => { throw new ReportError(code, message); };
  const safeURL = (value, base) => {
    if (typeof value !== 'string' || !value.trim()) return '';
    try {
      const url = new URL(value, base || document.baseURI);
      return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
    } catch (_) { return ''; }
  };
  const resolveAsset = (value, base) => {
    // Raster data URLs are allowed; executable/document data URLs are not images.
    if (typeof value === 'string' && /^data:image\/(?:png|jpeg|webp|gif|avif);base64,[a-z0-9+/=\s]+$/i.test(value)) return value;
    return safeURL(value, base);
  };
  const referenceFromURL = value => {
    const href = safeURL(value);
    if (!href) return null;
    const url = new URL(href), host = url.hostname, path = url.pathname;
    let match;
    if (host === 'chzzk.naver.com' && (match = /^\/video\/(\d{1,16})\/?$/.exec(path))) return {platform:'chzzk',id:match[1]};
    if (['youtube.com','www.youtube.com','youtu.be'].includes(host)) {
      const id = host === 'youtu.be' ? path.slice(1).replace(/\/$/,'') : path === '/watch' ? url.searchParams.get('v') : /^\/(?:live|embed)\/([^/]+)\/?$/.exec(path)?.[1];
      if (/^[A-Za-z0-9_-]{11}$/.test(id || '')) return {platform:'youtube',id};
    }
    if (['vod.sooplive.com','vod.sooplive.co.kr','vod.afreecatv.com','vod.afreecatv.co.kr'].includes(host) && (match = /^\/(?:player|PLAYER\/STATION)\/(\d{1,16})\/?$/.exec(path))) return {platform:'soop',id:match[1]};
    return null;
  };
  const sourceIdentity = packet => {
    const output = packet.viewerOutput || {}, meta = packet.metadata || {};
    const workId = text(output.video_no) || text(meta.videoNo);
    const href = text(output.source_url) || text(meta.sourceUrl);
    const parsed = referenceFromURL(href);
    const prefix = /^(youtube|soop)-(.+)$/.exec(workId);
    const platform = text(output.source_platform) || text(meta.sourcePlatform) || prefix?.[1] || parsed?.platform || (/^\d{1,16}$/.test(workId) ? 'chzzk' : '');
    const id = text(output.source_id) || text(meta.sourceId) || prefix?.[2] || workId || parsed?.id || '';
    return {platform, id, workId, url:safeURL(href)};
  };
  const sourceURLAt = (packet, seconds) => {
    if (!finiteTime(seconds)) return '';
    const {platform,id} = sourceIdentity(packet);
    const sec = Math.floor(seconds);
    if (platform === 'chzzk' && /^\d{1,16}$/.test(id)) return `https://chzzk.naver.com/video/${id}?currentTime=${sec}`;
    if (platform === 'youtube' && /^[A-Za-z0-9_-]{11}$/.test(id)) return `https://www.youtube.com/watch?v=${id}&t=${sec}s`;
    if (platform === 'soop' && /^\d{1,16}$/.test(id)) return `https://vod.sooplive.com/player/${id}?change_second=${sec}`;
    return '';
  };
  const normalize = (raw, options = {}) => {
    if (!object(raw)) fail('invalid_document', '보고서 JSON의 최상위 값은 객체여야 합니다.');
    // Bare public editor payloads are usable, but do not invent missing page metadata.
    const packet = raw.schema_version === VIEWER_SCHEMA ? {schema_version:PAGE_SCHEMA, viewerOutput:raw, metadata:{}} : raw;
    if (packet.schema_version !== PAGE_SCHEMA) fail('unsupported_page_schema', `지원하지 않는 페이지 형식입니다. ${PAGE_SCHEMA} JSON을 사용해 주세요.`);
    const output = packet.viewerOutput;
    if (!object(output) || output.schema_version !== VIEWER_SCHEMA) fail('unsupported_viewer_schema', `viewerOutput에는 ${VIEWER_SCHEMA} 출력이 필요합니다.`);
    if (packet.metadata != null && !object(packet.metadata)) fail('invalid_metadata', 'metadata는 객체여야 합니다.');
    if (packet.renderedOriginal != null && !object(packet.renderedOriginal)) fail('invalid_presentation', 'renderedOriginal은 객체여야 합니다.');
    if (output.duration_sec != null && !finiteTime(output.duration_sec)) fail('invalid_duration', 'duration_sec는 0 이상의 유한한 숫자여야 합니다.');
    for (const field of ['outline_projection','events','subtitles','chat_buckets','collaboration_periods','collaboration_candidates']) {
      if (output[field] != null && !Array.isArray(output[field])) fail('invalid_collection', `viewerOutput.${field}는 배열이어야 합니다.`);
    }
    for(const field of ['outline_projection','events','collaboration_periods']) {
      if(Array.isArray(output[field]) && output[field].some(item=>!object(item)))fail('invalid_entry',`viewerOutput.${field}의 항목은 객체여야 합니다.`);
    }
    if(packet.highlightImagePlacements!=null && (!Array.isArray(packet.highlightImagePlacements) || packet.highlightImagePlacements.some(item=>!object(item))))fail('invalid_images','highlightImagePlacements는 객체 배열이어야 합니다.');
    const metadata = packet.metadata || {}, chart = packet.renderedOriginal?.reportDocumentData;
    const same = (left, right, label) => {
      if (left != null && left !== '' && right != null && right !== '' && String(left) !== String(right)) fail('source_identity_mismatch', `${label}가 서로 다른 방송을 가리킵니다.`);
    };
    same(output.video_no, metadata.videoNo, 'metadata와 viewerOutput');
    same(output.source_platform, metadata.sourcePlatform, '원본 플랫폼');
    same(output.source_id, metadata.sourceId, '원본 영상 ID');
    same(output.duration_sec, metadata.durationSec, '방송 길이');
    if (chart != null && !object(chart)) fail('invalid_chart', 'reportDocumentData는 객체여야 합니다.');
    same(output.video_no, chart?.vodVideoNo, '차트와 viewerOutput');
    const identity = sourceIdentity(packet);
    const prefix = /^(youtube|soop)-(.+)$/.exec(text(output.video_no));
    if (prefix) { same(identity.platform,prefix[1],'작업 ID의 플랫폼'); same(identity.id,prefix[2],'작업 ID의 영상'); }
    else if (text(output.video_no) && identity.id) same(output.video_no,identity.id,'작업 ID와 원본 영상');
    for (const value of [output.source_url, metadata.sourceUrl]) {
      if (value == null || value === '') continue;
      const reference = referenceFromURL(value);
      if (!reference) fail('invalid_source_url', '원본 영상 주소는 CHZZK·YouTube·SOOP의 개별 다시보기 주소여야 합니다.');
      same(identity.platform,reference.platform,'원본 영상 주소의 플랫폼');
      same(identity.id,reference.id,'원본 영상 주소의 ID');
    }
    const reportURL = safeURL(options.reportURL || document.baseURI, document.baseURI);
    if (!reportURL) fail('invalid_report_url', '보고서는 HTTP 또는 HTTPS 주소에서 열어 주세요.');
    let assetBase = packet.asset_base == null ? new URL('.',reportURL).href : safeURL(packet.asset_base,reportURL);
    if (!assetBase) fail('invalid_asset_base', 'asset_base는 유효한 상대 경로나 HTTP(S) 주소여야 합니다.');
    const baseURL=new URL(assetBase);
    if(!baseURL.pathname.endsWith('/'))baseURL.pathname+='/';
    assetBase=baseURL.href;
    // The input remains untouched: IDs, raw strings, fractional spans, provenance,
    // optional/unknown fields and administrator edits are owned by the producer.
    return {packet,reportURL,assetBase};
  };
  const load = async input => {
    const reportURL = safeURL(input, document.baseURI);
    if (!reportURL) fail('invalid_report_url', '보고서 JSON 주소를 확인해 주세요. 로컬에서는 HTTP 서버로 열어 주세요.');
    let response;
    try { response = await fetch(reportURL,{credentials:'same-origin',headers:{Accept:'application/json'}}); }
    catch (_) { fail('network_error', '보고서 JSON을 읽지 못했습니다. 주소와 서버 연결을 확인해 주세요.'); }
    if (!response.ok) fail('http_error', `보고서 JSON을 읽지 못했습니다. HTTP ${response.status}`);
    let raw;
    try { raw = await response.json(); }
    catch (_) { fail('invalid_json', '이 주소에서 유효한 JSON을 받지 못했습니다. HTML 페이지나 깨진 JSON인지 확인해 주세요.'); }
    return normalize(raw,{reportURL:response.url || reportURL});
  };
  window.LokiReportSource = Object.freeze({load,normalize,sourceURLAt,sourceIdentity,resolveAsset,safeURL,ReportError,PAGE_SCHEMA,VIEWER_SCHEMA});
})();
