(function initCommentFormat() {
  const VERSION = "comment_preview.v2";
  const MAX_CHARS = 250000;
  const TIME = "(\\d+:\\d{2}(?::\\d{2})?)(?![\\d:]|\\.\\d)";
  const SPAN_TIME = "(\\d+:\\d{2}(?::\\d{2})?(?:\\.\\d{1,3})?)";
  const timedLine = new RegExp(`^${TIME}(?:\\s*(?:~|–|—|-)\\s*${TIME})?\\s+(.+)$`);
  const tags = new Map([
    ["P", "point"], ["포인트", "point"],
    ["M", "memo"], ["메모", "memo"],
    ["W", "watch"], ["볼 곳", "watch"], ["먼저 보기", "watch"],
    ["H", "highlight"], ["하이라이트", "highlight"],
    ["H?", "expected_highlight"], ["예상", "expected_highlight"],
    ["합방", "collab_context"]
  ]);

  function parseTime(value) {
    if (!/^\d+:\d{2}(?::\d{2})?(?:\.\d{1,3})?$/.test(String(value))) return null;
    const parts = String(value).split(":").map(Number);
    const seconds = parts.length === 2 && parts[1] < 60 ? parts[0] * 60 + parts[1] :
      parts.length === 3 && parts[1] < 60 && parts[2] < 60 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : null;
    return seconds !== null && Number.isFinite(seconds) && Number.isSafeInteger(Math.trunc(seconds)) ? seconds : null;
  }

  function invalidRangeEnd(tail) {
    const end = String(tail).match(/^\s*(?:~|–|—|-)\s*(\d+:[^\s]*)/);
    if (!end) return false;
    const clock = end[1].match(new RegExp(`^${TIME}`));
    return !clock || parseTime(clock[1]) === null;
  }

  function formatTime(seconds) {
    if (!Number.isInteger(seconds) || seconds < 0) throw new RangeError("invalid time");
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds % 3600 / 60);
    const rest = seconds % 60;
    return [hours, minutes, rest].map((part) => String(part).padStart(2, "0")).join(":");
  }

  function parseParticipants(value) {
    const trimmed = value.trim();
    if (!trimmed) return { mode: "invalid", names: [] };
    if (trimmed === "없음") return { mode: "none", names: [] };
    if (trimmed === "미확인") return { mode: "unknown", names: [] };
    const roster = splitRosterNames(trimmed, true);
    const names = roster?.names || [];
    if (!roster || roster.invalid) return { mode: "invalid", names };
    return { mode: "declared", names };
  }

  // Consume each character once. Delimiters need word/list boundaries so a
  // literal nickname such as VSinger or AvsB remains a single lookup token.
  function splitRosterNames(list, allowTeams) {
    const marker = /(?:versus|[vVｖＶ][.．]?[sSｓＳ][.．]?)/iy;
    // A single explicitly named channel may itself be called VS.
    if(/^(?:versus|[vVｖＶ][.．]?[sSｓＳ][.．]?)$/i.test(list.trim()))
      return {names:[list.trim()],teams:0,commas:0};
    const names = [];
    let depth=0, part='', groupMembers=0, teams=0, commas=0, commaPending=false, afterTeam=false, invalid=false;
    const flush = () => {
      const name=part.trim();
      if(!name)return false;
      names.push(name);part='';groupMembers++;commaPending=false;afterTeam=false;
      return true;
    };
    for(let i=0;i<list.length;i++) {
      const char=list[i];
      let separator=null;
      if(depth===0 && /[vVｖＶ]/.test(char) && (i===0 || /[\s,，)]/.test(list[i-1]))) {
        marker.lastIndex=i;
        const match=marker.exec(list);
        if(match && (marker.lastIndex===list.length || /[\s,，(]/.test(list[marker.lastIndex])))separator=match;
      }
      if(separator) {
        if(!allowTeams)return null;
        flush();
        if(!groupMembers)invalid=true;
        teams++;groupMembers=0;commaPending=false;afterTeam=true;
        i+=separator[0].length-1;
      } else if(depth===0 && (char===',' || char==='，')) {
        commas++;
        if(!flush() && !(afterTeam && !commaPending))invalid=true;
        commaPending=true;
      } else {
        if(char==='(')depth++;
        if(char===')' && --depth<0)return null;
        if(!/\s/.test(char)) {commaPending=false;afterTeam=false;}
        part+=char;
      }
    }
    if(depth!==0)return null;
    flush();
    if(!names.length || commaPending || (teams && !groupMembers))invalid=true;
    return {names,teams,commas,invalid};
  }

  // A leading roster is lookup metadata, not an invented timed participation span.
  function documentRoster(input) {
    const lines = String(input || '').replace(/\r\n?/g, '\n').split('\n');
    let roster = null;
    for (let i = 0; i < lines.length; i++) {
      const text = compatibleLexLine(lines[i]).trim();
      if (!text) continue;
      const match = text.match(/^(?:합방|함께|참여)\s*:\s*(.*)$/);
      if (!match) break;
      const value = parseParticipants(match[1]);
      if (value.names.some(n => n.length > 80 || /[\p{C}<>\[\]\/\\]/u.test(n))) value.mode = 'invalid';
      if (!roster) roster = {...value, sourceLine:i+1, lastSourceLine:i+1, raw:lines[i], scope:'document'};
      else {
        if (roster.mode !== value.mode || JSON.stringify(roster.names) !== JSON.stringify(value.names)) {
          roster.mode = 'invalid'; roster.names = []; roster.conflict = true;
        }
        roster.lastSourceLine = i+1;
      }
    }
    return roster ? {...roster, mode:roster.mode === 'declared' ? 'candidate' : roster.mode} : null;
  }

  function comparisonTitle(value) {
    return /(?:갈드컵|강함\s*순위|누가\s*더\s*(?:세|쎄|쎌)|티어\s*(?:표|순위))/.test(String(value || ''));
  }

  function collaborationTitle(value) {
    const title = String(value || '');
    return /(?:합방|내전|합동|함께\s*(?:플레이|게임))/.test(title) &&
      !comparisonTitle(title);
  }

  function inactiveCollaborationTitle(value) {
    let title='',depth=0;
    for(const char of String(value || '')) {
      if(char==='(')depth++;
      else if(char===')' && depth)depth--;
      else if(!depth)title+=char;
    }
    return /(?:합방|내전|합동)(?:은|이)?\s*(?:종료|끝|해산|예정|준비|대기|취소|아님|안\s*함|못\s*함|없음|후기|영상|클립)(?:입니다|임|중|함|됨)?(?=\s|$|[.!?。,:;])/u.test(title) ||
      /(?:끝난|종료된|예정인|취소된)\s*(?:합방|내전|합동)(?=\s|$|[.!?。,:;])/u.test(title);
  }
  function collaborationAnchorTitle(value) {
    return collaborationTitle(value) && !inactiveCollaborationTitle(value);
  }
  function collaborationContinuationTitle(title) {
    const text=String(title || '').normalize('NFKC').replace(/^[⭐★]\s*|\s*[⭐★]$/gu,'');
    return /^(?:제\s*)?\d+\s*(?:판|라운드|세트|게임|경기|R)(?=\s|$|[.!?:：()\[\]…]|시작|종료|끝|밴픽|게임)|^(?:첫|첫째|두번째|두 번째|둘째|세번째|세 번째|셋째|마지막)\s*(?:판|라운드|세트|게임|경기)(?=\s|$|[.!?:：()\[\]…]|시작|종료|끝)|^(?:round|game|match|set)\s*#?\s*\d+(?=\s|$|[.!?:：()\[\]…])|^(?:밴픽|픽밴|게임\s*시작|시작)(?=\s|$|[.!?:：()\[\]…])/i.test(text);
  }
  function collaborationEndTitle(title) {
    return /(?:합방|내전|합동)\s*(?:종료|끝|해산|취소)(?:입니다|임|함|됨)?(?=\s|$|[.!?。,:;])/.test(title) ||
      /^(?:저챗|잡담|방종|다음\s*(?:게임|콘텐츠|방송))(?=\s|$|[.!?。,:;])/.test(title);
  }
  function adjacentRoster(raw, sourceLine, onInvalid) {
    const text=String(raw).trim();
    const parenthesized=participantCandidates(text,sourceLine,true,onInvalid);
    if(parenthesized?.raw.trim()===text)return parenthesized;
    // A bare complete team list is only a clue beside a positive timed anchor.
    // Do not turn arbitrary prose or an isolated comma sentence into a roster.
    const split=splitRosterNames(text,true);
    if(!split?.teams || !split.commas)return null;
    const candidate=participantCandidates('('+text+')',sourceLine,true,onInvalid);
    return candidate ? {...candidate,raw:text} : null;
  }

  function participantCandidates(raw, sourceLine, teamContext = false, onInvalid) {
    // Prefer an explicit with clause; later explanatory parentheses are unrelated.
    const original = String(raw).trimEnd();
    // A named topic may put its roster after a colon rather than in brackets.
    // Keep the leading document-wide `합방:` declaration on its own path.
    const colonText=compatibleLexLine(original).trim().replace(/^(?:#{1,6}|--?|└|ㄴ)\s*/,'')
      .replace(new RegExp('^'+TIME+'\\s*:?\\s*'),'');
    const colon=colonText.match(/^(.+?)\s*[:：]\s*(.+)$/);
    let colonDepth=0;
    for(const char of colon?.[1] || '') {if(char==='(')colonDepth++;else if(char===')')colonDepth--;}
    if(colon && colonDepth===0 && collaborationAnchorTitle(colon[1]) &&
        !/^(?:합방|함께|참여)$/.test(colon[1].trim())) {
      const candidate=participantCandidates('('+colon[2]+')',sourceLine,true,onInvalid);
      if(candidate)return {...candidate,raw:colon[2]};
    }
    const clauses=[];let level=0,start=-1;
    for(let i=0;i<original.length;i++){if(original[i]==='('){if(level===0)start=i;level++;}else if(original[i]===')'&&level>0){if(--level===0)clauses.push({text:original.slice(start,i+1),start});}}
    const withPattern = /^(?:w\.|w\/|with\b)\s*:?\s*/i;
    const explicit=clauses.find(clause=>withPattern.test(compatibleLexLine(clause.text.slice(1,-1)).trim()));
    const titleParts=[];let titleStart=0;
    for(const clause of clauses) {titleParts.push(original.slice(titleStart,clause.start));titleStart=clause.start+clause.text.length;}
    titleParts.push(original.slice(titleStart));
    const prefix=titleParts.join(' ');
    let selected=null, selectedStrength=0, blockedStrength=0;
    for(const clause of explicit ? [explicit] : clauses) {
      const text=clause.text;
      const body=text.slice(1,-1).trim();
      if (/^(?:합방|함께|참여)\s*:/.test(body))continue;
      if(!teamContext && !explicit && comparisonTitle(prefix))continue;
      const lexicalBody=compatibleLexLine(body), withNames=withPattern.test(lexicalBody);
      const allowTeams=teamContext || withNames || collaborationTitle(prefix);
      const roster=splitRosterNames(lexicalBody.replace(withPattern,''),allowTeams);
      const names=roster?.names;
      const strength=withNames ? 4 : roster?.teams && roster.commas ? 3 : roster?.commas ? 2 : 1;
      if(roster?.invalid) {blockedStrength=Math.max(blockedStrength,strength);continue;}
      if(!names || names.length < (withNames ? 1 : 2) || names.some(name=>
        name.length>80 || /[\p{C}:\[\]<>\/\\]/u.test(name)))continue;
      // A structured team list outranks trailing game/round explanations.
      // Equal structures retain the existing last-list compatibility rule.
      if(strength>=selectedStrength) {
        selected={mode:'candidate',names,sourceLine,raw:text};selectedStrength=strength;
      }
    }
    if(blockedStrength>selectedStrength) {onInvalid?.();return null;}
    return selected;
  }

  function participantAliases(source) {
    const bindings = new Map(), conflicts = new Set();
    const add = (alias, streamer, sourceLine) => {
      alias = String(alias || '').trim(); streamer = String(streamer || '').trim();
      if (!alias || !streamer || alias.length > 80 || streamer.length > 80 || /[\[\]<>\/\\\r\n]/.test(alias + streamer)) return;
      const key = alias.normalize('NFKC').toLocaleLowerCase();
      if (bindings.has(key) && bindings.get(key).streamer !== streamer) conflicts.add(key);
      else bindings.set(key, {alias, streamer, sourceLine});
    };
    String(source.raw).split(/\r\n?|\n/).forEach((line, i) => {
      const match = compatibleLexLine(line).match(/^\s*별칭\s*:\s*(.+?)\s*=\s*(.+?)\s*$/);
      if (match) add(match[1], match[2], i + 1);
    });
    for (const item of Array.isArray(source.aliases) ? source.aliases.slice(0, 100) : []) add(item?.alias, item?.streamer, null);
    return {bindings, conflicts};
  }

  function resolveParticipantNames(value, source) {
    if (!value?.names?.length) return value;
    const {bindings, conflicts} = participantAliases(source);
    const identities = value.names.map(displayName => {
      const nested = displayName.match(/^([^(),]+)\(([^(),]+)\)$/);
      const key = displayName.normalize('NFKC').toLocaleLowerCase();
      const binding = !conflicts.has(key) && bindings.get(key);
      const alias = binding?.alias || nested?.[1]?.trim() || null;
      const streamer = binding?.streamer || nested?.[2]?.trim() || displayName;
      return {displayName, alias, streamer, basis: binding ? 'document_alias' : nested ? 'inline_alias' : 'name', conflict: conflicts.has(key)};
    });
    return {...value, names: identities.map(item => item.streamer), identities};
  }

  function parseComment(input, options = {}) {
    const source = String(input || "").replace(/\r\n?/g, "\n");
    if (source.length > MAX_CHARS) throw new RangeError("comment exceeds preview limit");
    const sourceRevision = String(options.sourceRevision || "unversioned");
    const durationSec = Number.isFinite(options.durationSec) && options.durationSec >= 0
      ? options.durationSec : null;
    const completeness = options.completeness === "partial" ? "partial" : "complete";
    const continuousLines = completeness === "complete" || options.continuousLines === true;
    const entries = [];
    const lines = [];
    const diagnostics = [];
    const stack = [];
    const orphanedChapters = new Set();
    const headerRoster = documentRoster(source);
    let participantTarget = null;
    let detailTarget = null;
    let hierarchyBarrierLine = 0;
    const scopes = options.logicalScopeLines;
    const sameScope = (a, b) => !scopes || scopes[a.sourceLine - 1] === scopes[b.sourceLine - 1];

    function issue(line, code) { diagnostics.push({ line, code }); }

    source.split("\n").forEach((raw, index) => {
      const line = index + 1;
      const trimmed = raw.trim();
      if (scopes && index > 0 && scopes[index] !== scopes[index - 1]) {
        stack.length = 0;
        participantTarget = detailTarget = null;
        hierarchyBarrierLine = 0;
      }
      if (options.logicalHierarchyBarriers?.[index]) {
        // A damaged heading cannot carry an earlier chapter or roster into the
        // following rows. Keep those rows' own clocks and declarations usable.
        stack.length = 0;
        participantTarget = detailTarget = null;
        hierarchyBarrierLine = line;
      }
      if (!trimmed) {
        lines.push({ line, raw, kind: "blank" });
        return;
      }
      const detail = trimmed.match(/^(설명|맥락|검토 상태):\s*(.*)$/);
      const spansLine = trimmed.match(/^원본 구간:\s*(.*)$/);
      if (detail || spansLine) {
        if (!detailTarget) issue(line, "detail_without_entry");
        else if (detail) detailTarget.detailLines.push({ sourceLine: line, raw, label: detail[1], text: detail[2] });
        else {
          const spans = spansLine[1].split(";").map((part) => {
            const match = part.trim().match(new RegExp(`^${SPAN_TIME}\\s*(?:~|–|—|-)\\s*${SPAN_TIME}$`));
            return match ? { startSec: parseTime(match[1]), endSec: parseTime(match[2]) } : null;
          });
          if (detailTarget.geometry !== "interval" || detailTarget.explicitEndSec === null || detailTarget.sourceSpans.length ||
              !spans.length || spans.some((span) => !span || span.startSec === null || span.endSec === null ||
                span.endSec <= span.startSec || span.startSec < detailTarget.startSec ||
                (detailTarget.explicitEndSec !== null && span.endSec > detailTarget.explicitEndSec &&
                  Math.floor(span.endSec) !== detailTarget.explicitEndSec) ||
                (durationSec !== null && span.endSec > durationSec)) ||
              spans.some((span, index) => index > 0 && span.startSec < spans[index - 1].endSec)) {
            issue(line, "invalid_source_spans");
          } else detailTarget.sourceSpans = spans;
        }
        lines.push({ line, raw, kind: detail ? "detail" : "source_spans", entryId: detailTarget?.entryId || null });
        return;
      }
      if (headerRoster && line >= headerRoster.sourceLine && line <= headerRoster.lastSourceLine) {
        if (line === headerRoster.sourceLine && headerRoster.mode === 'invalid') issue(line, headerRoster.conflict ? 'participants_conflict' : 'participants_invalid');
        lines.push({line, raw, kind:'document_participants'});
        return;
      }
      const roster = (options.compatibleInput ? compatibleLexLine(trimmed) : trimmed).match(/^(합방|함께|참여)\s*:\s*(.*)$/);
      if (roster) {
        const participants = parseParticipants(roster[2]);
        if (!participantTarget) issue(line, "participants_without_range");
        else if (participants.mode === "invalid") issue(line, "participants_invalid");
        else if (participantTarget.participants) {
          const previous = participantTarget.participants;
          issue(line, previous.mode === participants.mode &&
            JSON.stringify(previous.names) === JSON.stringify(participants.names)
            ? "participants_duplicate" : "participants_conflict");
        }
        else participantTarget.participants = { ...participants, sourceLine: line };
        lines.push({ line, raw, kind: "participants", entryId: participantTarget?.entryId || null });
        participantTarget = null;
        return;
      }

      const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
      const list = trimmed.match(/^(?:-\s*|▶\s+)(.+)$/);
      const prefix = heading ? "chapter" : list ? "entry" : null;
      const body = heading ? heading[2] : list ? list[1] : "";
      const match = prefix ? body.match(timedLine) : null;
      if (!match) {
        lines.push({ line, raw, kind: "unparsed" });
        if (prefix) issue(line, "invalid_timed_entry");
        participantTarget = null;
        detailTarget = null;
        return;
      }

      if (prefix === 'chapter' && parseTime(match[1]) !== null && invalidRangeEnd(body.slice(match[1].length))) {
        issue(line, 'invalid_end');
        lines.push({line, raw, kind:'unparsed'});
        participantTarget = detailTarget = null;
        return;
      }

      const startSec = parseTime(match[1]);
      const explicitEndSec = match[2] ? parseTime(match[2]) : null;
      if (startSec === null || (match[2] && explicitEndSec === null)) {
        issue(line, "invalid_time");
        lines.push({ line, raw, kind: "unparsed" });
        participantTarget = null;
        detailTarget = null;
        return;
      }
      let title = match[3].trim();
      const starred = /^(?:⭐|★)|^BEST\s+|(?:⭐|★)$|\sBEST$/i.test(title) ||
        (options.compatibleInput && /[⭐★]/u.test(title));
      title = title.replace(/^(?:⭐|★)\s*|^BEST\s+/i, "")
        .replace(/(?:⭐|★)$|\sBEST$/i, "").trim();
      if (options.compatibleInput) title = title.replace(/[⭐★]/gu, '').trim();
      const tagMatch = title.match(/^\[([^\]]+)\]\s*(.*)$/);
      const tag = tagMatch?.[1].trim() || "";
      let role = tag ? tags.get(tag.toUpperCase()) : null;
      // Natural comments can use an author's [H] label without an end time.
      // Keep its explicit clock as a point and leave the label in the raw title.
      if (options.compatibleInput && !match[2] &&
          ['highlight', 'expected_highlight', 'collab_context'].includes(role)) role = null;
      if (tagMatch && (role || !options.compatibleInput)) title = tagMatch[2].trim();
      if (tag && !role && !options.compatibleInput) issue(line, "unknown_tag");
      if (/^\[[^\]]+\]/.test(title) && !options.compatibleInput) issue(line, "conflicting_tags");
      const inlineRoster = heading ? title.match(/\(합방\s*:\s*([^)]*)\)$/) : null;
      if (inlineRoster) title = title.slice(0, inlineRoster.index).trim();
      if (!title) issue(line, "missing_title");

      const depth = heading ? heading[1].length : null;
      if (depth !== null) while (stack.length && stack.at(-1).depth >= depth) stack.pop();
      let parent = stack.at(-1) || null;
      const threadSources = options.logicalThreadSourceLines;
      const withinDeclaredParents = candidate => {
        for (let ancestor = candidate; ancestor; ancestor = entries.find(entry => entry.entryId === ancestor.parentId)) {
          if (ancestor.explicitEndSec !== null && startSec >= ancestor.explicitEndSec) return false;
        }
        return true;
      };
      if (threadSources && (!parent || threadSources[parent.sourceLine - 1] !== threadSources[line - 1] || !withinDeclaredParents(parent))) {
        // A later reply can continue an earlier chapter even when the root
        // already lists a future chapter. Its own time selects that context.
        parent = entries.filter(candidate => candidate.role === 'chapter' && candidate.sourceLine > hierarchyBarrierLine && sameScope(candidate, {sourceLine:line}) &&
          (depth === null || candidate.depth < depth) && candidate.startSec <= startSec && withinDeclaredParents(candidate) &&
          !entries.some(next => next.role === 'chapter' && sameScope(next, candidate) && next.depth <= candidate.depth &&
            next.startSec > candidate.startSec && next.startSec <= startSec))
          .sort((a, b) => b.depth - a.depth || b.startSec - a.startSec)[0] || null;
      }
      if (depth > 1 && parent?.depth !== depth - 1 && !(hierarchyBarrierLine && !parent)) issue(line, "missing_parent");
      const interval = Boolean(prefix === "chapter" || match[2] || role === "highlight"
        || role === "expected_highlight" || role === "collab_context");
      const normalizedRole = prefix === "chapter" ? "chapter" :
        role === "expected_highlight" ? "highlight" : role ||
        (interval || tag ? "unclassified" : "point");
      if (prefix === "entry" && interval && !role) issue(line, "range_role_required");
      if (prefix === "entry" && role === "point" && interval) issue(line, "point_cannot_be_range");
      if (prefix === "entry" && (role === "highlight" || role === "expected_highlight") && !match[2]) {
        issue(line, "highlight_end_required");
      }
      if (role === "collab_context" && !match[2]) issue(line, "collab_end_required");
      if (explicitEndSec !== null && explicitEndSec <= startSec) issue(line, "invalid_end");
      if (durationSec !== null && (startSec > durationSec || (explicitEndSec !== null && explicitEndSec > durationSec))) {
        issue(line, "outside_media");
      }
      const entry = {
        entryId: `${sourceRevision}:${line}`, sourceLine: line, raw, title, depth, detailLines: [], sourceSpans: [],
        parentId: parent?.entryId || null, geometry: interval ? "interval" : "moment",
        role: normalizedRole, startSec, atSec: interval ? null : startSec,
        endMode: interval ? (explicitEndSec !== null ? "explicit" :
          prefix === "chapter" ? "auto" : "unresolved") : null,
        explicitEndSec, resolvedEndSec: explicitEndSec, endBasis: explicitEndSec !== null ? "explicit" : "unknown",
        importance: starred ? "starred" : "normal", participants: null,
        selectionStatus: role === "expected_highlight" ? "candidate" : "unspecified",
        effectiveParticipants: null, inheritedFromEntryId: null,
        sourceRevision, sourceVodId: String(options.videoNo || "")
      };
      if (inlineRoster) {
        const participants = parseParticipants(inlineRoster[1]);
        if (participants.mode === "invalid") issue(line, "participants_invalid");
        else entry.participants = { ...participants, sourceLine: line };
      }
      entries.push(entry);
      if (depth !== null && ((depth > 1 && !parent && hierarchyBarrierLine) || orphanedChapters.has(parent?.entryId))) {
        orphanedChapters.add(entry.entryId);
      }
      lines.push({ line, raw, kind: prefix, entryId: entry.entryId });
      if (depth !== null) stack.push(entry);
      participantTarget = depth !== null || role === "collab_context" ? entry : null;
      detailTarget = entry;
    });

    const chapters = entries.filter((entry) => entry.role === "chapter");
    const nextChapter = chapter => {
      const next = chapters.find(candidate => sameScope(candidate, chapter) && candidate.sourceLine > chapter.sourceLine && candidate.depth <= chapter.depth &&
        (!options.logicalThreadSourceLines || options.logicalThreadSourceLines[candidate.sourceLine - 1] === options.logicalThreadSourceLines[chapter.sourceLine - 1]));
      if (!options.logicalThreadSourceLines || (next && next.startSec <= chapter.startSec)) return next;
      const threadNext = chapters.filter(candidate => sameScope(candidate, chapter) && candidate.depth <= chapter.depth && candidate.startSec > chapter.startSec &&
        options.logicalThreadSourceLines[candidate.sourceLine - 1] !== options.logicalThreadSourceLines[chapter.sourceLine - 1])
        .sort((a, b) => a.startSec - b.startSec)[0];
      return threadNext && (!next || threadNext.startSec < next.startSec) ? threadNext : next;
    };
    for (const chapter of chapters) {
      if (chapter.endMode === "explicit") continue;
      const next = nextChapter(chapter);
      const parent = chapters.find((candidate) => candidate.entryId === chapter.parentId);
      if (next && continuousLines) {
        if (next.startSec <= chapter.startSec) {
          issue(chapter.sourceLine, "chapter_order_conflict");
          issue(chapter.sourceLine, "empty_or_reversed_chapter");
        }
        else {
          if (parent?.resolvedEndSec !== null && parent?.resolvedEndSec !== undefined &&
            parent.resolvedEndSec < next.startSec) {
            chapter.resolvedEndSec = parent.resolvedEndSec;
            chapter.endBasis = "parent_end";
          } else {
            chapter.resolvedEndSec = next.startSec;
            chapter.endBasis = options.logicalThreadSourceLines &&
              options.logicalThreadSourceLines[next.sourceLine - 1] !== options.logicalThreadSourceLines[chapter.sourceLine - 1]
              ? 'next_thread_heading' : 'next_heading';
            chapter.boundarySourceEntryId = next.entryId;
          }
        }
      } else {
        if (parent?.resolvedEndSec !== null && parent?.resolvedEndSec !== undefined) {
          chapter.resolvedEndSec = parent.resolvedEndSec;
          chapter.endBasis = "parent_end";
        } else if (completeness === "complete" && durationSec !== null && !orphanedChapters.has(chapter.entryId) &&
            (!scopes || options.mediaEndScopes?.includes(scopes[chapter.sourceLine - 1]))) {
          chapter.resolvedEndSec = durationSec;
          chapter.endBasis = "media_end";
        }
      }
    }
    const byId = new Map(entries.map((entry) => [entry.entryId, entry]));
    for (const entry of entries) {
      if (entry.participants) {
        entry.effectiveParticipants = entry.participants;
      } else if (entry.parentId) {
        const parent = byId.get(entry.parentId);
        entry.effectiveParticipants = parent?.effectiveParticipants || null;
        entry.inheritedFromEntryId = parent?.participants ? parent.entryId : parent?.inheritedFromEntryId || null;
      }
    }
    for (const entry of entries) {
      if (entry.role === "chapter" || !entry.parentId) continue;
      const parent = chapters.find((candidate) => candidate.entryId === entry.parentId);
      if (parent && (entry.startSec < parent.startSec ||
        (parent.resolvedEndSec !== null && entry.startSec >= parent.resolvedEndSec))) {
        issue(entry.sourceLine, "outside_parent");
      }
    }
    for (const chapter of chapters) {
      const parent = chapters.find((candidate) => candidate.entryId === chapter.parentId);
      if (parent && (chapter.startSec < parent.startSec ||
        (parent.resolvedEndSec !== null && parent.resolvedEndSec !== undefined &&
          (chapter.startSec >= parent.resolvedEndSec ||
            (chapter.resolvedEndSec !== null && chapter.resolvedEndSec > parent.resolvedEndSec))))) {
        issue(chapter.sourceLine, "outside_parent");
      }
      const next = nextChapter(chapter);
      if (chapter.endMode === "explicit" && next && next.startSec < chapter.explicitEndSec) {
        issue(chapter.sourceLine, "explicit_overlap");
      }
      if (chapter.resolvedEndSec !== null && chapter.resolvedEndSec <= chapter.startSec) {
        issue(chapter.sourceLine, "empty_or_reversed_chapter");
      }
    }
    return { schemaVersion: VERSION, sourceRevision, sourceVodId: String(options.videoNo || ""),
      completeness, durationSec, lines, entries, diagnostics, documentRosters:headerRoster ? [headerRoster] : [] };
  }

  // A part must make sense on its own: repeat ancestor ranges and retain whole entry blocks.
  function splitComment(input, maxChars = 5000, options = {}) {
    if (!Number.isInteger(maxChars) || maxChars < 100 || maxChars > MAX_CHARS) throw new RangeError('invalid part limit');
    const raw = String(input || '').replace(/\r\n?/g, '\n');
    const parsed = parseComment(raw, options);
    if (parsed.diagnostics.length || !parsed.entries.length) throw new Error('fix comment diagnostics before splitting');
    const rawLines = raw.split('\n'), byId = new Map(parsed.entries.map((entry) => [entry.entryId, entry]));
    const prefix = rawLines.slice(0, parsed.entries[0].sourceLine - 1).join('\n').trimEnd();
    const blocks = new Map(parsed.entries.map((entry, index) => {
      const next = parsed.entries[index + 1];
      const lines = rawLines.slice(entry.sourceLine - 1, next ? next.sourceLine - 1 : rawLines.length);
      if (entry.role === 'chapter' && entry.endMode !== 'explicit') {
        if (!Number.isInteger(entry.resolvedEndSec)) throw new Error('chapter end is unknown');
        lines[0] = '#'.repeat(entry.depth) + ' ' + formatTime(entry.startSec) + ' ~ ' + formatTime(entry.resolvedEndSec) + ' ' +
          (entry.importance === 'starred' ? '⭐ ' : '') + entry.title;
        if (entry.participants?.sourceLine === entry.sourceLine) {
          lines.splice(1, 0, '합방: ' + (entry.participants.mode === 'declared' ? entry.participants.names.join(', ') : entry.participants.mode === 'none' ? '없음' : '미확인'));
        }
      }
      return [entry.entryId, lines.join('\n').trimEnd()];
    }));
    const parts = []; let current = [], present = new Set(), originals = [];
    const textFor = (items) => [prefix, ...items].filter(Boolean).join('\n') + '\n';
    const finish = () => { if (originals.length) parts.push({ text: textFor(current), entryIds: originals.slice() }); };
    for (const entry of parsed.entries) {
      const ancestors = []; let parent = byId.get(entry.parentId);
      while (parent) { ancestors.unshift(parent); parent = byId.get(parent.parentId); }
      let missing = ancestors.filter((ancestor) => !present.has(ancestor.entryId));
      let additions = [...missing.map((ancestor) => blocks.get(ancestor.entryId)), blocks.get(entry.entryId)];
      if (textFor([...current, ...additions]).length > maxChars && originals.length) {
        finish(); current = []; present = new Set(); originals = [];
        missing = ancestors; additions = [...ancestors.map((ancestor) => blocks.get(ancestor.entryId)), blocks.get(entry.entryId)];
      }
      if (textFor([...current, ...additions]).length > maxChars) throw new Error(`entry ${entry.sourceLine} and its context exceed ${maxChars} characters`);
      current.push(...additions); missing.forEach((ancestor) => present.add(ancestor.entryId)); present.add(entry.entryId); originals.push(entry.entryId);
    }
    finish();
    return { parts, originalEntryCount: parsed.entries.length, maxChars };
  }

  function parseComments(documents, options = {}) {
    if (!Array.isArray(documents) || !documents.length || documents.length > 100 ||
        documents.some((doc) => typeof doc.raw !== 'string') || documents.reduce((sum, doc) => sum + doc.raw.length, 0) > MAX_CHARS) {
      throw new RangeError('invalid comment collection');
    }
    const parsed = documents.map((doc, index) => ({ name: String(doc.name || `댓글 ${index + 1}`).slice(0, 120),
      documentId: `comment-${index + 1}`, ...parseComment(doc.raw, { ...options,
        sourceRevision: `${options.sourceRevision || 'unversioned'}:comment-${index + 1}` }) }));
    return { documents: parsed, entries: parsed.flatMap((doc) => doc.entries.map((entry) => ({ ...entry,
      documentId: doc.documentId, documentName: doc.name }))),
      diagnostics: parsed.flatMap((doc) => doc.diagnostics.map((item) => ({ ...item, documentName: doc.name }))) };
  }

  // Existing CHZZK comments are prose with timecodes, not necessarily our suggested Markdown.
  // Keep the source document and line identity while deriving only the times we can locate safely.
  function compatibleLexLine(raw) {
    // Unwrap only complete emphasis spans around clocks, never arbitrary Markdown/HTML.
    let line = String(raw).replace(/&(?:amp;)?(?:#x20|#32|nbsp);/gi,' ')
      // Copied Markdown hardbreaks are syntax in this projection; source stays exact.
      .replace(/(^|[^\\])\\\s*$/,'$1')
      .replace(/^(\s*)\\(?=#{1,6}(?:\s|\d+:)|-{1,2}\s*\d+:\d{2})/,'$1')
      .replace(/(\*\*|__|\*|_)(\d+:\d{2}(?::\d{2})?)\1/g,'$2')
      .replace(/^(\s*)\d+[.)]\s+(?=(?:[\[(【]\s*)?\d+:)/,'$1')
      .replace(/^(\s*)[+*]\s+(?=(?:[\[(【]\s*)?\d+:|\()/,'$1- ');
    // A complete leading roster may precede the chapter's leading clock.
    // Reorder only this lexical projection; never search prose for a later time.
    const front=line.match(/^(\s*(?:#{1,6}\s*|(?:-{1,2}|[•·▶└ㄴ])\s*)?)(?=\()/);
    if(front) {
      let depth=0, end=-1;
      for(let i=front[0].length;i<line.length;i++) {
        if(line[i]==='(')depth++;
        else if(line[i]===')' && --depth===0) {end=i+1;break;}
      }
      if(end>0) {
        const rosterText=line.slice(front[0].length,end), body=rosterText.slice(1,-1).trim();
        const withPattern=/^(?:w\.|w\/|with\b)\s*:?\s*/i;
        const roster=splitRosterNames(body.replace(withPattern,''),true);
        let tail=line.slice(end).trimStart(), prefix=front[1];
        const marker=tail.match(/^(#{1,6}|-{1,2}|[•·▶└ㄴ]|[+*]|\d+[.)])\s*(?=(?:[\[(【]\s*)?\d+:)/);
        if(marker && !prefix.trim()) {
          prefix+=/^\d/.test(marker[1])?'':/[+*]/.test(marker[1])?'- ':marker[1]+' ';
          tail=tail.slice(marker[0].length);
        }
        const clockWrapper=tail.match(/^(\[|\(|【)(\d+:\d{2}(?::\d{2})?)(\]|\)|】)(?=\s)/);
        if(clockWrapper && ({'[':']','(':')','【':'】'})[clockWrapper[1]]===clockWrapper[3])
          tail=clockWrapper[2]+tail.slice(clockWrapper[0].length);
        const clock=tail.match(timedLine);
        if(roster && !roster.invalid && roster.names.length>=(withPattern.test(body)?1:2) && clock &&
          parseTime(clock[1])!==null && (!clock[2] || parseTime(clock[2])!==null) && collaborationTitle(clock[3])) {
          line=prefix+clock[1]+(clock[2]?' ~ '+clock[2]:'')+' '+rosterText+' '+clock[3];
        }
      }
    }
    // A complete clock-only wrapper is another leading clock, not a search
    // for times inside prose. Keep indentation and existing structural markers.
    const wrapped = line.match(/^(\s*(?:#{1,6}\s*|(?:-{1,2}|[•·▶└ㄴ])\s*)?)([\[(【])\s*(\d+:[\d:.]*(?:\s*(?:~|–|—|-)\s*\d+:[\d:.]*)?)\s*([\])】])(?=\s|$|:\s+\S)/);
    let incompleteWrappedClock = false;
    if (wrapped && ({'[':']','(':')','【':'】'})[wrapped[2]] === wrapped[4]) {
      line = wrapped[1] + wrapped[3] + line.slice(wrapped[0].length);
      incompleteWrappedClock = /:$/.test(wrapped[3]);
    }
    const separator = !incompleteWrappedClock && line.match(/^(\s*(?:#{1,6}\s*|(?:-{1,2}|[•·▶└ㄴ])\s*)?)(\d+:\d{2}(?::\d{2})?)\s*:\s+(\S.*)$/);
    if (separator && parseTime(separator[2]) !== null &&
        !/^(?:[\[(【]\s*)?\d+:/.test(separator[3]) && !/^\d+(?:\.\d+)?(?:\s|$)/.test(separator[3])) {
      // A colon followed by a nonclock title is a label separator. Numeric or
      // clock-like tails can be a damaged clock and must keep the error path.
      line = separator[1] + separator[2] + ' ' + separator[3];
    }
    return line.replace(/^(\s*#{1,6})(?=(?:\*{1,2}|_{1,2})?\d+:)/,'$1 ');
  }
  function compatibleTimeLine(raw, parentAuthor = '') {
    const line = compatibleLexLine(raw).trim();
    const bareBullet = line.match(/^(?:-|▶)\s*(\d+:\d{2}(?::\d{2})?)\s*$/);
    if (bareBullet) return parseTime(bareBullet[1]) === null ? { error: 'invalid_time' } :
      { times: [parseTime(bareBullet[1])], end: null, title: '', pattern: 'leading' };
    if (!line || /^#{1,6}\s+/.test(line) ||
        /^(?:설명|맥락|검토 상태|원본 구간|합방|함께|참여)\s*:/.test(line)) return null;
    const time = '\\d+:\\d{2}(?::\\d{2})?(?![\\d:]|\\.\\d)';
    const bracketAnchors = [...line.matchAll(new RegExp(`\\[\\s*(${time})\\s*\\]`, 'g'))];
    if (bracketAnchors.length > 1) {
      const clocks = bracketAnchors.map((match) => parseTime(match[1]));
      const titles = bracketAnchors.map((match, index) => line.slice(
        index ? bracketAnchors[index - 1].index + bracketAnchors[index - 1][0].length : 0,
        match.index).replace(/^[\s·•/()]+|[\s·•/()]+$/g, '').trim());
      const valid = clocks.map((seconds, occurrence) => ({ seconds, occurrence, title: titles[occurrence] }))
        .filter((item) => item.seconds !== null);
      if (!valid.length) return { error: 'invalid_time' };
      return { times: valid.map((item) => item.seconds), titles: valid.map((item) => item.title),
        occurrences: valid.map((item) => item.occurrence), end: null, title: '', pattern: 'bracket_multiple',
        warning: valid.length < clocks.length ? 'invalid_time' : null };
    }
    const slashParts = line.split(new RegExp(`\\s+\\/\\s+(?=${time}(?:\\s|$))`));
    if (slashParts.length > 1) {
      const parts = slashParts.map((part) => part.match(new RegExp(`^(?:(?:-{1,2}|[•·▶└ㄴ])\\s*)?(${time})\\s*(.*)$`)));
      const times = parts.map((part) => part && parseTime(part[1]));
      if (parts.every(Boolean) && parts[0][2].trim()) {
        const valid = times.map((seconds, occurrence) => ({ seconds, occurrence, title: parts[occurrence][2].trim() }))
          .filter((item) => item.seconds !== null);
        if (!valid.length) return { error: 'invalid_time' };
        return { times: valid.map((item) => item.seconds), titles: valid.map((item) => item.title),
          occurrences: valid.map((item) => item.occurrence), end: null, title: '', pattern: 'inline_multiple',
          warning: valid.length < times.length ? 'invalid_time' : null };
      }
    }
    // CHZZK can prepend the parent author's name directly to a reply's timecode.
    // Interpret that clock only when the selected parent document supplies the name.
    const mention = parentAuthor && (line.startsWith(`@${parentAuthor}`) ? `@${parentAuthor}` :
      line.startsWith(parentAuthor) ? parentAuthor : '');
    const mentionedTime = mention && line.slice(mention.length).match(new RegExp(`^\\s*(${time})\\s+(.+)$`));
    if (mentionedTime) {
      const seconds = parseTime(mentionedTime[1]);
      return seconds === null ? { error: 'invalid_time' } :
        { times: [seconds], end: null, title: mentionedTime[2].trim(), pattern: 'reply_mention' };
    }
    const leading = line.match(new RegExp(`^(?:(?:-{1,2}|[•·▶└ㄴ])\\s*)?(${time})(?:\\s*(?:~|–|—|-)\\s*(${time}))?\\s*(.*)$`));
    if (leading) {
      const first = parseTime(leading[1]), end = leading[2] ? parseTime(leading[2]) : null;
      const rest = leading[3].trim();
      if (leading[2] && first === null) return { error: 'invalid_time' };
      if (!leading[2] && first !== null && invalidRangeEnd(rest)) {
        return { times:[first], end:null, title:rest, pattern:'recovered_point', warning:'invalid_end' };
      }
      if (leading[2] && (end === null || end <= first)) {
        return { times: [first], end: null, title: rest, pattern: 'recovered_point', warning: 'invalid_end' };
      }
      const spaceSeparated = !leading[2] && rest.match(new RegExp(`^(${time}(?:\\s+${time})*)$`));
      // A slash enumerates distinct moments. A dash/tilde above is an explicit range.
      const more = rest.match(new RegExp(`^/\\s*(${time}(?:\\s*/\\s*${time})*)\\s*(.*)$`));
      const clocks = spaceSeparated ? [first, ...spaceSeparated[1].split(/\s+/).map(parseTime)] :
        more ? [first, ...more[1].split(/\s*\/\s*/).map(parseTime)] : [first];
      const valid = clocks.map((seconds, occurrence) => ({ seconds, occurrence }))
        .filter((item) => item.seconds !== null);
      if (!valid.length) return { error: 'invalid_time' };
      const title = (spaceSeparated ? '' : more ? more[2] : rest).trim();
      // A trailing bare clock can be a cue, correction, or duration. Keep it in
      // the original title and request review instead of inventing an anchor/end.
      const reference = !leading[2] && clocks.length === 1 && title.match(new RegExp(`\\(\\s*(${time})\\s*\\)\\s*$`));
      const referenceTimeSec = reference ? parseTime(reference[1]) : null;
      return { times: valid.map((item) => item.seconds), occurrences: valid.map((item) => item.occurrence),
        end, title, pattern: end !== null ? 'range' : clocks.length > 1 ? 'multiple' : 'leading',
        referenceTimeSec,
        warning: valid.length < clocks.length ? 'invalid_time' : referenceTimeSec !== null ? 'parenthesized_time_review' : null };
    }
    if (/^(?:(?:-{1,2}|[•·▶└ㄴ])\s*)?\d+:/.test(line)) return {error:'invalid_time'};
    const leadingRole = tags.has(line.match(/^\[([^\]]+)\]/)?.[1].toUpperCase());
    const unwrapped = leadingRole ? line : line.replace(/^\[\s*(.*?)\s*\]$/, '$1');
    const trailing = unwrapped.match(new RegExp(`^(.+?)\\s*(?:[|–—-]\\s*|\\[\\s*)(${time}(?:\\s*[/~–—-]\\s*${time})*)\\s*\\]?$`));
    if (!trailing) return null;
    const title = trailing[1].trim().replace(/^(?:-{1,2}|[•·▶└ㄴ])\s+/, '').replace(/[·•\s]+$/, '');
    const expression = trailing[2];
    const range = expression.match(new RegExp(`^(${time})\\s*(?:~|–|—|-)\\s*(${time})$`));
    const clocks = (range ? [range[1]] : expression.split(/\s*\/\s*/)).map(parseTime);
    const end = range ? parseTime(range[2]) : null;
    if (!title || (range && clocks[0] === null)) return { error: 'invalid_time' };
    if (range && (end === null || end <= clocks[0])) {
      return { times: [clocks[0]], end: null, title, pattern: 'recovered_point', warning: 'invalid_end' };
    }
    const valid = clocks.map((seconds, occurrence) => ({ seconds, occurrence }))
      .filter((item) => item.seconds !== null);
    if (!valid.length) return { error: 'invalid_time' };
    return { times: valid.map((item) => item.seconds), occurrences: valid.map((item) => item.occurrence),
      end, title, pattern: range ? 'range' : clocks.length > 1 ? 'multiple' : 'trailing',
      warning: valid.length < clocks.length ? 'invalid_time' : null };
  }

  function naturalOutlinePlan(rawLines, parentAuthor) {
    const rows = rawLines.map(raw => {
      const lex=compatibleLexLine(raw), heading=lex.trim().match(/^(#{1,6})\s+(.+)$/);
      const clock=heading ? compatibleTimeLine(heading[2],parentAuthor) : null;
      const explicitClock=heading && timedLine.test(heading[2]) && clock && !clock.error && clock.times.length===1 &&
        clock.title && ['leading','range'].includes(clock.pattern) ? clock : null;
      return {raw,lex,candidate:compatibleTimeLine(raw,parentAuthor),heading:heading ? {
        depth:heading[1].length,title:heading[2],clock,explicitClock
      } : null};
    });
    const hasExplicit=rows.some(row=>/^#{1,6}\s+\d+:/.test(row.lex.trim()));
    const marker = row => row.lex.trim().match(/^(--?|[•·▶└ㄴ])(?=\s|\d)/)?.[1] || '';
    const isPlain = row => ['leading','trailing'].includes(row.candidate?.pattern) && !row.candidate.error &&
      !marker(row) && Boolean(row.candidate.title);
    const indentation = row => [...(row.lex.match(/^\s*/)?.[0] || '')]
      .reduce((width, char) => width + (char === '\t' ? 4 : char === '\u3000' ? 2 : 1), 0);
    const plainRows = rows.filter(isPlain);
    const commonIndent = plainRows.length ? Math.min(...plainRows.map(indentation)) : 0;
    const childIndents = plainRows.map(row => indentation(row) - commonIndent).filter(width => width >= 2);
    const sceneIndent = childIndents.length ? Math.min(...childIndents) : null;
    const relativeIndent = row => isPlain(row) ? indentation(row) - commonIndent : 0;
    const isIndented = row => sceneIndent !== null && relativeIndent(row) >= sceneIndent;
    const isTop = row => isPlain(row) && !isIndented(row);
    const isChild = row => row.candidate && (['-','--','└','ㄴ'].includes(marker(row)) || isIndented(row));
    const hasTopAndList = rows.some(isTop) && rows.some(isChild);
    const hasSceneList = rows.some(isTop) && rows.some(row=>row.candidate &&
      (marker(row)==='-' || isIndented(row)));
    const compact = title => title.normalize('NFKC').replace(/[\s⭐★]/gu,'').toLowerCase();
    const explicitRole=row=>tags.has(row.candidate?.title.match(/^\[([^\]]+)\]/)?.[1].toUpperCase());
    const topicBoundary=title=>collaborationEndTitle(title) || inactiveCollaborationTitle(title) || comparisonTitle(title);
    const validClock=row=>row.candidate && !row.candidate.error && row.candidate.times.length===1 &&
      ['leading','trailing','range'].includes(row.candidate.pattern);
    const metadata=row=>!row.candidate && (Boolean(adjacentRoster(row.lex.trim(),1)) ||
      /^(?:합방|함께|참여|별칭)\s*:/.test(row.lex.trim()));
    const plan=new Map();let nextHeading=null;
    // Explicit untimed containers borrow only a real, immediately deeper heading's
    // clock. Their own source row stays the title; same-depth labels stay literal.
    for(let index=rows.length-1;index>=0;index--) {
      const row=rows[index], heading=row.heading, clock=heading?.explicitClock;
      if(clock && !clock.warning) {
        nextHeading={depth:heading.depth,seconds:clock.times[0],line:index+1};
      } else if(heading && !heading.clock && !/^(?:\*{1,2}|_{1,2})?\d+:/.test(heading.title) &&
          nextHeading?.depth===heading.depth+1) {
        plan.set(index,{prefix:'#'.repeat(heading.depth),title:heading.title,anchorSec:nextHeading.seconds,
          inference:{basis:'explicit_untimed_parent',sourceLine:index+1,anchorLine:nextHeading.line}});
        nextHeading={...nextHeading,depth:heading.depth};
      } else if(row.lex.trim() && !metadata(row))nextHeading=null;
    }
    // One reverse pass supplies bounded lookahead. Prose, damaged clocks and
    // explicit hierarchy are barriers; blank lines and roster metadata are not.
    const following=new Array(rows.length+1);
    following[rows.length]={rounds:0,treeRounds:0,treeIndent:null,bullets:0,peers:null,nested:false,first:null};
    const peerForm=row=>`${marker(row)}:${indentation(row)}:${row.candidate.pattern}`;
    for(let index=rows.length-1;index>=0;index--) {
      const row=rows[index], next=following[index+1];
      const tree=['└','ㄴ'].includes(marker(row)), indent=indentation(row);
      const peerRound=(tree || isPlain(row)) && row.candidate?.end===null && !explicitRole(row) && collaborationContinuationTitle(row.candidate.title);
      const peer=tree || peerRound, treeIndent=peer ? Math.min(indent,next.treeIndent ?? Infinity) : next.treeIndent;
      const treeRounds=peer && (next.treeIndent===null || indent<next.treeIndent) ? Number(peerRound) :
        next.treeRounds+Number(peerRound && indent===next.treeIndent);
      const eligible=validClock(row) && row.candidate.end===null && !row.candidate.warning && !explicitRole(row);
      const peers=eligible ? {form:peerForm(row),first:row.candidate.times[0],
        count:next.peers?.form===peerForm(row) && row.candidate.times[0]<next.peers.first ? next.peers.count+1 : 1} :
        row.candidate && !row.candidate.error && (explicitRole(row) || row.candidate.times.length>1 || row.candidate.end!==null) ? next.peers : null;
      following[index]=validClock(row) ? {
        rounds:next.rounds+Number(row.candidate.end===null && !explicitRole(row) &&
          !['--','└','ㄴ'].includes(marker(row)) &&
          (!isIndented(row) || relativeIndent(row)===sceneIndent) && collaborationContinuationTitle(row.candidate.title)),
        treeRounds:isTop(row) && !collaborationContinuationTitle(row.candidate.title) ? 0 : treeRounds,
        treeIndent:isTop(row) && !collaborationContinuationTitle(row.candidate.title) ? null : treeIndent,
        bullets:next.bullets+Number(marker(row)==='-'),
        peers,
        nested:next.nested || ['--','└','ㄴ'].includes(marker(row)),first:row
      } : !row.lex.trim() || metadata(row) ? next : {rounds:0,treeRounds:0,treeIndent:null,bullets:0,
        peers:row.candidate && !row.candidate.error && row.candidate.times.length>1 ? next.peers : null,nested:false,first:null};
    }
    const treePeers=next=>next.treeRounds>=2 && next.first && ['└','ㄴ'].includes(marker(next.first)) ? next.treeIndent : null;
    function category(row, index) {
      if(row.heading?.depth>1)return null;
      const text=row.lex.trim().replace(/^#{1,6}\s+(?!\d+:)/,'');
      if (!text || text.length>240 || /^#|^\d+[/.]\d+|[.?。]$/.test(text) ||
          comparisonTitle(text) || inactiveCollaborationTitle(text) ||
          row.candidate?.error || explicitRole(row)) return null;
      const title=row.candidate?.title || text;
      if (/^[💬🎮]/u.test(text)) return {title, basis:'category_symbol'};
      const next=following[index+1];
      const treeSceneIndent=treePeers(next), repeatedRounds=next.rounds>=2 || treeSceneIndent!==null;
      if(row.candidate) return row.candidate.end===null && collaborationAnchorTitle(title) && repeatedRounds ?
        {title,basis:'semantic_collaboration_title',semantic:true,treeSceneIndent} : null;
      if(/^(?:합방|함께|참여|별칭)\s*:/.test(text))return null;
      const roster=participantCandidates(text,index+1,true);
      const label=(roster?text.slice(0,-roster.raw.length):text).trim();
      if(collaborationAnchorTitle(text) && next.first &&
          (collaborationContinuationTitle(next.first.candidate.title) || isChild(next.first)))
        return {title:text,basis:'semantic_collaboration_title',semantic:true,treeSceneIndent};
      const pure=adjacentRoster(text,index+1);
      if(repeatedRounds && (collaborationContinuationTitle(next.first?.candidate.title || '') || treeSceneIndent!==null)) {
        if(pure)return {title:'합방 '+text,basis:'semantic_roster_and_rounds',semantic:true,treeSceneIndent};
        // A short standalone heading is structural evidence, not a dictionary
        // of games. Sentences and malformed roster wrappers cannot qualify.
        if(label && label.length<=64 && !/[.!?。]|^[(-]|[()]$/.test(label) &&
            (!/[()]/.test(text) || roster))
          return {title:text,basis:'semantic_heading_and_rounds',semantic:true,treeSceneIndent};
      }
      if(pure)return null;
      if(/합방\s*시작$/.test(label) && next.first)
        return {title:text,basis:'collaboration_title'};
      // A short free heading followed by repeated root bullets and nested moments
      // supplies a structural topic, irrespective of the game's/name's vocabulary.
      if (next.bullets>=2 && next.nested) {
        return {title:text,basis:'heading_and_nested_list'};
      }
      if (roster && label.length<=64 && next.first?.candidate?.title && isChild(next.first) &&
          (/게임(?:\s|$)/.test(label) || compact(next.first.candidate.title).startsWith(compact(label)))) return {title:text,basis:'game_title_and_scene_list'};
      // A named local group owns its coherent peer list, irrespective of topic
      // vocabulary or clock location. Bare playlists have no parent evidence.
      if(!row.candidate && label.length<=64 && /[\p{L}\p{N}]/u.test(label) &&
          !/^[*\-]|[(){}.,?:;。]/.test(label) && next.peers?.count>=2)
        return {title:text,basis:'heading_and_peer_list',peerForm:next.peers.form};
      return null;
    }
    const categories=rows.map(category);
    const nextNonblank=new Array(rows.length);let nextIndex=null;
    for(let index=rows.length-1;index>=0;index--) {
      nextNonblank[index]=nextIndex;if(rows[index].lex.trim())nextIndex=index;
    }
    // A clocked topic immediately before a named group is its preceding peer.
    // Extend only through that adjacent run, never across prose or sections.
    for(let index=rows.length-1;index>=0;index--) {
      const nextAt=nextNonblank[index], next=categories[nextAt];
      if(isPlain(rows[index]) && !explicitRole(rows[index]) && rows[index].candidate.end===null && !rows[index].candidate.warning &&
          (next?.peerForm || next?.basis==='adjacent_topic_outline' && peerForm(rows[index])===peerForm(rows[nextAt])) &&
          rows[index].candidate.times[0]<(next.peerForm ? following[nextAt+1].first?.candidate?.times?.[0] : rows[nextAt].candidate.times[0]))
        categories[index]={title:rows[index].candidate.title,basis:'adjacent_topic_outline'};
    }
    const legacyOutline=!hasExplicit && hasTopAndList;
    const explicitRoundTopic=rows.some((row,index)=>/^#\s+\d+:/.test(row.lex.trim()) &&
      (following[index+1].rounds>=2 && isPlain(following[index+1].first || {}) || treePeers(following[index+1])!==null));
    if (!legacyOutline && !categories.some(Boolean) && !explicitRoundTopic && !plan.size) return plan;
    let pending=null, categoryChildren=false, semantic=false, explicitTopic=false, treeSceneIndent=null, localPeers=false, groupForm=null, d1=null, d2=null;
    const depthTimes=[];
    const noteDepth=(depth,seconds)=>{depthTimes.length=depth;depthTimes[depth-1]=seconds;};
    rows.forEach((row,index)=>{
      const anchored=plan.get(index);
      if(anchored?.anchorSec!==undefined) {
        pending=null;semantic=false;categoryChildren=false;explicitTopic=false;treeSceneIndent=null;localPeers=false;groupForm=null;
        if(depthTimes[row.heading.depth-1]===anchored.anchorSec) {
          plan.set(index,{...anchored,prefix:'-',ambiguous:true,
            inference:{...anchored.inference,basis:'same_time_role_confirmation'}});
          depthTimes.length=0;d1=null;d2=null;
          return;
        }
        noteDepth(row.heading.depth,anchored.anchorSec);
        if(row.heading.depth===1){d1=anchored.anchorSec;d2=null;}
        else if(row.heading.depth===2)d2=anchored.anchorSec;
        return;
      }
      const heading=categories[index];
      if(heading && !row.candidate) {
        if(heading.basis==='semantic_roster_and_rounds' && (semantic || categoryChildren))return;
        pending={...heading,line:index+1,raw:row.raw};return;
      }
      const c=row.candidate;
      if(!validClock(row)) {
        const explicit=row.lex.trim().match(/^(#{1,6})\s+(\d+:\d{2}(?::\d{2})?)/);
        if(explicit) {pending=null;semantic=false;categoryChildren=false;treeSceneIndent=null;localPeers=false;groupForm=null;
          if(row.heading?.explicitClock)noteDepth(explicit[1].length,row.heading.explicitClock.times[0]);
          else depthTimes.length=0;
          if(explicit[1].length===1){d1=parseTime(explicit[2]);d2=null;
            treeSceneIndent=treePeers(following[index+1]);
            semantic=following[index+1].rounds>=2 && isPlain(following[index+1].first || {}) || treeSceneIndent!==null;
            explicitTopic=semantic;
          }
          else if(explicit[1].length===2)d2=parseTime(explicit[2]);
        } else if(localPeers && c && !c.error && c.times.length>1) {
          return; // Multiple clocks are moments inside the established group.
        } else if(row.lex.trim() && !metadata(row)) {
          if(semantic || pending)plan.set(index,{barrier:true,inference:{sourceLine:index+1,anchorLine:index+1}});
          pending=null;semantic=false;explicitTopic=false;categoryChildren=false;treeSceneIndent=null;localPeers=false;groupForm=null;depthTimes.length=0;d1=null;d2=null;
        }
        return;
      }
      const seconds=c.times[0];
      if(pending) {
        const duplicate=d1===seconds;
        const ambiguous=duplicate && pending.semantic;
        plan.set(`heading:${index}`,{prefix:duplicate?'-':'#',title:pending.title,raw:pending.raw,line:pending.line,ambiguous,
          inference:{basis:duplicate?'same_time_role_confirmation':pending.basis,sourceLine:pending.line,anchorLine:index+1}});
        if(!duplicate) {d1=seconds;d2=null;noteDepth(1,seconds);}
        if(ambiguous) {
          pending=null;semantic=false;explicitTopic=false;categoryChildren=false;depthTimes.length=0;d1=null;d2=null;
          plan.set(index,{prefix:'-',inference:{basis:'same_time_role_confirmation',sourceLine:index+1,anchorLine:index+1}});
          return;
        }
        semantic=Boolean(pending.semantic);explicitTopic=false;categoryChildren=Boolean(pending.peerForm) || semantic || !hasSceneList;
        localPeers=Boolean(pending.peerForm);groupForm=pending.peerForm || null;
        treeSceneIndent=pending.treeSceneIndent ?? null;pending=null;
      }
      if(!semantic && !legacyOutline && !heading && !categoryChildren && !localPeers)return;
      let prefix='-', basis='event_marker', detach=false;
      if(c.end!==null) basis='explicit_span';
      else if(tags.has(c.title.match(/^\[([^\]]+)\]/)?.[1].toUpperCase())) basis='explicit_role';
      else if(heading) {prefix='#';basis=heading.basis;semantic=Boolean(heading.semantic);explicitTopic=false;categoryChildren=semantic || !hasSceneList;}
      else if(semantic && topicBoundary(c.title)) {
        prefix='#';basis='semantic_topic_boundary';semantic=false;categoryChildren=false;
      }
      else if(treeSceneIndent!==null && ['└','ㄴ'].includes(marker(row)) && indentation(row)===treeSceneIndent) {
        prefix='##';basis='tree_peer_scene';
      }
      else if(['--','└','ㄴ'].includes(marker(row))) basis='event_marker';
      else if(treeSceneIndent!==null && isTop(row)) {
        if(collaborationContinuationTitle(c.title)) {prefix='##';basis='tree_peer_scene';}
        else {prefix='#';basis='top_level_outline';semantic=false;categoryChildren=false;explicitTopic=false;}
      }
      else if(explicitTopic && marker(row)==='-')basis='event_marker';
      else if(localPeers && categoryChildren && peerForm(row)!==groupForm && !isChild(row)) {
        const run=following[index].peers;
        if(run?.count>=2) {prefix='#';basis='peer_list_topic_boundary';categoryChildren=false;groupForm=null;}
        else basis='mixed_format_role_confirmation';
      }
      else if(semantic) {
        prefix=(collaborationContinuationTitle(c.title) || d2===null && isChild(row) && !/[⭐★]/u.test(c.title)) &&
          (!isIndented(row) || d2===null) ? '##' : '-';
        basis=prefix==='##'?'semantic_round_under_topic':'semantic_incident';
      }
      else if(isIndented(row)) {
        prefix=relativeIndent(row)===sceneIndent?'##':'-';
        basis=prefix==='##'?'relative_indentation_scene':'relative_indentation_event';
      }
      else if(c.title && (isTop(row) || marker(row)==='-')) {
        prefix=marker(row)==='-' || categoryChildren?'##':'#';
        basis=prefix==='##'?'scene_under_topic':'top_level_outline';
      }
      if(prefix==='##' && d1===null) {prefix='-';basis='missing_topic_role_confirmation';}
      if(prefix==='##') {
        const nextAt=nextNonblank[index], nextRow=rows[nextAt], nextHeading=categories[nextAt];
        const explicitNext=nextRow?.lex.trim().match(/^#\s+(\d+:\d{2}(?::\d{2})?)/);
        const boundary=nextHeading && !nextRow.candidate ? following[nextAt+1].first?.candidate?.times[0] :
          nextHeading || nextRow?.candidate && topicBoundary(nextRow.candidate.title) ? nextRow.candidate?.times[0] :
          explicitNext ? parseTime(explicitNext[1]) : null;
        if(boundary===seconds) {prefix='-';basis='same_time_role_confirmation';detach=true;}
      }
      if(prefix==='#' && d1===seconds) {prefix=d2===seconds?'-':'##';basis='same_time_role_confirmation';}
      if(prefix==='##' && d2===seconds) {prefix='-';basis='same_time_role_confirmation';}
      if(prefix==='#') {
        d1=seconds;d2=null;
        noteDepth(1,seconds);
        treeSceneIndent=heading?.treeSceneIndent ?? (basis==='top_level_outline'?treePeers(following[index+1]):null);
      }
      else if(prefix==='##') {d2=seconds;noteDepth(2,seconds);}
      plan.set(index,{prefix,detach,inference:{basis,sourceLine:index+1,anchorLine:index+1}});
    });
    return plan;
  }

  function isSectionBreak(raw) {
    const text = compatibleLexLine(raw).trim().replace(/^\\(?=[*_\-])/, '');
    return /^(?:\*\s*){3,}$|^(?:-\s*){3,}$|^(?:_\s*){3,}$|^■+$/.test(text);
  }

  // Read block boundaries before clocks. An independent index must not acquire
  // the hierarchy of a later scene list; ordinary blank lines are not boundaries.
  function commentSections(rawLines, parentAuthor) {
    const starts = [0];
    let previousClock = null;
    for (let index = 0; index < rawLines.length; index++) {
      const text = compatibleLexLine(rawLines[index]).trim();
      if (isSectionBreak(rawLines[index])) {
        if (index + 1 < rawLines.length) starts.push(index + 1);
        previousClock = null;
        continue;
      }
      const clock = compatibleTimeLine(rawLines[index], parentAuthor);
      if (!clock && /^(?:[💬🎮]|#{1,6}\s+\S)/u.test(text) && previousClock !== null) {
        const nextIndex = rawLines.findIndex((raw, at) => at > index && compatibleLexLine(raw).trim());
        const next = nextIndex >= 0 && compatibleTimeLine(rawLines[nextIndex], parentAuthor);
        if (next?.times?.[0] < previousClock) {
          starts.push(index);
          previousClock = null;
        }
      }
      if (clock?.times?.length) previousClock = clock.times.at(-1);
    }
    const boundaries = [...new Set(starts)].sort((a,b) => a-b);
    return boundaries.map((start, index) => ({start, end:boundaries[index + 1] ?? rawLines.length}));
  }

  function groupSameAuthorReplies(documents, rootCommentId, enabled, threadMetadata=documents) {
    if(!enabled)return documents.map(doc=>({...doc,timelineGroupId:null}));
    const byId=new Map(threadMetadata.filter(doc=>doc.commentId).map(doc=>[String(doc.commentId),doc]));
    const root=byId.get(String(rootCommentId));
    if(!root?.authorId)return documents.map(doc=>({...doc,timelineGroupId:null}));
    const related=doc=>{const seen=new Set();let current=doc;while(current?.parentCommentId){const id=String(current.parentCommentId);if(seen.has(id))return false;if(id===String(root.commentId))return true;seen.add(id);current=byId.get(id);}return false;};
    return documents.map(doc=>({...doc,timelineGroupId:String(doc.commentId)===String(root.commentId)||(doc.authorId===root.authorId&&related(doc))?String(root.commentId):null}));
  }

  function parseCompatibleComments(documents, options = {}) {
    if (!Array.isArray(documents) || !documents.length || documents.length > 100 ||
        documents.some((doc) => typeof doc.raw !== 'string') ||
        documents.reduce((sum, doc) => sum + doc.raw.length, 0) > MAX_CHARS) {
      throw new RangeError('invalid comment collection');
    }
    const authorByCommentId = new Map(documents.filter((source) => source.commentId && source.author)
      .map((source) => [String(source.commentId), String(source.author)]));
    const units = [], groups = new Map();
    documents.forEach((source, index) => {
      const member = {source, name: String(source.name || `댓글 ${index + 1}`).slice(0, 120), documentId: `comment-${index + 1}`};
      if (!source.timelineGroupId) { units.push([member]); return; }
      let unit = groups.get(source.timelineGroupId);
      if (!unit) { unit = []; groups.set(source.timelineGroupId, unit); units.push(unit); }
      unit.push(member);
    });
    const parsed = units.map(members => {
      const {source, name, documentId} = members[0];
      const sourceRows = [], originalLines = [];
      const documentRosters = members.flatMap(member => {
        const roster = documentRoster(member.source.raw);
        return roster ? [{...resolveParticipantNames(roster, member.source), documentId:member.documentId, documentName:member.name, sourceCommentId:member.source.commentId || null}] : [];
      });
      for (const member of members) {
        const lines = member.source.raw.replace(/\r\n?/g, '\n').split('\n');
        const parentAuthor = authorByCommentId.get(String(member.source.parentCommentId || '')) || '';
        const sections = commentSections(lines, parentAuthor), outline = new Map(), start = originalLines.length;
        const sectionAt = [];
        sections.forEach((section, sectionIndex) => {
          // Verified reply continuation belongs to the terminal outline. Earlier
          // independent sections remain isolated, even inside a logical thread.
          const scope = sectionIndex === sections.length - 1 ? 'outline' : `${member.documentId}:section-${sectionIndex + 1}`;
          for(let at = section.start; at < section.end; at++) sectionAt[at] = scope;
          const plan = naturalOutlinePlan(lines.slice(section.start, section.end), parentAuthor);
          for(const [key, value] of plan) {
            const index = typeof key === 'number' ? key : Number(key.slice(8));
            outline.set(typeof key === 'number' ? index + section.start : `heading:${index + section.start}`, {
              ...value, ...(value.line ? {line:value.line + section.start} : {}),
              inference:{...value.inference, sourceLine:value.inference.sourceLine + section.start,
                anchorLine:value.inference.anchorLine + section.start}
            });
          }
        });
        lines.forEach((raw, index) => {
          originalLines.push(raw);
          sourceRows.push({...member, originalLine: index + 1, originalLines: lines, parentAuthor, outline, start, scope:sectionAt[index]});
        });
      }
      const normalized = [], sourceAt = [], untimedBlocks = [], compatibilityDiagnostics = [];
      for (const [lineIndex, raw] of originalLines.entries()) {
        const row = sourceRows[lineIndex];
        const outlineBarrier=Boolean(row.outline.get(row.originalLine-1)?.barrier);
        const header = documentRosters.find(roster => roster.documentId === row.documentId);
        if (header && row.originalLine >= header.sourceLine && row.originalLine <= header.lastSourceLine) {
          normalized.push('원문 행');
          sourceAt.push({line:lineIndex+1, raw, pattern:'document_roster'});
          if (row.originalLine === header.sourceLine && header.mode === 'invalid') compatibilityDiagnostics.push({line:lineIndex+1, code:header.conflict ? 'participants_conflict' : 'participants_invalid', recoverable:true});
          continue;
        }
        const candidate = compatibleTimeLine(raw, row.parentAuthor);
        const anchored=row.outline.get(row.originalLine-1);
        if(anchored?.anchorSec!==undefined) {
          normalized.push(`${anchored.prefix} ${formatTime(anchored.anchorSec)} ${anchored.title}`);
          sourceAt.push({line:lineIndex+1,raw,pattern:'natural_heading_anchor',inference:anchored.inference,
            ...(anchored.ambiguous?{hierarchyBarrier:true,ambiguousRoster:true}:{})});
          if(anchored.ambiguous)compatibilityDiagnostics.push({line:lineIndex+1,code:'participant_context_ambiguous',recoverable:true});
          continue;
        }
        if (candidate?.error || candidate?.warning) compatibilityDiagnostics.push({ line: lineIndex + 1,
          code: candidate.error || candidate.warning, recoverable: true,
          ...(candidate.referenceTimeSec !== null && candidate.referenceTimeSec !== undefined ? {referenceTimeSec:candidate.referenceTimeSec} : {}) });
        if (candidate && !candidate.error) {
          const heading=row.outline.get(`heading:${row.originalLine - 1}`);
          if(heading) {
            normalized.push(`${heading.prefix} ${formatTime(candidate.times[0])} ${heading.title}`);
            sourceAt.push({line:row.start + heading.line,raw:heading.raw,pattern:'natural_heading_anchor',inference:heading.inference,
              ...(heading.ambiguous ? {hierarchyBarrier:true,ambiguousRoster:true} : {})});
            if(heading.ambiguous)compatibilityDiagnostics.push({line:row.start+heading.line,code:'participant_context_ambiguous',recoverable:true});
          }
          for (const [occurrence, seconds] of candidate.times.entries()) {
            const title = candidate.titles ? candidate.titles[occurrence] || '이 시각' : candidate.title || '이 시각';
            const inferred=row.outline.get(row.originalLine - 1);
            const explicitRole=tags.has(title.match(/^\[([^\]]+)\]/)?.[1].toUpperCase());
            normalized.push(`${inferred?.prefix || '-'} ${formatTime(seconds)}${candidate.end !== null ? ' ~ ' + formatTime(candidate.end) : ''} ${candidate.end !== null && !explicitRole ? '[M] ' : ''}${title}`);
            sourceAt.push({ line: lineIndex + 1, raw,
              ...(inferred ? {inference:inferred.inference} : {}),
              ...(inferred?.detach ? {hierarchyBarrier:true,safeBoundary:true} : {}),
              occurrence: candidate.occurrences?.[occurrence] ?? occurrence, pattern: candidate.pattern,
              untitled: candidate.titles ? !candidate.titles[occurrence] : !candidate.title,
              documentMarker: raw.trim().match(/^(?:ㄴ|└)(?=\s|\d)/)?.[0] || null });
          }
        } else {
          if (isSectionBreak(raw)) {
            normalized.push('원문 행');
            sourceAt.push({line:lineIndex + 1, raw, pattern:'section_break'});
            untimedBlocks.push({line:lineIndex + 1, raw});
            continue;
          }
          const lexical = compatibleLexLine(raw).trim();
          const structure = lexical.match(/^(?:#{1,6}\s+|(?:-|▶)\s+)(.*)$/);
          if (!candidate && structure && !timedLine.test(structure[1])) {
            // Existing comments may use untimed headings or bullets. Keep the
            // original line without making it a timed entry or blocking its neighbors.
            const damagedHeading = /^#{1,6}\s+/.test(lexical) && /^(?:\*{1,2}|_{1,2})?\d+:/.test(structure[1]);
            const startClock = damagedHeading && structure[1].match(new RegExp(`^${TIME}`));
            const reason = damagedHeading ? (startClock && invalidRangeEnd(structure[1].slice(startClock[1].length)) ? 'invalid_end' :
              parseTime(structure[1].split(/\s/)[0]) === null ? 'invalid_time' : 'invalid_timed_entry') : null;
            if (reason) compatibilityDiagnostics.push({line:lineIndex + 1, code:reason, recoverable:true});
            normalized.push('원문 행'); // Keep the detail scope from crossing an untimed heading.
            sourceAt.push({ line: lineIndex + 1, raw, pattern: 'original', ...(damagedHeading || outlineBarrier ? {hierarchyBarrier:true} : {}) });
            untimedBlocks.push({ line: lineIndex + 1, raw, ...(reason ? {reason} : {}) });
            continue;
          }
          normalized.push(candidate?.error ? '원문 행' : structure ? lexical : raw);
          sourceAt.push({ line: lineIndex + 1, raw, pattern: 'original', ...(outlineBarrier ? {hierarchyBarrier:true} : {}) });
          if ((candidate?.error || raw.trim()) &&
              (candidate?.error || !/^(?:#{1,6}\s+|(?:-|▶)\s+\d+:\d{2}|설명:|맥락:|검토 상태:|원본 구간:|(?:합방|함께|참여)\s*:)/.test(lexical))) {
            untimedBlocks.push({ line: lineIndex + 1, raw,
              ...(candidate?.error ? { reason: candidate.error } : {}) });
          }
        }
      }
      const parseOptions = { ...options, compatibleInput: true,
        ...(members.some(member => member.source.completeness === 'partial') ? {completeness:'partial',
          continuousLines: members.every(member => member.source.completeness !== 'partial' || member.source.continuousLines === true)} : {}),
        sourceRevision: `${options.sourceRevision || 'unversioned'}:${documentId}` };
      parseOptions.logicalScopeLines = sourceAt.map(line => sourceRows[line.line - 1].scope);
      parseOptions.mediaEndScopes = ['outline'];
      if (members.length > 1) parseOptions.logicalThreadSourceLines = sourceAt.map(line => sourceRows[line.line - 1].documentId);
      let document;
      for (let pass = 0; pass <= originalLines.length; pass++) {
        parseOptions.logicalHierarchyBarriers = sourceAt.map(line => Boolean(line.hierarchyBarrier));
        document = parseComment(normalized.join('\n'), parseOptions);
        if (!document.diagnostics.length) break;
        const affected = new Set(document.diagnostics.map((issue) => sourceAt[issue.line - 1]?.line)
          .filter((line) => line && normalized.some((value, index) => sourceAt[index].line === line && value.trim())));
        if (!affected.size) break;
        for (const issue of document.diagnostics) {
          const line = sourceAt[issue.line - 1]?.line;
          if (affected.has(line)) compatibilityDiagnostics.push({ line, code: issue.code, recoverable: true });
        }
        for (const line of affected) {
          let retainedTime = false;
          normalized.forEach((value, index) => {
            if (sourceAt[index].line !== line) return;
            if (/^#{1,6}\s+/.test(value) && document.diagnostics.some(issue => issue.line === index + 1 &&
                /^(?:invalid_time|invalid_timed_entry|invalid_end|outside_media|outside_parent|missing_parent|chapter_order_conflict|empty_or_reversed_chapter|explicit_overlap)$/.test(issue.code))) {
              sourceAt[index].hierarchyBarrier = true;
            }
            const matched = value.match(/^(?:#{1,6}\s+|(?:-|▶)\s+)(\d+:\d{2}(?::\d{2})?)(?:\s*(?:~|–|—|-)\s*\d+:\d{2}(?::\d{2})?)?\s*(.*)$/);
            const start = matched && parseTime(matched[1]);
            if (Number.isInteger(start) &&
                !document.diagnostics.some((issue) => issue.line === index + 1 && issue.code === 'outside_media') &&
                !sourceAt[index].recoveredPoint) {
              normalized[index] = `- ${formatTime(start)} ${matched[2].trim() || '이 시각'}`;
              sourceAt[index].recoveredPoint = true;
              sourceAt[index].pattern = 'recovered_point';
              retainedTime = true;
            } else normalized[index] = '원문 행';
          });
          if (!retainedTime && !untimedBlocks.some((block) => block.line === line)) {
            untimedBlocks.push({ line, raw: originalLines[line - 1], reason: 'not_imported' });
          }
        }
      }
      const byId = new Map(document.entries.map(entry => [entry.entryId, entry]));
      const hazards = compatibilityDiagnostics.filter(issue => /^(?:invalid_time|invalid_timed_entry|invalid_end|outside_media|outside_parent|missing_parent|chapter_order_conflict|empty_or_reversed_chapter|explicit_overlap)$/.test(issue.code));
      function boundaryLine(entry, seen = new Set()) {
        if (!entry || seen.has(entry.entryId)) return null;
        seen.add(entry.entryId);
        if (entry.endBasis === 'parent_end') return boundaryLine(byId.get(entry.parentId), seen);
        if (entry.boundarySourceEntryId) return sourceAt[byId.get(entry.boundarySourceEntryId)?.sourceLine - 1]?.line;
        if (entry.endBasis === 'media_end') {
          const scope = parseOptions.logicalScopeLines[entry.sourceLine - 1];
          return sourceRows.findLastIndex(row => row.scope === scope) + 1;
        }
        // An explicit parent is a stronger boundary than intervening malformed text.
        return null;
      }
      const unsafeIds = new Set(document.entries.filter(entry => {
        const sourceLine = sourceAt[entry.sourceLine - 1], row = sourceRows[sourceLine.line - 1];
        const endLine = boundaryLine(entry);
        return entry.endMode === 'auto' && endLine && (hazards.some(issue =>
          sourceRows[issue.line - 1]?.scope === row.scope && issue.line > sourceLine.line && issue.line <= endLine) ||
          sourceAt.some(line=>line.hierarchyBarrier && !line.safeBoundary && sourceRows[line.line-1]?.scope===row.scope &&
            line.line>sourceLine.line && line.line<=endLine));
      }).map(entry => entry.entryId));
      const invalidCandidateLines = new Set();
      const entries = document.entries.map((entry) => {
        const sourceLine = sourceAt[entry.sourceLine - 1], row = sourceRows[sourceLine.line - 1];
        let safeEntry = unsafeIds.has(entry.entryId) ?
          { ...entry, resolvedEndSec: null, endBasis: 'unknown', boundarySourceEntryId: null } : entry;
        if (!unsafeIds.has(entry.entryId) && entry.endBasis === 'parent_end' && unsafeIds.has(entry.parentId)) {
          // The child can still reach a reliable boundary after the damaged row.
          // Cite that boundary directly instead of the now-unknown parent's end.
          let anchor = byId.get(entry.parentId);
          while (anchor?.endBasis === 'parent_end') anchor = byId.get(anchor.parentId);
          if (anchor) safeEntry = {...entry, endBasis:anchor.endBasis, boundarySourceEntryId:anchor.boundarySourceEntryId || null};
        }
        return { ...safeEntry, sourceLine: row.originalLine, raw: sourceLine.raw,
          sourceSectionId:`${documentId}:${row.scope}`,
          ...(sourceLine.inference ? {inference:{...sourceLine.inference,
            anchorRaw:row.originalLines[sourceLine.inference.anchorLine-1],
            ...(sourceLine.recoveredPoint?{basis:'recovered_point_role_confirmation'}:{})}} : {}),
          participantCandidates: resolveParticipantNames(participantCandidates(sourceLine.raw, row.originalLine,
            Boolean(sourceLine.ambiguousRoster) || /^semantic_(?:heading_and_rounds|roster_and_rounds)$/.test(sourceLine.inference?.basis || ''),
            ()=>invalidCandidateLines.add(sourceLine.line)), row.source),
          effectiveParticipants: resolveParticipantNames(entry.effectiveParticipants,
            entry.inheritedFromEntryId ? sourceRows[sourceAt[byId.get(entry.inheritedFromEntryId)?.sourceLine-1]?.line-1]?.source || row.source : row.source),
          detailLines: entry.detailLines.map((detail) => {
            const detailRow = sourceRows[sourceAt[detail.sourceLine - 1]?.line - 1];
            return {...detail, sourceLine: detailRow?.originalLine || detail.sourceLine,
              ...(members.length > 1 ? {sourceCommentId: detailRow?.source.commentId || null, sourceDocumentId: detailRow?.documentId || null} : {})};
          }),
          sourceOccurrence: sourceLine.occurrence || 0, sourcePattern: sourceLine.pattern,
          documentMarker: sourceLine.documentMarker || null,
          title: sourceLine.untitled ? '' : entry.title, untitled: Boolean(sourceLine.untitled),
          documentId, documentName: row.name, sourceDocumentId:row.documentId, sourceCommentId: row.source.commentId || null, sourceAuthorId:row.source.authorId||null,
          sourceAuthor: row.source.author || null, sourceParentCommentId: row.source.parentCommentId || null };
      });
      // Chronological adjacency provides lookup context, never an attendance declaration.
      // Use original document boundaries after damaged clocks have been recovered.
      const finalById = new Map(entries.map(entry => [entry.entryId, entry]));
      const sourceLineById = new Map(document.entries.map(entry => [entry.entryId, sourceAt[entry.sourceLine-1].line]));
      const entryAtLine = new Map(entries.map(entry => [sourceLineById.get(entry.entryId), entry]));
      const attachedRosters = new Map(), ambiguousRosters = new Map(), localContexts = new Set();
      const precedingById=new Map(), latestByScope=new Map();
      for(const entry of entries) {
        const scope=entry.sourceDocumentId+'|'+entry.sourceSectionId;
        precedingById.set(entry.entryId,latestByScope.get(scope));latestByScope.set(scope,entry);
      }
      function collabContext(entry) {
        let current=entry, found=false;
        while(current) {
          if(current.sourceDocumentId!==entry.sourceDocumentId || current.sourceSectionId!==entry.sourceSectionId)break;
          if(comparisonTitle(current.title) || inactiveCollaborationTitle(current.title))return false;
          found ||= collaborationAnchorTitle(current.title) ||
            /^semantic_(?:heading_and_rounds|roster_and_rounds)$/.test(current.inference?.basis || '') &&
              Boolean(current.participantCandidates);
          current=finalById.get(current.parentId);
        }
        return found;
      }
      function comparisonContext(entry) {
        let current=entry;
        while(current && current.sourceDocumentId===entry.sourceDocumentId && current.sourceSectionId===entry.sourceSectionId) {
          if(comparisonTitle(current.title))return true;
          current=finalById.get(current.parentId);
        }
        return false;
      }
      for(const entry of entries) {
        const candidate=entry.participantCandidates, preceding=precedingById.get(entry.entryId);
        // Inline guesses were read before hierarchy/chronology was available.
        // Apply the same context boundary as adjacent bare lists; explicit with
        // clauses retain their independent lookup authority.
        const explicit=candidate && /^\(\s*(?:w\.|w\/|with\b)/i.test(compatibleLexLine(candidate.raw).trim());
        const suppressed=!entry.effectiveParticipants && !explicit &&
          (comparisonContext(entry) || inactiveCollaborationTitle(entry.title) ||
            collabContext(entry) && preceding && preceding.startSec>entry.startSec);
        if(suppressed)entry.participantCandidatesSuppressed=true;
        if(candidate && suppressed) {
          entry.participantCandidates=null;
        }
      }
      const explicitRole=entry=>/\[(?:P|H|H\?|M|W|합방)\]/i.test(compatibleLexLine(entry.raw));
      const contextAnchor=entry=>entry.role==='chapter' ? entry.depth<=2 && collabContext(entry) :
        ['point','memo'].includes(entry.role) && !explicitRole(entry) &&
        ['leading','trailing','range'].includes(entry.sourcePattern) && collaborationAnchorTitle(entry.title) && collabContext(entry);
      for(const entry of entries) {
        if(!contextAnchor(entry) || entry.effectiveParticipants ||
          entry.sourcePattern==='recovered_point' || !collabContext(entry))continue;
        const preceding=precedingById.get(entry.entryId);
        if(preceding && preceding.startSec>entry.startSec)continue;
        const row=sourceRows[sourceLineById.get(entry.entryId)-1];
        entry.participantCandidates ||= resolveParticipantNames(participantCandidates(entry.raw,entry.sourceLine,true,
          ()=>invalidCandidateLines.add(sourceLineById.get(entry.entryId))),row.source);
        if(entry.participantCandidates)localContexts.add(entry.entryId);
      }
      for(const block of untimedBlocks) {
        if(block.reason)continue;
        const row=sourceRows[block.line-1], text=compatibleLexLine(block.raw).trim();
        let previous=block.line-2;
        while(previous>=0 && !originalLines[previous].trim())previous--;
        let next=block.line;
        while(next<originalLines.length && !originalLines[next].trim())next++;
        const previousEntry=entryAtLine.get(previous+1);
        const eligible=(index,forward)=> {
          const entry=entryAtLine.get(index+1), anchorRow=sourceRows[index];
          if(!entry || !contextAnchor(entry) || entry.effectiveParticipants ||
            entry.sourcePattern==='recovered_point' || anchorRow.documentId!==row.documentId ||
            anchorRow.scope!==row.scope || !collabContext(entry) ||
            forward && !collaborationAnchorTitle(entry.title) ||
            forward && entry.participantCandidates ||
            /^\(\s*(?:w\.|w\/|with\b)/i.test(entry.participantCandidates?.raw || ''))return null;
          const preceding=precedingById.get(entry.entryId);
          return preceding && preceding.startSec>entry.startSec ? null : entry;
        };
        const backward=eligible(previous,false), forward=eligible(next,true);
        if(!backward && !forward || previousEntry && comparisonContext(previousEntry))continue;
        const candidate=adjacentRoster(text,row.originalLine,()=>invalidCandidateLines.add(block.line));
        if(!candidate)continue;
        const resolved=resolveParticipantNames(candidate,row.source);
        if(backward && forward) {
          ambiguousRosters.set(block.line,resolved);
          compatibilityDiagnostics.push({line:block.line,code:'participant_context_ambiguous',recoverable:true});
          continue;
        }
        const anchor=backward || forward;
        anchor.participantCandidates=resolved;
        localContexts.add(anchor.entryId);
        attachedRosters.set(block.line,{candidate:resolved,anchor});
      }
      for(const entry of entries) {
        const parent=finalById.get(entry.parentId);
        if(!parent || entry.sourceDocumentId!==parent.sourceDocumentId || entry.sourceSectionId!==parent.sourceSectionId)continue;
        if(!entry.effectiveParticipants && !entry.participantCandidates && !entry.participantCandidatesSuppressed && parent.participantCandidates &&
          (localContexts.has(parent.entryId) || parent.inheritedParticipantCandidateFrom) && entry.role==='chapter' &&
          entry.startSec>=parent.startSec && (parent.resolvedEndSec===null || entry.startSec<parent.resolvedEndSec) &&
          !comparisonTitle(entry.title)) {
          entry.participantCandidates=parent.participantCandidates;
          entry.inheritedParticipantCandidateFrom=parent.inheritedParticipantCandidateFrom || parent.entryId;
        }
      }
      for(const child of entries) {
        if((!child.participants && !child.participantCandidatesSuppressed && !localContexts.has(child.entryId)) || child.role!=='chapter' ||
          (child.resolvedEndSec!==null && (!Number.isFinite(child.resolvedEndSec) || child.resolvedEndSec<=child.startSec)))continue;
        let parent=finalById.get(child.parentId);
        while(parent) {
          if(localContexts.has(parent.entryId) && child.sourceDocumentId===parent.sourceDocumentId) {
            parent.participantCandidates.excludedContextRanges ||= [];
            parent.participantCandidates.excludedContextRanges.push({startSec:child.startSec,endSec:child.resolvedEndSec});
          }
          parent=finalById.get(parent.parentId);
        }
      }
      // Flat round lists have no invented parent chapter. Read a round's own
      // roster only while the preceding original timed rows establish a collab.
      let flatContext=null, previousTimedLine=0;
      for(const entry of entries) {
        const line=sourceLineById.get(entry.entryId), row=sourceRows[line-1];
        if(flatContext && (entry.sourceDocumentId!==flatContext.sourceDocumentId ||
            entry.sourceSectionId!==flatContext.sourceSectionId || entry.startSec<=flatContext.startSec ||
            originalLines.slice(previousTimedLine,line-1).some((raw,index)=>raw.trim() &&
              !adjacentRoster(compatibleLexLine(raw),sourceRows[previousTimedLine+index].originalLine))))flatContext=null;
        if(entry.role==='chapter')flatContext=null;
        if(flatContext && !entry.effectiveParticipants && !explicitRole(entry) && collaborationContinuationTitle(entry.title)) {
          entry.participantCandidates=resolveParticipantNames(participantCandidates(entry.raw,entry.sourceLine,true,
            ()=>{invalidCandidateLines.add(line);entry.participantCandidatesSuppressed=true;}),row.source);
          if(entry.participantCandidates) {localContexts.add(entry.entryId);flatContext=entry;}
          else if(entry.participantCandidatesSuppressed)flatContext=null;
        } else if(!explicitRole(entry) && !/^(?:\s*)(?:--|└|ㄴ)/.test(compatibleLexLine(entry.raw)))flatContext=null;
        if(entry.role!=='chapter' && localContexts.has(entry.entryId) && entry.participantCandidates)flatContext=entry;
        previousTimedLine=line;
      }
      // Search context is independent of the scene's Point/D1/D2 geometry.
      // Bound flat starts by original chronology; a round is a continuation,
      // while another topic/end is a boundary. Uncertain ends remain moments.
      const complete=options.completeness!=='partial';
      const duration=Number.isFinite(options.durationSec)?options.durationSec:null;
      const lookupBarrierLines=new Set([...hazards.map(issue=>issue.line),
        ...sourceAt.filter(line=>line.hierarchyBarrier && !line.safeBoundary).map(line=>line.line)]);
      for(const entry of entries) {
        const inherited=Boolean(entry.inheritedParticipantCandidateFrom);
        if((!localContexts.has(entry.entryId) && !inherited) || !entry.participantCandidates)continue;
        const flat=entry.role!=='chapter', startLine=sourceLineById.get(entry.entryId), row=sourceRows[startLine-1];
        let end=entry.geometry==='interval'?entry.resolvedEndSec:null, basis=entry.endBasis, bounded=false;
        const parent=finalById.get(entry.parentId);
        if(inherited && Number.isFinite(parent?.participantCandidates?.contextEndSec)) {
          const limit=parent.participantCandidates.contextEndSec;
          end=end===null?limit:Math.min(end,limit);
          basis=parent.participantCandidates.contextEndBasis==='media_end'?'media_end':'parent_context';bounded=true;
        }
        if(flat && Number.isFinite(parent?.resolvedEndSec) && parent.resolvedEndSec>entry.startSec) {
          end=end===null?parent.resolvedEndSec:Math.min(end,parent.resolvedEndSec);basis='parent';
        }
        let lastScope=true, uncertain=false;
        for(let i=startLine;i<sourceRows.length;i++) {
          const nextRow=sourceRows[i];
          if(nextRow.documentId!==row.documentId || nextRow.scope!==row.scope) {lastScope=false;break;}
          if(lookupBarrierLines.has(i+1)) {uncertain=true;break;}
          if(!originalLines[i].trim())continue;
          const next=entryAtLine.get(i+1);
          if(!next) {
            if(flat && !attachedRosters.has(i+1) && !adjacentRoster(compatibleLexLine(originalLines[i]),nextRow.originalLine)) {uncertain=true;break;}
            continue;
          }
          if(Number.isFinite(end) && next.startSec>=end)break;
          let ancestor=finalById.get(next.parentId);
          while(ancestor && ancestor.entryId!==entry.entryId && ancestor.sourceDocumentId===entry.sourceDocumentId &&
              ancestor.sourceSectionId===entry.sourceSectionId)ancestor=finalById.get(ancestor.parentId);
          const descendant=ancestor?.entryId===entry.entryId;
          if(next.startSec<entry.startSec || next.startSec===entry.startSec && !descendant ||
              next.sourcePattern==='recovered_point') {uncertain=true;break;}
          if(collaborationEndTitle(next.title) || next.role!=='chapter' && inactiveCollaborationTitle(next.title) || flat &&
              (localContexts.has(next.entryId) && next.participantCandidates!==entry.participantCandidates ||
                collaborationAnchorTitle(next.title) && !explicitRole(next) ||
                !collaborationContinuationTitle(next.title) && !explicitRole(next) && !/^(?:\s*)(?:--|└|ㄴ)/.test(compatibleLexLine(next.raw)))) {
            end=next.startSec;basis='next_scene';bounded=true;break;
          }
        }
        if(uncertain && !Number.isFinite(entry.explicitEndSec)) {end=null;basis='unknown';bounded=true;}
        else if(flat && end===null && complete && lastScope && duration>entry.startSec) {end=duration;basis='media_end';}
        if(flat || bounded || inherited) {
          // Inherited candidates share names and exclusions, but each source
          // row owns its lookup bounds. An uncertain parent cannot erase the
          // safe interval of a later child through a shared-object mutation.
          let contextBasis=basis || 'unknown';
          if(contextBasis==='parent_end') {
            let anchor=parent;
            while(anchor?.endBasis==='parent_end')anchor=finalById.get(anchor.parentId);
            if(anchor?.endBasis==='media_end')contextBasis='media_end';
          }
          entry.participantCandidates={...entry.participantCandidates,
            contextEndSec:Number.isFinite(end)?end:null,
            contextEndBasis:/^next_(?:heading|thread_heading|thread_line)$/.test(contextBasis)?'next_scene':
              ['parent_context','parent_end'].includes(contextBasis)?'parent':contextBasis};
        }
      }
      // Preserve exact source-line failures so paired consumers cannot revive a
      // rejected team list through their legacy name-only fallback.
      for(const block of untimedBlocks) {
        if(!block.reason)participantCandidates(block.raw,sourceRows[block.line-1].originalLine,false,
          ()=>invalidCandidateLines.add(block.line));
      }
      for(const line of invalidCandidateLines) {
        if(!compatibilityDiagnostics.some(issue=>issue.line===line && issue.code==='participants_invalid'))
          compatibilityDiagnostics.push({line,code:'participants_invalid',recoverable:true});
      }
      const diagnostics = [...document.diagnostics.map((issue) => ({ ...issue, line: sourceAt[issue.line - 1]?.line || issue.line })),
        ...compatibilityDiagnostics].map((issue) => {
          const row = sourceRows[issue.line - 1];
          return {...issue, line: row?.originalLine || issue.line, documentName: row?.name || name,
            ...(members.length > 1 ? {sourceCommentId: row?.source.commentId || null} : {})};
        });
      const result = { ...document, name, documentId, raw: source.raw, author: source.author || null,
        commentId: source.commentId || null, parentCommentId: source.parentCommentId || null, authorId:source.authorId||null,timelineGroupId:source.timelineGroupId||null,
        entries, diagnostics, documentRosters, untimedBlocks: untimedBlocks.map((block) => {
          const row = sourceRows[block.line - 1];
          const attached=attachedRosters.get(block.line);
          let context=entries.findLast(entry=>entry.sourceDocumentId===row.documentId && entry.sourceSectionId===`${documentId}:${row.scope}` && sourceLineById.get(entry.entryId)<block.line);
          let comparison=false;
          while(context && context.sourceDocumentId===row.documentId){if(comparisonTitle(context.title)){comparison=true;break;}context=finalById.get(context.parentId);}
          const ambiguous=ambiguousRosters.get(block.line);
          const candidates = attached?.candidate || ambiguous || (!comparison ? resolveParticipantNames(participantCandidates(block.raw, row.originalLine), row.source) : null);
          return {...block, line: row.originalLine, ...(candidates ? {participantCandidates: candidates} : {}),
            ...(comparison && !attached && !ambiguous ? {participantCandidatesSuppressed:true} : {}),
            ...(ambiguous ? {participantContext:'ambiguous'} : {}),
            ...(attached ? {participantAnchor:{entryId:attached.anchor.entryId,sourceLine:attached.anchor.sourceLine,
              startSec:attached.anchor.startSec,endSec:attached.anchor.participantCandidates.contextEndSec ?? attached.anchor.resolvedEndSec}} : {}),
            ...(source.timelineGroupId ? {sourceCommentId: row.source.commentId || null, documentName: row.name} : {})};
        }) };
      if (source.timelineGroupId) result.sourceDocuments = members.map(member => ({
        ...result, sourceDocuments: undefined, name: member.name, documentId: member.documentId, raw: member.source.raw,
        author: member.source.author || null, commentId: member.source.commentId || null,
        authorId: member.source.authorId || null, parentCommentId: member.source.parentCommentId || null,
        documentRosters:documentRosters.filter(roster => roster.documentId === member.documentId),
        entries: entries.filter(entry => entry.sourceDocumentId === member.documentId).map(entry => ({...entry, documentId: member.documentId})),
        diagnostics: diagnostics.filter(issue => issue.documentName === member.name &&
          (!issue.sourceCommentId || String(issue.sourceCommentId) === String(member.source.commentId))),
        untimedBlocks: result.untimedBlocks.filter(block => String(block.sourceCommentId) === String(member.source.commentId))
      }));
      return result;
    });
    return { documents: parsed, entries: parsed.flatMap((doc) => doc.entries),diagnostics: parsed.flatMap((doc) => doc.diagnostics) };
  }

  function contextNotices(documents, { documentId, parentCommentId } = {}) {
    if (!Array.isArray(documents) || !documentId) return [];
    // A caution without a time belongs to its source document. A selected reply
    // also keeps its selected parent comment's cautions in the reading context.
    const caution = /(?:오차|주의|음질|싱크|지연|±|정확하지|차이날|차이가|틀릴\s*수)/i;
    return documents.filter((doc) => doc.documentId === documentId ||
      (parentCommentId && String(doc.commentId || '') === String(parentCommentId)))
      .flatMap((doc) => (doc.untimedBlocks || []).filter((block) => caution.test(block.raw || ''))
        .map((block) => ({ documentName: block.documentName||doc.name, sourceCommentId:block.sourceCommentId||doc.commentId||null,sourceLine: block.line, raw: block.raw,
          source: doc.documentId === documentId ? 'selected' : 'parent' })));
  }

  function readTimelineFile(text, name='구조 데이터', videoNo=null) {
    if(typeof text!=='string' || text.length>MAX_CHARS) throw new RangeError('구조 데이터가 너무 큽니다.');
    if(!/\.json$/i.test(name) && !/^\s*\{/.test(text)) return [{raw:text,name}];
    const data=JSON.parse(text);
    if(data?.format!=='chzzk.comment-timeline' || data.version!==1 || !Array.isArray(data.documents) || !data.documents.length || data.documents.length>100)
      throw Error('지원 형식은 chzzk.comment-timeline 버전 1입니다.');
    if(data.videoNo!==undefined && (!/^[1-9][0-9]{0,19}$/.test(String(data.videoNo)) || (videoNo && String(data.videoNo)!==String(videoNo))))
      throw Error('현재 VOD의 구조 데이터가 아닙니다.');
    const documents=data.documents.map((doc,index)=>{
      if(!doc || (doc.raw!==undefined && (typeof doc.raw!=='string'||doc.entries!==undefined)) || (doc.aliases!==undefined && (!Array.isArray(doc.aliases) || doc.aliases.length>100 || doc.aliases.some(x=>!x || [x.alias,x.streamer].some(name=>typeof name!=='string'||!name.trim()||name.length>80||/[\[\]<>\/\\\r\n]/.test(name))))))throw Error('원문 또는 별칭 형식을 확인해 주세요.');
      let raw=doc.raw;
      if(raw===undefined) {
        if(!Array.isArray(doc.entries) || doc.entries.length>5000)throw Error('원문이나 항목 목록이 필요합니다.');
        raw=doc.entries.map(entry=>{
          if(!entry || !Number.isSafeInteger(entry.startSec) || entry.startSec<0 || typeof entry.title!=='string' || /[\r\n]/.test(entry.title) || entry.title.length>1000)throw Error('항목의 시각과 제목을 확인해 주세요.');
          const end=entry.endSec;
          if(end!==undefined && (!Number.isSafeInteger(end) || end<=entry.startSec))throw Error('항목의 끝 시각을 확인해 주세요.');
          const role=entry.role||'point', tags={point:'[P]',memo:'[M]',watch:'[W]',highlight:'[H]',expected_highlight:'[H?]',collab_context:'[합방]'};
          if(role!=='chapter' && !Object.hasOwn(tags,role))throw Error('지원하지 않는 항목 종류입니다.');
          if(role==='point' && end!==undefined)throw Error('Point는 한 시각입니다. 구간은 memo 또는 highlight 종류로 지정해 주세요.');
          if(role==='chapter' && (!Number.isInteger(entry.depth) || entry.depth<1 || entry.depth>6))throw Error('제목 깊이는 1–6입니다.');
          return `${role==='chapter'?'#'.repeat(entry.depth):'-'} ${formatTime(entry.startSec)}${end===undefined?'':` ~ ${formatTime(end)}`} ${role==='chapter'?'':tags[role]} ${entry.title}`;
        }).join('\n');
        const projected=parseCompatibleComments([{raw}]).entries;
        if(projected.length!==doc.entries.length || projected.some((entry,i)=>entry.startSec!==doc.entries[i].startSec || entry.role!==(doc.entries[i].role||'point') ||
          (entry.role==='chapter'&&entry.depth!==doc.entries[i].depth) || (doc.entries[i].endSec!==undefined&&entry.explicitEndSec!==doc.entries[i].endSec)))
          throw Error('항목 제목의 시간·문법 표기로 해석이 달라집니다. 제목을 정리하거나 raw 원문으로 가져와 주세요.');
      }
      return {raw,name:String(doc.name||`${name} ${index+1}`).slice(0,120),aliases:doc.aliases||[]};
    });
    parseCompatibleComments(documents);
    return documents;
  }
  const api = { parseComment, parseComments, parseCompatibleComments, groupSameAuthorReplies, contextNotices, readTimelineFile,
    splitComment, documentRoster, parseTime, formatTime, version: VERSION };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else globalThis.ChzzkCommentFormat = api;
})();
