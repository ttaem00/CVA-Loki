    function actionLabel(action) {
      const labels = signalTimelineState && signalTimelineState.labels && signalTimelineState.labels.actions || {};
      return labels[action] || ({
        pin: '중요 장면으로 표시',
        ignore: '이번 편집에서 제외',
        add: '요약에 추가 검토',
        retime: '시각 조정 필요',
        note: '메모만 남김',
      }[action] || action || '메모만 남김');
    }

    function roleLabel(role) {
      const labels = signalTimelineState && signalTimelineState.labels && signalTimelineState.labels.roles || {};
      return labels[role] || ({
        candidate: '검토 범위',
        supporting: '보조 신호',
        confirmed: '요약에 반영됨',
      }[role] || role || '검토 범위');
    }

    function laneOptionLabel(kind) {
      const labels = signalTimelineState && signalTimelineState.labels && signalTimelineState.labels.lanes || {};
      if (kind === 'chat') return '전체 채팅량';
      return labels[kind] || ({
        timeline: '기존 요약',
        existing_segments: '요약 본문 장면',
        highlight: '하이라이트',
        comment_replay: '댓글 시간표 후보',
        chat_surprise: '채팅 급증 구간',
        chat_laughter: '웃음/놀람 채팅',
        chat: '전체 채팅량',
        subtitle_density: '자막 긴 구간',
        audio_peak: '오디오 피크',
        viewer_clip: '시청자 클립',
        comment_context_range: '댓글 맥락 범위 후보',
        comment_moment_seed: '댓글 시각 씨앗',
        comment_story_window: '댓글 Story Window 후보',
        context_activity_shadow: '자동 활동 맥락 후보',
        context_topic_shadow: '자동 주제 맥락 후보',
        context_unknown_gap_shadow: '모르는 맥락 범위',
      }[kind] || kind || '근거');
    }

    const CONTEXT_RANGE_LANES = new Set([
      'comment_context_range',
      'context_activity_shadow',
      'context_topic_shadow',
      'context_unknown_gap_shadow',
    ]);
    const STORY_WINDOW_LANES = new Set(['comment_story_window']);
    const MOMENT_SEED_LANES = new Set(['comment_moment_seed']);

    function timelineSelectionKind(event) {
      const lane = String(event && event.lane || '');
      if (STORY_WINDOW_LANES.has(lane)) return 'story_window';
      if (CONTEXT_RANGE_LANES.has(lane)) return 'context_range';
      if (MOMENT_SEED_LANES.has(lane)) return 'moment_seed';
      return 'exact_time';
    }

    function selectedEvidenceKicker(event) {
      const lane = String(event && event.lane || '');
      if (lane === 'context_unknown_gap_shadow') return '모르는 범위';
      const kind = timelineSelectionKind(event);
      if (kind === 'story_window') return '선택한 Story Window 후보';
      if (kind === 'context_range') {
        return lane.startsWith('context_') ? '자동 맥락 범위 후보' : '선택한 맥락 범위 후보';
      }
      if (kind === 'moment_seed') return '선택한 시각 씨앗';
      return '장면 찾기에서 선택한 시각';
    }

    function adaptivePresentationSummary(workbench) {
      const summary = workbench && workbench.summary || {};
      const managerHints = summary.manager_comment_hints || {};
      const adaptive = managerHints.adaptive_presentation || summary.adaptive_presentation || null;
      if (!adaptive || typeof adaptive !== 'object') return null;
      const profile = adaptive.presentation_profile && typeof adaptive.presentation_profile === 'object'
        ? adaptive.presentation_profile
        : adaptive;
      const broadcastProfile = String(adaptive.broadcast_profile || profile.broadcast_profile || 'mixed_unknown');
      const confidenceBand = String(adaptive.confidence_band || profile.confidence_band || 'unknown');
      const order = profile.hints && Array.isArray(profile.hints.order) ? profile.hints.order : [];
      return {
        broadcastProfile,
        confidenceBand,
        fallbackReason: String(profile.fallback_reason || ''),
        order,
        contextRangeTotal: Number(
          (managerHints.source_lane_event_counts || {}).comment_context_range || 0,
        ),
        contextRangeShown: Number(
          (managerHints.lane_event_counts || {}).comment_context_range || 0,
        ),
        contextRangeHidden: Number(
          (managerHints.hidden_lane_event_counts || {}).comment_context_range || 0,
        ),
      };
    }

    function broadcastProfileLabel(profile) {
      return ({
        game_match: '게임·경기',
        talk_story: '토크·이야기',
        collab_multi_pov: '합방·여러 사람',
        watch_event: '같이보기·월드컵/선택형',
        creative_work: '작업·창작',
        mixed_unknown: '공통 화면',
      }[profile] || '공통 화면');
    }

    function activityHintLabel(activity) {
      return ({
        game_play: '게임 진행',
        match_watching: '경기 시청',
        watch_along: '같이보기',
        bracket_choice: '월드컵·VS 선택',
        collaboration: '합방',
        talk: '토크',
        creative_work: '작업·창작',
        unknown: '활동 종류 확인 필요',
      }[String(activity || '')] || '');
    }

    function contentFormHintLabel(contentForm) {
      return ({
        game_session: '게임 세션',
        within_game_phase: '게임 안 단계',
        match_watch: '경기 시청 구간',
        watch_along: '같이보기 구간',
        versus_bracket: 'VS·월드컵 선택 구간',
        collab: '합방 구간',
        topic_discussion: '주제 토크',
        creative_step: '작업 단계',
        unknown: '구간 종류 확인 필요',
      }[String(contentForm || '')] || '');
    }

    function adaptiveRangeHintChips(event) {
      const labels = [
        activityHintLabel(event && event.activity_hint),
        contentFormHintLabel(event && event.content_form_hint),
      ].filter(Boolean);
      return [...new Set(labels)].map(
        (label) => `<span class="viewer-editor-chip" data-adaptive-range-hint="${escapeHtml(label)}">${escapeHtml(label)}</span>`,
      );
    }

    function adaptiveRangeGuidance(event) {
      return ({
        game_session: '게임 전체의 시작·준비·종료 경계를 먼저 확인하세요.',
        within_game_phase: '라운드·보스전·퀘스트처럼 게임 안 단계의 시작과 끝을 확인하세요.',
        match_watch: '경기 흐름과 시청 반응이 바뀌는 앞뒤를 함께 확인하세요.',
        watch_along: '같이 본 원본 장면과 이어지는 반응을 함께 확인하세요.',
        versus_bracket: '선택지 제시 → 비교 → 결정 → 반응이 이어지는지 확인하세요.',
        collab: '합류·화자·관점 전환과 함께 반응하는 부분을 확인하세요.',
        topic_discussion: '주제 시작 → 전환 → 결론이 끊기지 않는지 확인하세요.',
        creative_step: '작업 시작 → 핵심 변화 → 완료 단계를 확인하세요.',
        unknown: '구간 종류를 단정하지 말고 자막·소리·화면 근거를 함께 확인하세요.',
      }[String(event && event.content_form_hint || '')] || '');
    }

    function adaptiveRangeActionLabel(event, selectionKind) {
      if (selectionKind === 'story_window') return 'Story Window 시작으로 이동';
      if (selectionKind === 'moment_seed') return '사건 시점부터 보기';
      return ({
        game_session: '게임 구간 시작으로 이동',
        within_game_phase: '게임 단계 시작으로 이동',
        match_watch: '경기 시청 구간 시작으로 이동',
        watch_along: '같이보기 구간 시작으로 이동',
        versus_bracket: 'VS 구간 시작으로 이동',
        collab: '합방 구간 시작으로 이동',
        topic_discussion: '토크 구간 시작으로 이동',
        creative_step: '작업 단계 시작으로 이동',
      }[String(event && event.content_form_hint || '')] || '구간 시작으로 이동');
    }

    function adaptiveContextStateCopy(state) {
      return ({
        not_analyzed: '아직 분석 안 됨',
        no_useful_context_ranges: '쓸 만한 자동 구간 없음',
        partial_signal_coverage: '일부 근거로 자동 생성',
        analysis_failed: '자동 구간 분석 실패',
        disabled: '자동 구간 꺼짐',
        invalid_zero_duration_moment: '잘못된 0초 순간 제외',
      })[String(state || '')] || '상태 확인 필요';
    }

    function adaptiveCompressedContextRows(payload) {
      const compressed = payload && payload.compressed_context_structure || {};
      const contextRanges = Array.isArray(compressed.context_ranges) ? compressed.context_ranges : [];
      const userChapters = Array.isArray(compressed.user_chapters) ? compressed.user_chapters : [];
      const rawRanges = Array.isArray(payload && payload.ranges) ? payload.ranges : [];
      const dispositions = Array.isArray(compressed.candidate_dispositions)
        ? compressed.candidate_dispositions
        : [];
      if (!contextRanges.length || !userChapters.length) return [];
      const rangeById = new Map(
        contextRanges.map((row) => [String(row && row.context_range_id || ''), row]),
      );
      const rawRangeById = new Map(
        rawRanges.map((row) => [String(row && row.context_range_id || ''), row]),
      );
      return userChapters.map((chapter) => {
        const rangeId = String(chapter && chapter.context_range_id || '');
        const range = rangeById.get(rangeId) || {};
        const clock = range.clock && typeof range.clock === 'object' ? range.clock : {};
        const startSec = Number(clock.start_sec);
        const endSec = Number(clock.end_sec);
        if (!rangeId || !Number.isFinite(startSec) || !Number.isFinite(endSec) || endSec <= startSec) return null;
        const semanticTitle = chapter.semantic_title && typeof chapter.semantic_title === 'object'
          ? chapter.semantic_title
          : {};
        const title = String(semanticTitle.text || '내용을 확인할 장');
        const descriptionParts = [range.topic, range.activity]
          .map((value) => String(value || '').trim())
          .filter((value, index, values) => value && values.indexOf(value) === index && !title.includes(value));
        const sourceRanges = dispositions
          .filter((row) => (
            String(row && row.disposition || '') === 'merged_into'
            && String(row && row.merged_into_context_range_id || '') === rangeId
          ))
          .map((row) => rawRangeById.get(String(row.candidate_id || '')))
          .filter(Boolean)
          .sort((left, right) => (
            Number(left.start_sec || 0) - Number(right.start_sec || 0)
            || Number(left.end_sec || 0) - Number(right.end_sec || 0)
            || String(left.context_range_id || '').localeCompare(String(right.context_range_id || ''))
          ));
        const exactBoundarySource = sourceRanges.find((row) => (
          Math.abs(Number(row.start_sec) - startSec) < 0.001
          && Math.abs(Number(row.end_sec) - endSec) < 0.001
        ));
        const dominantSourceValue = (field, fallback, ignored = []) => {
          const values = Array.from(new Set(
            sourceRanges.map((row) => String(row && row[field] || '')).filter(Boolean),
          ));
          const counts = new Map();
          sourceRanges.forEach((row) => {
            const value = String(row && row[field] || '');
            if (!value || ignored.includes(value)) return;
            counts.set(value, Number(counts.get(value) || 0) + 1);
          });
          if (!counts.size) return values.length === 1 ? values[0] : fallback;
          return Array.from(counts.entries())
            .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))[0][0];
        };
        const supportModalityFamilies = Array.from(new Set(
          sourceRanges.flatMap((row) => Array.isArray(row.support_modality_families)
            ? row.support_modality_families
            : []),
        )).sort();
        const riskFlags = Array.from(new Set(
          sourceRanges.flatMap((row) => Array.isArray(row.risk_flags) ? row.risk_flags : []),
        )).sort();
        if (sourceRanges.length > 1) riskFlags.push('compressed_from_multiple_candidates');
        const evidenceModalityBreakdown = {};
        sourceRanges.forEach((sourceRow) => {
          const breakdown = sourceRow.evidence_modality_breakdown
            && typeof sourceRow.evidence_modality_breakdown === 'object'
            ? sourceRow.evidence_modality_breakdown
            : {};
          Object.entries(breakdown).forEach(([family, value]) => {
            const current = evidenceModalityBreakdown[family] || {bucket_count: 0, event_count: 0};
            evidenceModalityBreakdown[family] = {
              bucket_count: current.bucket_count + Number(value && value.bucket_count || 0),
              event_count: current.event_count + Number(value && value.event_count || 0),
            };
          });
        });
        const boundaryAlternatives = exactBoundarySource
          && Array.isArray(exactBoundarySource.boundary_alternatives)
          ? exactBoundarySource.boundary_alternatives.slice(0, 3)
          : [];
        return {
          context_range_id: rangeId,
          parent_context_range_id: range.parent_context_range_id || null,
          child_context_range_ids: Array.isArray(range.child_context_range_ids)
            ? range.child_context_range_ids.map((value) => String(value || '')).filter(Boolean)
            : [],
          start_sec: startSec,
          end_sec: endSec,
          title,
          preview_text: descriptionParts.join(' · '),
          broadcast_profile: dominantSourceValue('broadcast_profile', 'mixed_unknown'),
          segment_kind: dominantSourceValue('segment_kind', 'unknown', ['transition', 'unknown']),
          confidence_band: String(chapter.boundary_confidence || semanticTitle.confidence || 'unknown'),
          title_provenance_state: String(semanticTitle.provenance_state || 'unknown'),
          title_evidence_count: Array.isArray(semanticTitle.evidence_refs) ? semanticTitle.evidence_refs.length : 0,
          manager_correction: chapter.manager_correction && typeof chapter.manager_correction === 'object'
            ? chapter.manager_correction
            : null,
          support_modality_families: supportModalityFamilies,
          evidence_modality_breakdown: evidenceModalityBreakdown,
          boundary_alternatives: boundaryAlternatives,
          risk_flags: riskFlags,
          activity_state: range.activity || dominantSourceValue('activity_state', 'unknown'),
          topic_state: range.topic || dominantSourceValue('topic_state', 'unknown'),
          source_context_range_ids: sourceRanges
            .map((row) => String(row.context_range_id || ''))
            .filter(Boolean),
          manager_display_activity: String(chapter.manager_display_activity || ''),
          manager_display_activity_evidence: chapter.manager_display_activity_evidence
            && typeof chapter.manager_display_activity_evidence === 'object'
            ? chapter.manager_display_activity_evidence
            : null,
          confirmed: chapter.confirmed === true,
          public_safe: chapter.public_safe === true,
          is_compressed_user_chapter: true,
        };
      }).filter(Boolean).sort((left, right) => {
        const timeOrder = Number(left.start_sec || 0) - Number(right.start_sec || 0);
        if (timeOrder) return timeOrder;
        return Number(!!left.parent_context_range_id) - Number(!!right.parent_context_range_id);
      });
    }

    function adaptiveContextRows(workbench) {
      const payload = workbench && workbench.adaptive_context_ranges || {};
      const compressedRows = adaptiveCompressedContextRows(payload);
      if (compressedRows.length) return compressedRows;
      const ranges = Array.isArray(payload.ranges)
        ? payload.ranges.map((row) => ({ ...row, is_legacy_context_row: true }))
        : [];
      const gaps = Array.isArray(payload.unknown_gaps)
        ? payload.unknown_gaps.map((row) => ({
            ...row,
            context_range_id: row.gap_id,
            broadcast_profile: 'mixed_unknown',
            segment_kind: 'unknown',
            confidence_band: 'unknown',
            parent_context_range_id: null,
            support_modality_families: row.support_families || [],
            evidence_modality_breakdown: {},
            boundary_alternatives: [],
            risk_flags: ['unknown_gap'],
            is_unknown_gap: true,
            is_legacy_context_row: true,
          }))
        : [];
      return ranges.concat(gaps).sort((left, right) => {
        const timeOrder = Number(left.start_sec || 0) - Number(right.start_sec || 0);
        if (timeOrder) return timeOrder;
        return Number(!!left.parent_context_range_id) - Number(!!right.parent_context_range_id);
      });
    }

    function adaptivePublicTypeLabel(row) {
      const kind = String(row && row.segment_kind || 'unknown');
      const profile = String(row && row.broadcast_profile || 'mixed_unknown');
      const activity = String(row && row.manager_display_activity || '');
      if (kind === 'versus_bracket') return 'VS·월드컵';
      if (kind === 'watch_along') return '같이보기';
      if (kind === 'match_watch') return '경기 시청';
      if (kind === 'collab' || profile === 'collab_multi_pov') return '합방';
      if (['game_session', 'within_game_phase'].includes(kind) || profile === 'game_match') return '게임';
      if (activity === 'opening_talk') return '오프닝';
      if (activity === 'singing_and_closing_talk') return '마무리';
      if (kind === 'topic_discussion' || profile === 'talk_story') return '저스트채팅';
      return '방송';
    }

    function adaptivePublicSafeRow(row) {
      const title = adaptiveMeaningfulLabel(adaptiveProjectedTitle(row));
      if (!title || !row || !row.is_compressed_user_chapter) return false;
      return row.confirmed === true && row.public_safe === true;
    }

    function selectAdaptiveContextRow(row) {
      if (!row) return;
      selectSemanticRange(adaptiveContextEvent(row));
    }

    function renderViewerRoleSurface() {
      if (!viewerRoleSurfaceEl) return;
      const rows = adaptiveContextRows(signalTimelineState).filter(adaptivePublicSafeRow);
      const rowById = new Map(rows.map((row) => [String(row.context_range_id || ''), row]));
      const topRows = rows.filter((row) => {
        const parentId = String(row.parent_context_range_id || '');
        return !parentId || !rowById.has(parentId);
      });
      const childrenByParent = new Map();
      rows.forEach((row) => {
        const parentId = String(row.parent_context_range_id || '');
        if (!parentId || !rowById.has(parentId)) return;
        const current = childrenByParent.get(parentId) || [];
        current.push(row);
        childrenByParent.set(parentId, current);
      });
      const childRows = [];
      const cards = topRows.map((row, parentIndex) => {
        const title = adaptiveMeaningfulLabel(adaptiveProjectedTitle(row));
        const type = adaptivePublicTypeLabel(row);
        const startSec = Number(row.start_sec || 0);
        const endSec = Number(row.end_sec || startSec);
        const children = childrenByParent.get(String(row.context_range_id || '')) || [];
        const childHtml = children.length
          ? `
            <details class="report-role-viewer-children">
              <summary aria-expanded="false">하위 장면 ${children.length}개 보기</summary>
              ${children.map((child) => {
                const childIndex = childRows.push(child) - 1;
                return `
                  <div class="report-role-viewer-child">
                    <button type="button" data-viewer-child-index="${childIndex}">${escapeHtml(adaptiveProjectedTitle(child))}</button>
                    <button type="button" data-viewer-child-play-index="${childIndex}">하위 장면 재생</button>
                  </div>
                `;
              }).join('')}
            </details>
          `
          : '';
        return `
          <article class="report-role-viewer-card" data-viewer-parent-index="${parentIndex}">
            <div class="report-role-viewer-title"><span class="adaptive-context-type-badge">${escapeHtml(type)}</span><strong>${escapeHtml(title)}</strong></div>
            <div class="report-role-viewer-meta">${fmtSeconds(startSec)}–${fmtSeconds(endSec)}</div>
            <div class="adaptive-context-actions"><button type="button" data-viewer-play-index="${parentIndex}">이 구간 재생</button></div>
            ${childHtml}
          </article>
        `;
      }).join('');
      viewerRoleSurfaceEl.innerHTML = `
        <header class="report-role-viewer-head">
          <h1>방송 흐름</h1>
          <p class="meta-note">확인된 큰 장과 필요한 하위 장면만 표시합니다.</p>
        </header>
        <div class="report-role-viewer-list">${cards || '<div class="meta-note">공개할 수 있는 장을 확인 중입니다.</div>'}</div>
      `;
      viewerRoleSurfaceEl.querySelectorAll('details.report-role-viewer-children').forEach((details) => {
        const summary = details.querySelector('summary');
        const sync = () => summary && summary.setAttribute('aria-expanded', String(Boolean(details.open)));
        sync();
        details.addEventListener('toggle', sync);
      });
      viewerRoleSurfaceEl.querySelectorAll('[data-viewer-play-index]').forEach((button) => {
        button.addEventListener('click', () => {
          const row = topRows[Number(button.getAttribute('data-viewer-play-index'))];
          if (!row) return;
          setSplitView(true, { silent: true });
          splitPlayerSeek(Number(row.start_sec || 0));
        });
      });
      viewerRoleSurfaceEl.querySelectorAll('[data-viewer-child-index]').forEach((button) => {
        button.addEventListener('click', () => {
          selectAdaptiveContextRow(childRows[Number(button.getAttribute('data-viewer-child-index'))]);
        });
      });
      viewerRoleSurfaceEl.querySelectorAll('[data-viewer-child-play-index]').forEach((button) => {
        button.addEventListener('click', () => {
          const row = childRows[Number(button.getAttribute('data-viewer-child-play-index'))];
          if (!row) return;
          selectAdaptiveContextRow(row);
          setSplitView(true, { silent: true });
          splitPlayerSeek(Number(row.start_sec || 0));
        });
      });
    }

    function restoreRoleDetachedNodes() {
      Array.from(roleDetachedNodes.values()).reverse().forEach(({node, marker}) => {
        if (marker && marker.parentNode) marker.replaceWith(node);
      });
      roleDetachedNodes.clear();
    }

    function detachRoleNode(selector) {
      const node = document.querySelector(selector);
      if (!node || !node.parentNode || roleDetachedNodes.has(selector)) return;
      const marker = document.createComment('role-placeholder');
      node.parentNode.insertBefore(marker, node);
      node.remove();
      roleDetachedNodes.set(selector, {node, marker});
    }

    function setReportAppAttached(attached) {
      if (!reportAppEl) return;
      if (attached && !reportAppEl.isConnected && reportAppAnchor.parentNode) {
        reportAppAnchor.parentNode.insertBefore(reportAppEl, reportAppAnchor.nextSibling);
      } else if (!attached && reportAppEl.isConnected) {
        reportAppEl.remove();
      }
    }

    function applyReportPageRole(nextRole) {
      reportPageRole = ['manager', 'editor', 'viewer'].includes(nextRole) ? nextRole : 'manager';
      restoreRoleDetachedNodes();
      setReportAppAttached(true);
      if (reportAppEl) reportAppEl.classList.toggle('report-role-editor', reportPageRole === 'editor');
      if (viewerRoleSurfaceEl) viewerRoleSurfaceEl.hidden = reportPageRole !== 'viewer';
      if (reportPageRole === 'editor') {
        adaptiveContextMode = 'editor';
        [
          '.sidebar', '#sidebarResizer', '#previewFrame', '#adminPanelSummary',
          '#bundleMembershipSection', '#summaryCandidateSection', '#commentUsefulnessSection',
          '#managerNoteSection', '#approvalInboxSection', '#diagnosticsSection',
          '#contextVocabSection', '#revisionPanel', '#busyOverlay',
        ].forEach(detachRoleNode);
        setSplitView(true, { silent: true });
      } else if (reportPageRole === 'viewer') {
        adaptiveContextMode = 'viewer';
        ['#revisionPanel', '#busyOverlay'].forEach(detachRoleNode);
        renderViewerRoleSurface();
        const viewerPlayer = document.getElementById('splitPlayerPane');
        if (viewerPlayer && viewerRoleSurfaceEl) {
          detachRoleNode('#splitPlayerPane');
          viewerRoleSurfaceEl.insertBefore(viewerPlayer, viewerRoleSurfaceEl.firstChild);
          setSplitView(true, { silent: true });
        }
        setReportAppAttached(false);
      }
      if (reportRoleSwitchEl) {
        reportRoleSwitchEl.querySelectorAll('[data-report-page-role]').forEach((button) => {
          button.setAttribute(
            'aria-pressed',
            String(button.getAttribute('data-report-page-role') === reportPageRole),
          );
        });
      }
    }
