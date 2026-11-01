'use strict';
const native = require('../pipeline/assets/ttaempad_comment_format.js');

const LIMITS = Object.freeze({ inputBytes: 1024 * 1024, outputBytes: 8 * 1024 * 1024,
  characters: 250000, documents: 100, projections: 128 });
const key = name => name.normalize('NFKC').toLocaleLowerCase();
// Roster syntax is owned by the native parser. Its resolved aliases obey the
// same public Unicode-name contract as already validated collaboration periods.
const safeName = name => typeof name === 'string' && name.length > 0 && name === name.trim() &&
  [...name].length <= 64 && !/\p{C}/u.test(name);
const rawName = name => /^[\p{L}\p{N}_][\p{L}\p{N}_.-]{0,63}$/u.test(name);
const fail = code => { throw new Error(code); };

function membersOf(roster, period = false) {
  if (!roster || !['declared', 'candidate', 'none'].includes(roster.mode) ||
      !Array.isArray(roster.names) ||
      roster.identities?.some(person => person.conflict) || roster.names.some(name => !safeName(name))) return null;
  // A raw untimed phrase cannot become a person merely because it is printable.
  // Resolved document/inline aliases and native timed periods have their own
  // explicit name authority; retain their literal display names.
  if (!period && roster.names.some((name, index) => !rawName(name) &&
      !['document_alias', 'inline_alias'].includes(roster.identities?.[index]?.basis))) return null;
  if (roster.mode === 'none') return [];
  if (!roster.names.length || roster.names.some(name => ['없음', '미확인', '불명', 'unknown'].includes(key(name)))) return null;
  return [...new Map(roster.names.map(name => [key(name), name])).values()];
}

function normalizedInput(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) fail('editor_comment_input_invalid');
  let encoded;
  try { encoded = JSON.stringify(input); } catch (_) { fail('editor_comment_input_invalid'); }
  if (Buffer.byteLength(encoded, 'utf8') > LIMITS.inputBytes) fail('editor_comment_input_limit');
  const envelope = input.format === 'chzzk.comment-timeline' ? { timeline: input, video_no: input.videoNo } : input;
  const video = envelope.video_no === undefined ? '' : String(envelope.video_no);
  if (video && !/^[1-9][0-9]{0,19}$/.test(video)) fail('editor_comment_video_invalid');
  const duration = envelope.duration_sec ?? null;
  if (duration !== null && (typeof duration !== 'number' || !Number.isFinite(duration) || duration < 0)) fail('editor_comment_duration_invalid');
  if (envelope.completeness !== undefined && !['partial', 'complete'].includes(envelope.completeness)) fail('editor_comment_completeness_invalid');
  if (envelope.continuous_lines !== undefined && typeof envelope.continuous_lines !== 'boolean') fail('editor_comment_continuity_invalid');
  if (envelope.source_revision !== undefined && (typeof envelope.source_revision !== 'string' || envelope.source_revision.length > 128)) fail('editor_comment_revision_invalid');
  if (envelope.timeline !== undefined && envelope.documents !== undefined) fail('editor_comment_source_conflict');
  let documents;
  if (envelope.timeline !== undefined) {
    documents = native.readTimelineFile(typeof envelope.timeline === 'string' ? envelope.timeline : JSON.stringify(envelope.timeline), 'timeline.json', video || null);
  } else documents = envelope.documents;
  if (!Array.isArray(documents) || documents.length > LIMITS.documents || documents.some(doc => !doc || typeof doc !== 'object' || Array.isArray(doc) || typeof doc.raw !== 'string') ||
      documents.reduce((sum, doc) => sum + doc.raw.length, 0) > LIMITS.characters) fail('editor_comment_documents_invalid');
  for (const doc of documents) {
    if (doc.aliases !== undefined && (!Array.isArray(doc.aliases) || doc.aliases.length > 100 || doc.aliases.some(alias => !alias ||
      [alias.alias, alias.streamer].some(value => typeof value !== 'string' || !value.trim() || value.length > 80 || /[\[\]<>\/\\\r\n\p{C}]/u.test(value))))) fail('editor_comment_aliases_invalid');
    if (doc.completeness !== undefined && !['partial', 'complete'].includes(doc.completeness)) fail('editor_comment_completeness_invalid');
  }
  return { documents, options: { videoNo: video, durationSec: duration,
    sourceRevision: envelope.source_revision || 'unversioned', completeness: envelope.completeness || 'partial',
    continuousLines: envelope.continuous_lines !== false } };
}

