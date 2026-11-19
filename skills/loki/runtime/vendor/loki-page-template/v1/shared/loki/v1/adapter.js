/*
 * Read-only display adapter for a viewer_editor_tools.v1 output.
 * This payload is not the native analysis report or a complete schema validation.
 * Original objects, text, identities, fractional spans, and image metadata remain
 * available unchanged; UI aliases below do not manufacture native report fields.
 */
(() => {
  'use strict';
  const create = (packet, environment = {}) => {
  if (!packet || typeof packet !== 'object' || !packet.viewerOutput) throw new TypeError('viewerOutput is required');
  const source = window.LokiReportSource;
  const assetBase = environment.assetBase || document.baseURI;
  const asset = value => source.resolveAsset(value, assetBase);
  const link = value => source.safeURL(value, environment.reportURL || document.baseURI);
  const output = packet.viewerOutput;
  const meta = packet.previewMeta || {};
  const metadata = packet.metadata || {};
  const rendered = packet.renderedOriginal || {};
  const array = value => Array.isArray(value) ? value : [];
  const text = value => typeof value === 'string' ? value : '';
  const issues = [];
  const issue = (code, path) => issues.push({ code, path });
  const seconds = (value, path) => {
    if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return value;
    issue('unusable_seconds', path);
    return null;
  };
  const timestamp = (value, path) => {
    if (typeof value === 'number') return seconds(value, path);
    const match = typeof value === 'string' && /^(\d{2,}):([0-5]\d):([0-5]\d)$/.exec(value);
    if (!match) { issue('unusable_timestamp', path); return null; }
    const total = Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
    if (!Number.isSafeInteger(total)) { issue('unusable_timestamp', path); return null; }
    return total;
  };
  const sourceIdentity = source.sourceIdentity(packet);
  const sourceUrl = sourceIdentity.url || source.sourceURLAt(packet, 0);
  const vodUrlAt = value => source.sourceURLAt(packet, value);
  const outline = array(output.outline_projection);
  const rawHighlights = outline.filter(entry => entry.kind === 'editorial_highlight');
  const rawPoints = outline.filter(entry => entry.kind === 'outline_point');
  const chapterEntries = outline.filter(entry => entry.kind === 'outline_range' && entry.level === 'D1');
  const topicEntries = outline.filter(entry => entry.kind === 'outline_range' && entry.level === 'D2');
  const rangeView = (entry, level) => ({
    id: text(entry.id), title: text(entry.title), content: text(entry.content), short: text(entry.title),
    start: seconds(entry.start_sec, `outline_projection.${entry.id}.start_sec`),
    end: seconds(entry.end_sec, `outline_projection.${entry.id}.end_sec`),
    originalStart: entry.start, originalEnd: entry.end, level,
    parent_id: text(entry.parent_id), chapter: text(entry.parent_id),
    ...(Object.hasOwn(entry, 'image') ? {image: entry.image} : {}), raw: entry
  });
  const chapters = chapterEntries.map(entry => rangeView(entry, 'D1'));
  const topics = topicEntries.map(entry => rangeView(entry, 'D2'));
  const chapterAt = value => typeof value === 'number' && Number.isFinite(value)
    ? chapters.find(entry => entry.start !== null && entry.end !== null && value >= entry.start && value < entry.end)?.id || '' : '';
  const topicAt = value => typeof value === 'number' && Number.isFinite(value)
    ? topics.find(entry => entry.start !== null && entry.end !== null && value >= entry.start && value < entry.end)?.id || '' : '';
  const pointViews = rawPoints.map(entry => {
    const time = timestamp(entry.timestamp, `outline_projection.${entry.id}.timestamp`);
    return {
      id: text(entry.id), time, timestamp: entry.timestamp,
      projectedStart: entry.start_sec, title: text(entry.title), content: text(entry.content),
      story: text(entry.story_content), storyTitle: text(entry.story_title),
      story_ref: text(entry.story_ref), d1_ref: text(entry.d1_ref), d2_ref: text(entry.d2_ref),
      sourceUrl: vodUrlAt(time), evidence_refs: array(entry.evidence_refs).slice(),
      ...(Object.hasOwn(entry, 'editorial_priority') ? {editorial_priority: entry.editorial_priority} : {}),
      storyProjection: { story_ref: entry.story_ref, title: entry.story_title, content: entry.story_content, sourceKind: 'point_story_projection' },
      rawPoint: entry, rawStory: null, rawProjection: entry
    };
  });
  const pointsById = new Map(pointViews.map(point => [point.id, point]));
  const renderedHighlights = new Map(array(rendered.highlights).map(entry => [entry.id, entry]));
  const renderedClips = new Map(array(rendered.clips).map(entry => [entry.clipUid, entry]));
  const clipByImage = new Map(array(rendered.clips).map(entry => [entry.imageAttributes?.src, entry]));
  const imageSourcesByFile = new Map(array(packet.imageSources?.images).map(entry => [entry.file, entry]));
  const imageFrames = outline.filter(entry => entry.image && typeof entry.image === 'object').map(entry => entry.image);
  const imageForHighlight = entry => {
    const placement = array(packet.highlightImagePlacements).find(item => item.highlight_id === entry.id) || null;
    const originalPresentation = renderedHighlights.get(entry.id);
    const originalImage = originalPresentation?.image;
    const suppliedFrame = entry.image && typeof entry.image === 'object' ? entry.image : null;
    const imageFile = text(placement?.image) || text(originalImage?.imageAttributes?.src) || text(suppliedFrame?.data_url) || text(suppliedFrame?.src);
    const src = asset(imageFile);
    if (!src) return null;
    const imageSource = imageSourcesByFile.get(imageFile) || null;
    const role = text(placement?.role) || (originalImage || suppliedFrame ? 'original_saved_highlight_image' : 'provided_highlight_image');
    if (role === 'original_saved_highlight_image' && originalImage) {
      const frameRef = text(originalImage.figureAttributes?.['data-frame-ref']);
      const frame = imageFrames.find(item => item.frame_ref === frameRef) || null;
      const imageTimeText = originalImage.captionLinkAttributes?.['data-start'];
      const imageTime = typeof frame?.source_sec === 'number' ? seconds(frame.source_sec, `outlineImage.${frameRef}.source_sec`) : typeof imageTimeText === 'string' && /^\d+(?:\.\d+)?$/.test(imageTimeText)
        ? seconds(Number(imageTimeText), `renderedOriginal.${entry.id}.image.source_sec`) : null;
      return {
        src, alt: text(originalImage.imageAttributes?.alt),
        role, label: '원본에 저장된 장면 이미지', source_sec: imageTime,
        frame_ref: frameRef, sha256: text(originalImage.figureAttributes?.['data-image-sha256']),
        ...(frame?.source_interval_sec ? {source_interval_sec: frame.source_interval_sec} : {}),
        ...(frame?.time_binding ? {time_binding: frame.time_binding} : {}),
        caption: text(originalImage.caption), sourceIntervalLabel: text(originalImage.sourceIntervalLabel),
        sourceUrl: link(originalImage.captionLinkAttributes?.href),
        provenance: { placement, renderedImage: originalImage, outlineImage: frame, imageSource }
      };
    }
    const clip = clipByImage.get(imageFile) || null;
    const example = role === 'design_example_from_existing_public_clip_thumbnail';
    const sourceTime = placement?.source_sec ?? suppliedFrame?.source_sec;
    return {
      src,
      alt: text(placement?.alt) || text(suppliedFrame?.alt) || (example && clip ? `시청자 클립 썸네일: ${clip.title} (하이라이트 이미지 배치 예시)` : text(entry.title)),
      role, label: example ? '배치 예시 · 공개 클립 썸네일' : role === 'original_saved_highlight_image' ? '원본에 저장된 장면 이미지' : '하이라이트 이미지',
      source_sec: typeof sourceTime === 'number' ? seconds(sourceTime, `highlightImage.${entry.id}.source_sec`) : null,
      caption: text(placement?.caption) || text(suppliedFrame?.caption), sourceUrl: link(example ? clip?.sourceUrl : placement?.source_url), thumbnailUrl: link(imageSource?.url),
      clipUid: text(clip?.clipUid),
      provenance: { placement, imageSource, renderedClip: clip, outlineImage: suppliedFrame }
    };
  };
  const highlights = rawHighlights.map((entry, index) => {
    const presentation = renderedHighlights.get(entry.id) || null;
    const pointRefs = array(entry.point_refs);
    const points = pointRefs.map(ref => {
      const point = pointsById.get(ref);
      if (!point) issue('missing_point', `outline_projection.${entry.id}.point_refs`);
      return point;
    }).filter(Boolean);
    const spans = array(entry.source_spans).map((span, spanIndex) => {
      const path = `outline_projection.${entry.id}.source_spans[${spanIndex}]`;
      const start = seconds(span.start_sec, `${path}.start_sec`);
      const end = seconds(span.end_sec, `${path}.end_sec`);
      if (start !== null && end !== null && end <= start) issue('nonpositive_span', path);
      return { ...span, start, end, label: text(span.reason), sourceUrl: vodUrlAt(start), rawSpan: span };
    });
    const start = spans[0]?.start ?? null;
    const image = imageForHighlight(entry, index);
    return {
      id: text(entry.id), displayId: `H${index + 1}`, ordinal: index + 1, kind: 'highlight',
      title: text(entry.title), description: text(entry.content), body: text(entry.content),
      selectionReason: text(presentation?.selectionReason), watchReason: text(presentation?.selectionReason),
      storySummary: text(presentation?.storySummary) || text(entry.content), tag: '하이라이트',
      start, end: spans.length ? spans[spans.length - 1].end : null,
      projectedStart: entry.start_sec, projectedEnd: entry.end_sec,
      chapter: points[0]?.d1_ref || chapterAt(start), topic: points[0]?.d2_ref || topicAt(start),
      points, point_refs: pointRefs.slice(), story_refs: points.map(point => point.story_ref).filter(Boolean),
      storyRefsDerivedFromPoints: true, source_spans: spans,
      sourceUrl: link(presentation?.sourceUrl) || vodUrlAt(start),
      evidence: spans.map(span => span.label).filter(Boolean).join('\n\n'),
      image, hasArtwork: Boolean(image), artworkKind: image?.role || 'none',
      ...(Object.hasOwn(entry, 'editorial_priority') ? {editorial_priority: entry.editorial_priority} : {}),
      rawHighlight: entry, renderedOriginal: presentation
    };
  });
  const referencedPointIds = new Set(rawHighlights.flatMap(entry => array(entry.point_refs)));
  const standalonePoints = pointViews.filter(point => !referencedPointIds.has(point.id)).map(point => ({
    id: point.id, displayId: point.id, kind: 'point', title: point.title,
    description: point.content, body: point.content, storySummary: point.story,
    selectionReason: '', tag: '다시 볼 지점', start: point.time,
    chapter: point.d1_ref || chapterAt(point.time), topic: point.d2_ref || topicAt(point.time),
    point, sourceUrl: point.sourceUrl, evidence: point.story,
    image: null, hasArtwork: false, artworkKind: 'none', rawPoint: point.rawPoint
  }));
  const moments = [...highlights, ...standalonePoints].sort((a, b) => {
    if (a.start === null) return b.start === null ? 0 : 1;
    if (b.start === null) return -1;
    return a.start - b.start;
  });
  const clips = array(output.events).filter(entry => entry.kind === 'viewer_clip').map(entry => {
    const clipEvidence = array(entry.evidence).find(item => typeof item.clip_uid === 'string');
    const clipUid = text(clipEvidence?.clip_uid);
    const presentation = renderedClips.get(clipUid) || null;
    const imageFile = text(presentation?.imageAttributes?.src) || text(entry.image?.src) || text(entry.thumbnail_url);
    const imageSource = imageSourcesByFile.get(imageFile) || null;
    const start = seconds(entry.start_sec, `events.${entry.id}.start_sec`);
    const end = seconds(entry.end_sec, `events.${entry.id}.end_sec`);
    const image = asset(imageFile) ? {
      src: asset(imageFile), alt: `시청자 클립 썸네일: ${text(entry.title)}`,
      role: 'existing_public_viewer_clip_thumbnail', label: '시청자 클립 썸네일', source_sec: null,
      sourceUrl: link(presentation?.sourceUrl), thumbnailUrl: link(imageSource?.url),
      provenance: { imageSource, renderedImageAttributes: presentation?.imageAttributes || null }
    } : null;
    return {
      id: text(entry.id), displayId: clipUid, clipUid, kind: 'clip', title: text(entry.title),
      description: '', start, end, seek: seconds(entry.seek_sec, `events.${entry.id}.seek_sec`),
      chapter: chapterAt(start), topic: topicAt(start), timecode: entry.timecode,
      sourceUrl: link(presentation?.sourceUrl) || link(clipEvidence?.url), vodUrl: vodUrlAt(start),
      image, hasArtwork: Boolean(image), artworkKind: image?.role || 'none',
      renderedMeta: text(presentation?.renderedMeta), engagementAvailable: output.viewer_clip_source?.engagement_available === true,
      rawEvent: entry, renderedOriginal: presentation
    };
  });
  const collaborationPeriods = array(output.collaboration_periods).map((entry, index) => {
    const path = `collaboration_periods[${index}]`;
    const start = seconds(entry.start_sec, `${path}.start_sec`);
    const end = seconds(entry.end_sec, `${path}.end_sec`);
    const membersValid = Array.isArray(entry.members) && entry.members.every(member => typeof member === 'string' && member.length > 0);
    const statusValid = ['supported', 'provisional'].includes(entry.support_status);
    const sourceValid = ['manager_declared', 'source_evidence'].includes(entry.source_kind);
    if (!membersValid) issue('unusable_members', `${path}.members`);
    if (!statusValid) issue('unusable_support_status', `${path}.support_status`);
    if (!sourceValid) issue('unusable_source_kind', `${path}.source_kind`);
    return {
      id: text(entry.id), start, end, start_sec: entry.start_sec, end_sec: entry.end_sec,
      members: membersValid ? entry.members.slice() : [], source_kind: entry.source_kind, support_status: entry.support_status,
      memberVods: array(entry.member_vods).map(link => ({ ...link })),
      usable: membersValid && statusValid && sourceValid && start !== null && end !== null && end > start, raw: entry
    };
  });
  const unknownCollaboration = reason => ({
    status: 'unknown', reason, hasPeriod: false, explicitNoMembers: false,
    members: [], memberVods: [], period: null, start: null, end: null,
    sourceKind: null, supportStatus: null, referenceStreamer: text(metadata.streamer)
  });
  const getCollaborationAt = value => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return unknownCollaboration('unusable_time');
    const covering = collaborationPeriods.filter(entry => entry.start !== null && entry.end !== null && value >= entry.start && value < entry.end);
    if (!covering.length) return unknownCollaboration('no_period');
    if (covering.length !== 1) return unknownCollaboration('ambiguous_periods');
    const period = covering[0];
    if (!period.usable) return unknownCollaboration('unusable_period');
    return {
      status: period.support_status, reason: null, hasPeriod: true, explicitNoMembers: period.members.length === 0,
      members: period.members.slice(), memberVods: period.memberVods.map(link => ({ ...link })),
      period: period.raw, start: period.start, end: period.end, sourceKind: period.source_kind,
      supportStatus: period.support_status, referenceStreamer: text(metadata.streamer)
    };
  };
  const sourceContract = Object.freeze({
    scope: 'viewer_output_projection', sourceSchema: output.schema_version, pageSchema: packet.schema_version,
    adapterScope: 'read_only_display_subset', productionValidation: false, nativeReportIncluded: false,
    sourceFiles: environment.reportURL ? [environment.reportURL] : [],
    clock: 'reference_vod_relative_seconds',
    rangeSource: 'outline_projection D1/D2 start_sec/end_sec; original clock text is retained',
    pointSource: 'outline_projection outline_point timestamp; no Point end time is invented',
    storySource: 'Point story_ref/story_title/story_content are existing output fields; no native Story roles are reconstructed',
    highlightSource: 'outline_projection editorial_highlight source_spans in original order with fractional seconds preserved',
    highlightRelationships: 'point_refs remain original; UI story_refs resolve those Points without claiming an original Highlight story_refs field',
    rawReportAlias: 'rawReport is a compatibility alias of rawViewerPayload, not a native report',
    imageSource: 'Images are joined by stable highlight_id or supplied with the original object; role and provenance are retained',
    imageTime: 'Only supplied frame/caption metadata provides image.source_sec. Placement start_sec does not set the Highlight or thumbnail time',
    syntheticSignals: 'designExamples only; never merged into original events, chat, or signal-scene statistics',
    collaborationInterval: '[start_sec,end_sec) on the reference VOD clock',
    collaborationAbsence: 'A missing period is unknown; a supplied period with an empty members list explicitly declares no members. Document-scoped candidates are not attendance periods',
    memberVodNavigation: 'Member VOD start only; no main-VOD timestamp or synchronization is implied',
    unavailableTime: null,
    notice: '제공된 화면용 출력을 읽기 전용으로 표시합니다. 원본 분석 패킷 전체의 검증이나 운영 반영을 뜻하지 않습니다.'
  });
  return Object.freeze({
    title: text(metadata.title), streamer: text(metadata.streamer), date: text(metadata.date),
    game: text(metadata.game), summary: text(metadata.summary), lokiVersion: text(metadata.lokiVersion), metadata,
    duration: seconds(output.duration_sec, 'viewerOutput.duration_sec'), durationLabel: text(metadata.durationLabel),
    videoNo: text(output.video_no), sourcePlatform: sourceIdentity.platform, sourceUrl, vodURL: vodUrlAt,
    reportId: text(packet.report_id) || environment.reportURL || text(output.video_no), environment,
    initialMomentId: text(meta.initialMomentId), sample: false,
    chapters, topics, moments, highlights, points: pointViews, clips,
    stories: pointViews.map(point => ({ ...point.storyProjection, pointId: point.id, rawProjection: point.rawProjection })),
    previewMeta: meta, rawViewerPayload: output, rawReport: output, rawHighlights,
    sourceContract, adapterIssues: issues,
    collaborationPeriods, getCollaborationAt,
    collaborationCandidates: array(output.collaboration_candidates), collaborationReplays: output.collaboration_replays || null,
    imageSources: packet.imageSources, highlightImagePlacements: packet.highlightImagePlacements,
    renderedOriginal: rendered, sourceManifest: packet.sourceManifest,
    designExamples: packet.designExamples,
    subtitles: array(output.subtitles), chatBuckets: array(output.chat_buckets),
    viewerClipSource: output.viewer_clip_source, reportProjection: output.report_projection
  });
  };
  window.LokiReportAdapter = Object.freeze({create});
})();