function parseEditorDocuments(input) {
  const { documents, options } = normalizedInput(input);
  const parsed = documents.length ? native.parseCompatibleComments(documents, options) : { documents: [], entries: [], diagnostics: [] };
  const rosters = [], periods = [], seen = new Set();
  function addRoster(roster, documentId, sourceLine, timed = false) {
    const members = membersOf(roster, timed);
    if (!members?.length) return;
    const identity = `${documentId}:${members.map(key).sort().join(',')}`;
    if (seen.has(identity)) return;
    if (rosters.length === LIMITS.projections) fail('editor_comment_projection_limit');
    seen.add(identity);
    rosters.push({ members, mode: roster.mode, document_id: documentId, source_line: sourceLine });
  }
  for (const doc of parsed.documents) {
    for (const roster of doc.documentRosters || []) addRoster(roster, roster.documentId || doc.documentId, roster.sourceLine);
    for (const entry of doc.entries) {
      const roster = entry.effectiveParticipants || entry.participantCandidates;
      addRoster(roster, entry.sourceDocumentId || doc.documentId, entry.sourceLine, true);
      const members = membersOf(roster, true);
      // Larger candidates remain complete name-only rosters. The declared
      // public period capacity still applies to every timed projection.
      if (members === null || members.length > 32) continue;
      let endSec = entry.resolvedEndSec;
      // Candidate lookup bounds own search context independently of display
      // geometry. Unknown or expired context cannot fall back to a scene end.
      if (roster.mode === 'candidate' && (Object.hasOwn(roster, 'contextEndSec') ||
          Object.hasOwn(roster, 'contextEndBasis'))) {
        if (!['next_scene', 'explicit', 'parent'].includes(roster.contextEndBasis)) continue;
        endSec = roster.contextEndSec;
      } else if (entry.geometry !== 'interval' || entry.endBasis === 'media_end') continue;
      if (!Number.isFinite(entry.startSec) || !Number.isFinite(endSec) || entry.startSec < 0 || endSec <= entry.startSec ||
          (options.durationSec !== null && endSec > options.durationSec)) continue;
      let spans = [[entry.startSec, endSec]];
      // A child's roster/none/unknown masks derived parent lookup only. A null
      // exclusion end removes the known remainder without inventing an end.
      if (roster.mode === 'candidate') for (const range of roster.excludedContextRanges || []) {
        if (!Number.isFinite(range.startSec) ||
            (range.endSec !== null && (!Number.isFinite(range.endSec) || range.endSec <= range.startSec))) continue;
        const excludedEnd = range.endSec === null ? Infinity : range.endSec;
        spans = spans.flatMap(([start, end]) => range.startSec >= end || excludedEnd <= start ? [[start, end]] : [
          ...(start < range.startSec ? [[start, Math.min(end, range.startSec)]] : []),
          ...(excludedEnd < end ? [[Math.max(start, excludedEnd), end]] : []),
        ]);
      }
      for (const [start, end] of spans) {
        if (periods.length === LIMITS.projections) fail('editor_comment_projection_limit');
        periods.push({ start_sec: start, end_sec: end, members, mode: roster.mode,
          document_id: entry.sourceDocumentId || doc.documentId, source_line: entry.sourceLine });
      }
    }
    for (const block of doc.untimedBlocks || []) {
      if (/(?:합방|참여자|참가자|\bw[./]|\bwith\b)/i.test(block.raw)) addRoster(block.participantCandidates, doc.documentId, block.line);
    }
  }
  // Legacy context files can place a roster after prose. Reuse native header
  // parsing for those labels; do not introduce a second name/alias grammar.
  for (const [index, source] of documents.entries()) {
    const lines = source.raw.replace(/\r\n?/g, '\n').split('\n');
    const aliasLines = lines.filter(line => /^\s*별칭\s*:/.test(line));
    const nativeHeader = native.documentRoster(source.raw);
    let labels = 0;
    for (const [line, raw] of lines.entries()) {
      if (nativeHeader && line + 1 >= nativeHeader.sourceLine && line + 1 <= nativeHeader.lastSourceLine) continue;
      const header = raw.replace(/^\s*(?:[-*#]+\s*)?(?:참여자|참가자|멤버)\s*[:：]\s*/, '합방: ')
        .replace(/^\s*(합방|함께|참여)\s*：/, '$1:');
      if (!native.documentRoster(header)) continue;
      if (++labels > LIMITS.projections) fail('editor_comment_projection_limit');
      const isolated = native.parseCompatibleComments([{ raw: [header, ...aliasLines].join('\n'), aliases: source.aliases }]);
      for (const roster of isolated.documents[0].documentRosters) addRoster(roster, `comment-${index + 1}`, line + 1);
    }
  }
  const sourceOrder = new Map(documents.map((_, index) => [`comment-${index + 1}`, index]));
  rosters.sort((left, right) => sourceOrder.get(left.document_id) - sourceOrder.get(right.document_id) ||
    left.source_line - right.source_line);
  const result = { schema_version: 'cva.editor_comments.v1', ...parsed, roster_candidates: rosters, periods };
  if (Buffer.byteLength(JSON.stringify(result), 'utf8') > LIMITS.outputBytes) fail('editor_comment_output_limit');
  return result;
}

async function runCli(project = result => result) {
  try {
    const chunks = []; let bytes = 0;
    for await (const chunk of process.stdin) {
      bytes += chunk.length;
      if (bytes > LIMITS.inputBytes) fail('editor_comment_input_limit');
      chunks.push(chunk);
    }
    const result = project(parseEditorDocuments(JSON.parse(Buffer.concat(chunks).toString('utf8'))));
    const serialized = JSON.stringify(result);
    if (Buffer.byteLength(serialized, 'utf8') > LIMITS.outputBytes) fail('editor_comment_output_limit');
    process.stdout.write(serialized);
  } catch (error) {
    // Never expose a native message, source text, path or stack to callers.
    const code = /^editor_comment_[a-z_]+$/.test(error?.message || '') ? error.message : 'editor_comment_parse_failed';
    process.stderr.write(JSON.stringify({ error: code }));
    process.exitCode = 1;
  }
}

module.exports = { parseEditorDocuments, runCli, LIMITS };
if (require.main === module) runCli();
