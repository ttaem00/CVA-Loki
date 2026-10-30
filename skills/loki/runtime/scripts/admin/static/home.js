
    const searchEl = document.getElementById('search');
    const focusSearchBtn = document.getElementById('focusSearchBtn');
    const listEl = document.getElementById('list');
    const metaEl = document.getElementById('meta');
    const errorEl = document.getElementById('error');
    const streamerBarEl = document.getElementById('streamerBar');
    const bundleSelectStatusEl = document.getElementById('bundleSelectStatus');
    const bundlePreviewPanelEl = document.getElementById('bundlePreviewPanel');
    const bundlePreviewBtn = document.getElementById('bundlePreviewBtn');
    const bundleCreateBtn = document.getElementById('bundleCreateBtn');
    const bundleClearBtn = document.getElementById('bundleClearBtn');
    const bundleTitleInput = document.getElementById('bundleTitleInput');
    const bundleModeBtn = document.getElementById('bundleModeBtn');
    const bundlePanelEl = document.querySelector('[data-testid="existing-summary-bundle-panel"]');
    const loadMoreBtn = document.getElementById('loadMoreBtn');
    const operationsDetails = document.getElementById('operationsDetails');
    let reports = [];
    const initialParams = new URLSearchParams(window.location.search);
    let activeStreamer = initialParams.get('streamer') || '__all__';
    let streamerFacets = [];
    let catalogStats = {total: 0, matchedTotal: 0};
    let nextOffset = 0;
    let hasMore = false;
    let bundleMode = false;
    let operationsLoaded = false;
    let searchTimer = null;
    let catalogGeneration = 0;
    let catalogController = null;
    let loadedCatalogFilter = null;
    const PAGE_SIZE = 24;
    const selectedBundleBases = new Set();
    let lastBundlePreview = null;
    let lastBundlePreviewSignature = '';
    let bundleBusy = false;
    if (searchEl) searchEl.value = initialParams.get('q') || '';

    // pass9 — "/" 키로 검색창 포커스 (published web 와 동일 동작).
    document.addEventListener('keydown', (e) => {
      const t = e.target;
      const isField = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
      if (e.key === '/' && !isField && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (searchEl) { e.preventDefault(); searchEl.focus(); searchEl.select(); }
      }
    });
    if (focusSearchBtn) {
      focusSearchBtn.addEventListener('click', () => {
        setTimeout(() => {
          if (searchEl) { searchEl.focus(); searchEl.select(); }
        }, 0);
      });
    }

    async function api(path, options = undefined) {
      const res = await fetch(path, options);
      const text = await res.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
      if (!res.ok) throw new Error(data.error || data.raw || `HTTP ${res.status}`);
      return data;
    }
    document.querySelectorAll('[data-open-manager-entry]').forEach(button => {
      button.addEventListener('click', async () => {
        const status = button.parentElement.querySelector('.journey-status');
        button.disabled = true;
        status.textContent = '창을 여는 중입니다…';
        try {
          const data = await api('/api/manager-entry/open', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({entry:button.dataset.openManagerEntry})
          });
          status.textContent = data.entry === 'chzzk'
            ? 'CHZZK 수집 창을 열었습니다. 등록 스트리머 감지가 자동으로 시작됩니다.'
            : 'YouTube 요약 창을 열었습니다. CHZZK 수집은 시작하거나 멈추지 않았습니다.';
        } catch (error) {
          status.textContent = `창을 열지 못했습니다. ${error.message}`;
        } finally {
          button.disabled = false;
        }
      });
    });

    function streamerKey(report) {
      return report.streamer_id || report.channel_id || report.channel || '__unknown__';
    }
    function streamerLabel(report) {
      return report.channel || report.streamer_id || '(unknown)';
    }
    function escapeHtml(s) {
      return String(s || '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[c]));
    }
    function label(report) {
      return `${report.video_no} ${report.title} ${streamerLabel(report)} ${report.site_only ? '공개만 자막없음 site-only' : ''}`.toLowerCase();
    }
    function renderStatusBadges(report) {
      const badges = Array.isArray(report.status_badges) ? report.status_badges : [];
      if (!badges.length) return '';
      return `<div class="row status-row">${badges.map((badge) => {
        const level = String(badge.level || 'unknown').replace(/[^a-z0-9_-]/gi, '');
        const title = badge.title ? ` title="${escapeHtml(badge.title)}"` : '';
        return `<span class="badge status status-${level}"${title}>${escapeHtml(badge.label)}</span>`;
      }).join('')}</div>`;
    }
    function renderCanonicalBadges(report) {
      const badges = [];
      if (report.canonical_source === 'site' && !report.site_only) {
        badges.push('<span class="badge" title="공개된 보고서를 정본으로 열었습니다.">공개 정본</span>');
      }
      if (report.canonical_conflict) {
        const title = report.canonical_source === 'site'
          ? '같은 VOD의 로컬 보고서와 공개 보고서 연결이 명확하지 않습니다. 공개 정본을 열었으며 저장 전 확인이 필요합니다.'
          : '같은 VOD의 로컬 보고서가 여러 개입니다. 가장 최근 파일을 열었으며 저장 전 확인이 필요합니다.';
        badges.push(`<span class="badge status status-warning" title="${escapeHtml(title)}">중복 검토 필요 (${Number(report.duplicate_artifact_count || 0)})</span>`);
      }
      return badges.join('');
    }
    function renderWorkEvidenceHint(report) {
      const ev = report.work_evidence || {};
      if (!ev.work_dir_present || !Number.isFinite(Number(ev.visible_artifact_count))) return '';
      const count = Number(ev.visible_artifact_count);
      const excluded = Number(ev.excluded_artifact_count || 0);
      const title = excluded > 0
        ? `민감 이름 ${excluded}개는 목록에서 제외했습니다.`
        : '작업 폴더 자료가 있습니다.';
      return `<div class="work-evidence-row"><span class="badge" title="${escapeHtml(title)}">작업자료 ${count}개</span></div>`;
    }

    function renderRuntimeDataGuard(guard) {
      const el = document.getElementById('runtimeDataBanner');
      if (!el || !guard) return;
      const counts = guard.counts || {};
      const split = !!guard.code_data_split;
      const safe = !!guard.safe_for_dangerous_actions;
      el.hidden = false;
      el.className = 'runtime-data-banner' + (safe ? ' ok' : '');
      const prefix = split ? '코드 폴더와 데이터 폴더가 다릅니다.' : '코드 폴더와 데이터 폴더가 같습니다.';
      const action = safe ? '실제 리포트 데이터가 확인됐습니다.' : '위험 작업은 자동으로 막았습니다.';
      const dataRootLabel = guard.data_root_display || guard.data_root_ref || guard.data_root_source || '데이터 폴더';
      const lifecycle = guard.worktree_lifecycle || {};
      const worktreeMeta = Number.isFinite(Number(lifecycle.registered_count))
        ? ` · 임시 작업폴더 ${Number(lifecycle.registered_count)}개`
        : '';
      const lifecycleMessage = lifecycle.manager_message
        ? `<span class="runtime-data-meta">${escapeHtml(lifecycle.manager_message)} 다음 자동조치: ${escapeHtml(lifecycle.next_automatic_action || '상태를 다시 확인합니다.')}</span>`
        : '';
      el.innerHTML = `
        <strong>${escapeHtml(prefix)}</strong> ${escapeHtml(action)} ${escapeHtml(guard.manager_message || '')}
        <span class="runtime-data-meta">운영자료 정본: ${escapeHtml(dataRootLabel)} · 리포트 ${Number(counts.output_md || 0)}개 · 공개 HTML ${Number(counts.site_html || 0)}개 · 작업자료 폴더 ${Number(counts.work_dirs || 0)}개${worktreeMeta}</span>
        ${lifecycleMessage}
      `;
      ['saveDestinationsBtn','deployDryRunBtn','multiBackupDryRunBtn','dryRunBtn','kopiaTrayRepairBtn'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = !safe;
      });
    }

    function renderLlmCallHomeStatus(data) {
      const el = document.getElementById('llmCallHomeStatus');
      if (!el) return;
      if (!data || data.status === 'ERROR') {
        el.textContent = 'AI 처리 상태를 읽지 못했습니다. Report Admin은 계속 사용할 수 있습니다.';
        return;
      }
      const latest = data.latest_run || {};
      const roles = latest.roles || {};
      const observations = roles.observations || {};
      const core = roles.core || {};
      const usage = latest.usage || {};
      const modeLabel = {
        automatic_recommended: '자동 추천 (OpenAI 지원 경로 → Codex)',
        codex_only: 'Codex만 사용',
        advanced: '고급 사용자 지정',
      }[data.manager_route_mode] || '확인 필요';
      const roleText = (role, emptyText) => Number(role.success_count || 0) > 0
        ? `${(role.models || []).join(' → ') || '모델 확인 필요'} · ${(role.providers || []).join(' → ') || '통로 확인 필요'} · ${Number(role.success_count || 0)}회${role.fallback_used ? ' · 안전 전환 있음' : ''}`
        : emptyText;
      const openaiText = {
        sponsored_verified: '이전 지원 표시 · 공식 비용 확인 필요',
        fail_closed: 'OpenAI 증명 실패 · 결과 미사용',
        unproven: 'OpenAI 생성 성공 · 개별 호출의 무료 적용은 공식 집계 확인 대기',
        unavailable: 'OpenAI 한도·통신 문제 · Codex로 이어서 처리',
        not_used: 'OpenAI 지원 경로 사용 안 함',
      }[usage.openai_sponsored] || 'OpenAI 사용량 확인 필요';
      const codexText = usage.codex_subscription === 'used'
        ? 'Codex 구독 이용량 사용'
        : 'Codex 구독 이용량 사용 안 함';
      const account = data.openai_account_usage || {};
      const remaining = account.remaining_tokens || {};
      const hasObservation = ['observed', 'stale'].includes(account.status);
      const quotaText = hasObservation
        ? `마지막 공식 집계 기준 잔여 추정: Sol 그룹 ${Number(remaining.big).toLocaleString()} · Terra/Luna 그룹 ${Number(remaining.small).toLocaleString()} 토큰 (${account.fetched_at || ''}). 집계 지연·다른 앱 사용은 아직 반영되지 않을 수 있습니다.`
        : '무료 잔여량 확인 불가 · API 호출 전에 기존 Codex로 전환합니다. 조직 Usage/Costs 읽기 연결이 필요합니다.';
      const billingText = hasObservation
        ? `공식 계정 집계 비용 $${Number(account.account_cost_usd).toFixed(4)} · 적용 구분: ${Object.entries(account.service_tier_tokens || {}).map(([tier, tokens]) => `${tier} ${Number(tokens).toLocaleString()} 토큰`).join(', ') || '사용 내역 없음'}. 개별 호출의 무료 적용 확정과는 구분합니다.`
        : '개별 호출의 무료 적용과 실시간 잔여량은 아직 확인되지 않았습니다.';
      el.innerHTML = `
        <strong>현재 방식: ${escapeHtml(modeLabel)}</strong>
        <span class="runtime-data-meta">역할: 구간 관찰은 Terra, 핵심 구조·Story/Point/Highlight 판단은 Sol</span>
        <span class="runtime-data-meta" data-testid="llm-observations-actual">이번 구간 관찰: ${escapeHtml(roleText(observations, '아직 성공 기록 없음'))}</span>
        <span class="runtime-data-meta" data-testid="llm-core-actual">이번 핵심 판단: ${escapeHtml(roleText(core, '아직 성공 기록 없음'))}</span>
        <span class="runtime-data-meta" data-testid="llm-usage-actual">${escapeHtml(openaiText)} / ${escapeHtml(codexText)}</span>
        <span class="runtime-data-meta" data-testid="llm-free-quota">${escapeHtml(quotaText)} · 매일 오전 9시 초기화 (한국 시간)</span>
        <span class="runtime-data-meta" data-testid="llm-billing-evidence">${escapeHtml(billingText)} <a href="https://platform.openai.com/usage/chat-completions" target="_blank" rel="noopener noreferrer">공식 사용량 확인</a></span>
        <span class="llm-route-privacy">실제 실행 기록만 표시하며 키 값·로그인 정보·원문은 읽거나 표시하지 않습니다.</span>
      `;
    }

    async function loadLlmCallHomeStatus() {
      try {
        renderLlmCallHomeStatus(await api('/api/llm-call/status'));
      } catch (err) {
        renderLlmCallHomeStatus({status: 'ERROR'});
      }
    }

    function formatElapsedSeconds(value) {
      if (value === null || value === undefined || value === '') return '없음';
      const n = Number(value);
      if (!Number.isFinite(n) || n < 0) return '없음';
      const h = Math.floor(n / 3600);
      const m = Math.floor((n % 3600) / 60);
      const s = Math.floor(n % 60);
      return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }
    function renderLiveCollectorHomeStatus(data) {
      const el = document.getElementById('liveCollectorHomeStatus');
      if (!el) return;
      if (!data || data.status === 'ERROR') {
        el.textContent = '라이브 맥락 수집 상태를 읽지 못했습니다. Report Admin은 계속 사용할 수 있습니다.';
        return;
      }
      const armed = data.status === 'armed';
      const collecting = data.status === 'collecting';
      const hasViewer = data.last_viewer_count !== null && data.last_viewer_count !== undefined && data.last_viewer_count !== '';
      const viewer = hasViewer && Number.isFinite(Number(data.last_viewer_count)) ? `${Number(data.last_viewer_count).toLocaleString()}명` : '없음';
      const match = data.official_match_shadow || {};
      const events = Array.isArray(match.recent_events) ? match.recent_events : [];
      const eventRows = events.length
        ? `<ul class="official-match-events">${events.map((event) => `<li><span>${escapeHtml(formatElapsedSeconds(event.match_time_sec))}</span> ${escapeHtml(event.label || '경기 이벤트')}</li>`).join('')}</ul>`
        : `<p class="runtime-data-meta">${escapeHtml(match.empty_message || '아직 표시할 경기 이벤트가 없습니다.')}</p>`;
      el.innerHTML = `
        <strong>${collecting ? '수집 중' : (armed ? '수집 준비' : '수집 중지')}</strong>
        <span class="runtime-data-meta">마지막 성공 ${escapeHtml(data.last_success_at || '없음')} · 시청자 ${escapeHtml(viewer)} · 경과 ${escapeHtml(formatElapsedSeconds(data.last_elapsed_sec))}</span>
        <span class="runtime-data-meta">제목 변경 ${escapeHtml(data.last_title_change || '없음')} · 저장 ${Number(data.sample_count || 0)}행</span>
        <span class="runtime-data-meta">${escapeHtml(data.manager_message || '')}</span>
        <span class="runtime-data-meta">${escapeHtml(data.browser_route_status || 'UNVERIFIED_MAIN_CHROME_NOT_CLAIMED')}</span>
        <section class="official-match-shadow" aria-labelledby="officialMatchShadowTitle" data-testid="official-match-shadow">
          <div class="official-match-shadow-heading">
            <h3 id="officialMatchShadowTitle">LoL 경기 데이터 미리보기</h3>
            <span class="badge status status-${escapeHtml(match.tone || 'unknown')}">${escapeHtml(match.state_label || '확인 중')}</span>
          </div>
          <p class="official-match-summary"><strong>${escapeHtml(match.match_label || '현재 경기 없음')}</strong> · ${escapeHtml(match.set_label || '세트 미확인')} · ${escapeHtml(match.game_clock_label || '경기 시각 미확인')}</p>
          <dl class="official-match-facts">
            <div><dt>근거</dt><dd>${escapeHtml(match.source_label || '공급자 확인 중')}</dd></div>
            <div><dt>신뢰 수준</dt><dd>${escapeHtml(match.confidence_label || '확인 중')}</dd></div>
            <div><dt>VOD 시간 연결</dt><dd>${escapeHtml(match.mapping_label || '연결 안 됨')}</dd></div>
            <div><dt>대회·시리즈</dt><dd>${escapeHtml(match.competition_label || '아직 확인되지 않음')}</dd></div>
          </dl>
          ${eventRows}
          <p class="official-match-shadow-note">${escapeHtml(match.effect_label || '현재 요약에는 반영하지 않습니다.')}</p>
          <p class="runtime-data-meta">${escapeHtml(match.next_action || '')}</p>
        </section>
      `;
    }
    async function loadLiveCollectorHomeStatus() {
      try {
        renderLiveCollectorHomeStatus(await api('/api/live-context-collector/status'));
      } catch (err) {
        renderLiveCollectorHomeStatus({status: 'ERROR'});
      }
    }
    function renderServerRoleHomeStatus(data) {
      const el = document.getElementById('serverRoleHomeStatus');
      if (!el) return;
      if (!data || data.status === 'ERROR') {
        el.innerHTML = '<div class="server-operation-card"><strong>서버 상태를 읽지 못했습니다.</strong><span>리포트 목록과 검토할 요약은 계속 사용할 수 있습니다.</span></div>';
        return;
      }
      const indicator = data.manager_status_indicator || {};
      const stage = data.stage_c || {};
      const operation = data.operation_summary || {};
      const surface = data.operation_surface || {};
      const queue = data.vod_run_queue || {};
      const notifications = Array.isArray(indicator.notifications) ? indicator.notifications : [];
      const toneLabel = {green: '초록', yellow: '노랑', red: '빨강'}[indicator.color] || indicator.color || '확인 중';
      const nextAction = operation.next_useful_action || (
        Number(indicator.candidate_count || 0) > 0
          ? '검토할 요약에서 서버 생성 초안을 열어 확인합니다.'
          : (notifications.length ? '상세 상태에서 경고를 확인합니다.' : '새 서버 작업이 생기면 검토할 요약에 표시됩니다.')
      );
      el.innerHTML = `
        <div class="server-operation-card">
          <strong>${escapeHtml(indicator.summary || data.manager_message || '워크스테이션 우선 운영')}</strong>
          <span>상태 표지: ${escapeHtml(toneLabel)} · 후보 ${Number(indicator.candidate_count || 0)}개 · 실행 큐 ${Number(queue.total || 0)}건</span>
          <span>${escapeHtml(notifications.length ? notifications.join(' · ') : '조치 필요한 알림 없음')}</span>
          <span>${escapeHtml(surface.headless_boundary_label || 'Report Admin은 관리자-visible 상태/제어 표면입니다. 별도 창은 증명된 경우에만 표시합니다.')}</span>
        </div>
        <div class="server-operation-card">
          <strong>마지막 서버 작업</strong>
          <span>마지막 실행 ${escapeHtml(indicator.last_run_time || stage.last_run_time || '없음')}</span>
          <span>최근 결과 ${escapeHtml(stage.status || data.status || '확인 필요')} · 다음 실행 ${escapeHtml(indicator.next_run_time || stage.next_run_time || '확인 필요')}</span>
        </div>
        <div class="server-operation-card">
          <strong>다음 할 일</strong>
          <span>${escapeHtml(nextAction)}</span>
          <span>검토/편집/번들은 가능하지만 공개, 기본 반영, 외부 제출은 자동으로 실행되지 않습니다.</span>
        </div>
        <div class="server-operation-card warning">
          <strong>오류와 복구 안내</strong>
          <span>현재 오류: ${escapeHtml(surface.current_error || '없음')}</span>
          <span>${escapeHtml(surface.recovery_guidance || '문제가 있으면 상세 상태에서 원인과 다음 조치를 확인합니다.')}</span>
          <span>재시작: ${escapeHtml(surface.restart_guidance || 'Report Admin은 report_admin.bat로 다시 엽니다. 서비스/시작프로그램 등록은 관리자 승인 전에는 하지 않습니다.')}</span>
        </div>
        <div class="server-operation-card">
          <strong>안전 경계와 준비 상태</strong>
          <span>백업 ${escapeHtml(operation.backup_readiness || indicator.backup_warning || '확인 필요')} · 자격/제공자 ${escapeHtml(operation.credential_readiness || '값을 읽지 않음, 재연결은 관리자 전용')}</span>
          <span>${escapeHtml(operation.safe_lifecycle || '중지/시작/상세 상태는 승인된 Report Admin 경로에서만 다룹니다.')}</span>
        </div>
      `;
    }
    async function loadServerRoleHomeStatus() {
      try {
        renderServerRoleHomeStatus(await api('/api/server-role/readiness'));
      } catch (err) {
        renderServerRoleHomeStatus({status: 'ERROR'});
      }
    }
    function reviewInboxActionsHtml(row) {
      const actions = Array.isArray(row.actions) ? row.actions : [];
      if (!actions.length) return '';
      return `<div class="review-inbox-actions">${actions.slice(0, 3).map((action) => {
        const target = action.external ? ' target="_blank" rel="noreferrer"' : '';
        return `<a class="review-inbox-action" href="${escapeHtml(action.href || '#')}"${target}>${escapeHtml(action.label || '열기')}</a>`;
      }).join('')}</div>`;
    }
    function reviewInboxPrimaryActionHtml(row) {
      const adoption = row.adoption || {};
      if (adoption.state === 'already_added' || adoption.state === 'added') {
        return `<div class="review-inbox-primary done"><span>${escapeHtml(adoption.state_label || '이미 요약 목록에 있음')}</span>${adoption.report_href ? `<a class="review-inbox-action primary-link" href="${escapeHtml(adoption.report_href)}">편집 열기</a>` : ''}</div>`;
      }
      if (adoption.state === 'blocked_no_clobber') {
        return `<div class="review-inbox-primary blocked"><span>${escapeHtml(adoption.state_label || '추가 보류')}</span><a class="review-inbox-action primary-link" href="/reports">목록 확인</a></div>`;
      }
      if (adoption.confirmation_required === false || adoption.state === 'waiting_for_server_draft') {
        return `<div class="review-inbox-primary blocked"><span>${escapeHtml(adoption.state_label || row.review_state_label || '확인 중')}</span><a class="review-inbox-action primary-link" href="${escapeHtml(adoption.list_href || '/vod-run-queue')}">실행 큐 보기</a></div>`;
      }
      return `<div class="review-inbox-primary"><button class="review-inbox-adopt-btn" type="button" data-video-no="${escapeHtml(row.video_no || '')}" data-confirmation-token="${escapeHtml(adoption.confirmation_token || '')}" data-confirmation-action="${escapeHtml(adoption.confirmation_action || '')}" data-confirmation-label="${escapeHtml(adoption.confirmation_label || '요약 목록에 추가')}">요약 목록에 추가</button><span class="review-inbox-primary-note">관리자 확인 필요 · 덮어쓰기 없음</span></div>`;
    }
    function reviewInboxCardHtml(row) {
      return `
        <article class="review-inbox-card" data-video-no="${escapeHtml(row.video_no || '')}">
          <div class="review-inbox-card-head">
            <div>
              <div class="review-inbox-card-title">${escapeHtml(row.title || row.video_no || '제목 없음')}</div>
              <div class="review-inbox-card-meta">
                <span>${escapeHtml(row.video_no || '')}</span>
                <span>${escapeHtml(row.channel_name || row.channel_id || '채널 미확인')}</span>
                <span>${escapeHtml(row.stage_label || row.stage || '')}</span>
                <span>${escapeHtml(row.recorded_at_label || '')}</span>
              </div>
            </div>
            <span class="review-inbox-badge">${escapeHtml(row.review_state_label || row.manager_label || row.status || '검토 필요')}</span>
          </div>
          ${reviewInboxPrimaryActionHtml(row)}
          ${reviewInboxActionsHtml(row)}
          <div class="review-inbox-note">${escapeHtml((row.adoption || {}).manager_message || row.manager_review_note || '')}</div>
        </article>`;
    }
    function renderGeneratedReviewInboxHome(data) {
      const el = document.getElementById('generatedReviewInboxHome');
      if (!el) return;
      if (!data || data.status === 'ERROR') {
        el.dataset.state = 'error';
        el.innerHTML = `
          <div class="review-inbox-empty" role="alert">
            <span class="review-inbox-state-title">검토할 요약을 읽지 못했습니다.</span>
            리포트 목록은 계속 사용할 수 있습니다.
            <div class="review-inbox-state-actions">
              <button class="review-inbox-action" type="button" data-review-inbox-retry="true">다시 시도</button>
              <a class="review-inbox-action" href="/server-role">서버 상태 보기</a>
            </div>
          </div>`;
        return;
      }
      const rows = Array.isArray(data.records) ? data.records : [];
      const summary = data.summary || {};
      if (!rows.length) {
        el.dataset.state = 'empty';
        el.innerHTML = `
          <div class="review-inbox-empty">
            <span class="review-inbox-state-title">지금 검토할 서버 생성 요약은 없습니다.</span>
            새 VOD가 안전하게 요약되면 이곳에 표시됩니다.
            <div class="review-inbox-state-actions">
              <a class="review-inbox-action" href="/server-role">서버 상태 보기</a>
              <a class="review-inbox-action" href="#search">일반 목록에서 찾기</a>
            </div>
          </div>`;
        return;
      }
      el.dataset.state = 'success';
      const primaryRow = rows[0];
      const overflowCount = Math.max(0, rows.length - 1);
      el.innerHTML = `
        <div class="review-inbox-summary">
          <strong>검토 필요 ${Number(summary.needs_review || rows.length)}개</strong>
          <span>열 수 있는 초안 ${Number(summary.ready_with_artifacts || rows.length)}개</span>
          <span>자동 공개/기본 반영 없음</span>
        </div>
        <div class="review-inbox-list">${reviewInboxCardHtml(primaryRow)}</div>
        ${overflowCount ? `<div class="review-inbox-empty"><span class="review-inbox-state-title">다른 초안 ${Number(overflowCount)}개가 더 있습니다.</span><a class="review-inbox-action" href="/review-inbox">전체 검토함 열기</a></div>` : ''}`;
    }
    async function adoptReviewInboxDraft(videoNo, button) {
      if (!videoNo || button?.disabled) return;
      if (button) { button.disabled = true; button.textContent = '추가 중...'; button.setAttribute('aria-busy', 'true'); }
      try {
        const result = await api('/api/review-inbox/adopt', {method: 'POST', body: JSON.stringify({
          video_no: videoNo,
          confirmation_token: button ? button.dataset.confirmationToken || '' : '',
          confirmation_action: button ? button.dataset.confirmationAction || '' : '',
          confirmation_label: button ? button.dataset.confirmationLabel || '요약 목록에 추가' : '요약 목록에 추가',
          manager_confirmed: true
        })});
        await loadGeneratedReviewInboxHome();
        const message = result.state === 'already_added' ? '이미 요약 목록에 있음' : '요약 목록에 추가됨';
        const statusEl = document.getElementById('managerStatusText');
        if (statusEl) statusEl.textContent = message;
      } catch (err) {
        if (button) { button.disabled = false; button.textContent = '요약 목록에 추가'; button.removeAttribute('aria-busy'); }
        const statusEl = document.getElementById('managerStatusText');
        if (statusEl) statusEl.textContent = err.message;
      }
    }
    document.addEventListener('click', (event) => {
      const retry = event.target.closest && event.target.closest('[data-review-inbox-retry="true"]');
      if (retry) {
        loadGeneratedReviewInboxHome();
        return;
      }
      const button = event.target.closest && event.target.closest('.review-inbox-adopt-btn');
      if (!button) return;
      adoptReviewInboxDraft(button.dataset.videoNo || '', button);
    });
    async function loadGeneratedReviewInboxHome() {
      try {
        renderGeneratedReviewInboxHome(await api('/api/review-inbox'));
      } catch (err) {
        renderGeneratedReviewInboxHome({status: 'ERROR'});
      }
    }
    async function updateLiveCollector(action) {
      const btn = action === 'start' ? document.getElementById('liveCollectorStartBtn') : document.getElementById('liveCollectorStopBtn');
      if (btn) btn.disabled = true;
      try {
        renderLiveCollectorHomeStatus(await api(`/api/live-context-collector/${action}`, {method: 'POST', body: JSON.stringify({})}));
      } catch (err) {
        renderLiveCollectorHomeStatus({status: 'ERROR'});
      } finally {
        if (btn) btn.disabled = false;
      }
    }
    const liveCollectorStartBtn = document.getElementById('liveCollectorStartBtn');
    const liveCollectorStopBtn = document.getElementById('liveCollectorStopBtn');
    if (liveCollectorStartBtn) liveCollectorStartBtn.addEventListener('click', () => updateLiveCollector('start'));
    if (liveCollectorStopBtn) liveCollectorStopBtn.addEventListener('click', () => updateLiveCollector('stop'));

    function editPatternReadyRecord(data) {
      const rows = Array.isArray(data.records) ? data.records : [];
      return rows.find((row) => row && row.edit_pattern_annotation_ready === true) || null;
    }

    function alignmentRecordId(row) {
      if (!row) return '';
      if (row.record_id) return String(row.record_id);
      const edited = row.edited_video_id || '';
      const source = row.source_video_id || '';
      return edited || source ? `${edited}:${source}` : '';
    }

    function renderEditPatternHomeAction(data) {
      const el = document.getElementById('editPatternHomeAction');
      if (!el || !data) return;
      const readiness = data.edit_pattern_readiness || {};
      const record = editPatternReadyRecord(data);
      const status = readiness.manager_visible_status || '';
      if (!status && !record) return;
      const action = readiness.manager_next_action || '자동 근거 후보를 먼저 만들고, 관리는 일부 표본만 점검합니다.';
      const recordId = alignmentRecordId(record);
      const href = record
        ? `/alignment-review?filter=edit_pattern_ready&record_id=${encodeURIComponent(recordId)}`
        : '/alignment-review?filter=edit_pattern_ready';
      el.hidden = false;
      el.innerHTML = `
        <p class="focus-status-title">편집 패턴 검토 · ${escapeHtml(status || '후보 확인 필요')}</p>
        <p class="focus-status-copy">${escapeHtml(action)}</p>
        <a class="focus-link" href="${escapeHtml(href)}">편집 패턴 후보 보기</a>
      `;
    }

    async function loadEditPatternHomeAction() {
      try {
        renderEditPatternHomeAction(await api('/api/alignment-review'));
      } catch (err) {
        // Home report selection must remain usable even if the review queue is unavailable.
      }
    }

    function isExistingSummaryBundleEligible(report) {
      return !!(report && report.kind === 'vod' && report.base && report.existing_summary_bundle_eligible !== false);
    }
    function selectedBundleReports() {
      return reports.filter((report) => selectedBundleBases.has(report.base) && isExistingSummaryBundleEligible(report));
    }
    function bundleSelectionSignature(items = selectedBundleReports()) {
      return items.map((report) => String(report.base || '')).sort().join('|');
    }
    function bundleSelectionsPayload(items = selectedBundleReports()) {
      return items.map((report) => ({
        base: report.base,
        video_no: report.video_no || '',
        title: report.title || '',
      }));
    }
    function invalidateBundlePreview() {
      lastBundlePreview = null;
      lastBundlePreviewSignature = '';
      if (bundlePreviewPanelEl) {
        bundlePreviewPanelEl.hidden = true;
        bundlePreviewPanelEl.className = 'bundle-preview';
        bundlePreviewPanelEl.innerHTML = '';
      }
    }
    function updateBundleSelectionPanel() {
      const selected = selectedBundleReports();
      const count = selected.length;
      const hasPreview = !!(lastBundlePreview && lastBundlePreview.ok && lastBundlePreviewSignature === bundleSelectionSignature(selected));
      if (bundleSelectStatusEl) {
        if (bundleBusy) {
          bundleSelectStatusEl.textContent = '처리 중입니다. 잠시만 기다려 주세요.';
        } else if (count < 2) {
          bundleSelectStatusEl.textContent = `현재 ${count}개 선택됨 - 요약 리포트 2개 이상을 선택하면 미리 볼 수 있습니다.`;
        } else if (hasPreview) {
          bundleSelectStatusEl.textContent = `${count}개 선택됨 - 미리보기를 확인했습니다. 생성할 수 있습니다.`;
        } else {
          bundleSelectStatusEl.textContent = `${count}개 선택됨 - 먼저 미리보기로 생성 가능 여부를 확인하세요.`;
        }
      }
      if (bundlePreviewBtn) bundlePreviewBtn.disabled = bundleBusy || count < 2;
      if (bundleCreateBtn) bundleCreateBtn.disabled = bundleBusy || !hasPreview;
      if (bundleClearBtn) bundleClearBtn.disabled = bundleBusy || count === 0;
    }
    function bundleReasonLabel(reason) {
      const labels = {
        selection_count_less_than_two: '요약을 2개 이상 선택해야 합니다.',
        valid_member_count_less_than_two: '생성 가능한 요약이 2개 미만입니다.',
        cross_streamer_mismatch: '서로 다른 스트리머 리포트는 한 번에 묶을 수 없습니다.',
        duplicate_selection: '같은 VOD가 중복 선택됐습니다.',
        missing_report: '리포트 파일을 찾지 못했습니다.',
        missing_metadata: '메타데이터가 없습니다.',
        stale_bundle_member: '번들 구성에서 온 오래된 항목입니다.',
        missing_vod_identity: 'VOD 식별값이 없습니다.',
        vod_identity_mismatch: '선택한 VOD 번호와 리포트 식별값이 다릅니다.',
        missing_report_markdown: '요약 Markdown이 없습니다.',
        missing_report_html: '요약 HTML이 없습니다.',
        viewer_editor_payload_missing: '요약 화면 근거 데이터가 없습니다.',
        missing_source_time_context: '시간 근거가 부족합니다.',
        viewer_editor_payload_downgrade_risk: '생성하면 장면 근거가 약해질 수 있습니다.',
      };
      return labels[reason] || reason || '확인 필요';
    }
    function renderBundlePreview(data) {
      if (!bundlePreviewPanelEl) return;
      const plan = data || {};
      const bundle = plan.bundle || {};
      const diagnostics = plan.diagnostics || {};
      const items = Array.isArray(plan.items) ? plan.items : [];
      const invalid = items.filter((item) => item && item.ok !== true);
      const selected = selectedBundleReports();
      bundlePreviewPanelEl.hidden = false;
      bundlePreviewPanelEl.className = `bundle-preview ${plan.ok ? 'ok' : 'blocked'}`;
      if (plan.created || plan.href) {
        bundlePreviewPanelEl.innerHTML = `
          <strong>${escapeHtml(plan.message || '기존 요약 번들을 만들었습니다.')}</strong>
          ${plan.href ? `<div><a href="${escapeHtml(plan.href)}">생성된 번들 열기</a></div>` : ''}
        `;
        return;
      }
      const selectedList = selected.map((report) => (
        `<li>${escapeHtml(report.title || report.base)} <span class="badge">${escapeHtml(report.video_no || report.base)}</span></li>`
      )).join('');
      if (plan.ok) {
        bundlePreviewPanelEl.innerHTML = `
          <strong>생성 가능</strong>
          <div>${escapeHtml(bundle.title || bundle.bundle_id || '선택한 기존 요약 번들')} · ${Number(bundle.vod_count || selected.length)}개 VOD</div>
          <ol class="bundle-preview-list">${selectedList}</ol>
        `;
        return;
      }
      const blockers = invalid.length
        ? invalid.map((item) => {
            const reasons = Array.isArray(item.reasons) && item.reasons.length ? item.reasons : ['확인 필요'];
            return `<li>${escapeHtml(item.title || item.base || item.video_no || '선택 항목')}: ${escapeHtml(reasons.map(bundleReasonLabel).join(', '))}</li>`;
          }).join('')
        : (Array.isArray(diagnostics.global_reasons) && diagnostics.global_reasons.length
            ? diagnostics.global_reasons.map((reason) => `<li>${escapeHtml(bundleReasonLabel(reason))}</li>`).join('')
            : `<li>선택 항목을 확인하세요.</li>`);
      bundlePreviewPanelEl.innerHTML = `
        <strong>생성 전 확인 필요</strong>
        <div>${escapeHtml(plan.manager_label || '막힌 항목을 확인하세요.')}</div>
        <ul class="bundle-preview-list">${blockers}</ul>
      `;
    }
    async function previewExistingSummaryBundle() {
      const selected = selectedBundleReports();
      if (selected.length < 2) return;
      bundleBusy = true;
      updateBundleSelectionPanel();
      try {
        const signature = bundleSelectionSignature(selected);
        const data = await api('/api/existing-summary-bundle/preview', {
          method: 'POST',
          body: JSON.stringify({
            selections: bundleSelectionsPayload(selected),
            title: bundleTitleInput ? bundleTitleInput.value : '',
          }),
        });
        lastBundlePreview = data;
        lastBundlePreviewSignature = signature;
        renderBundlePreview(data);
      } catch (err) {
        lastBundlePreview = null;
        lastBundlePreviewSignature = '';
        if (bundlePreviewPanelEl) {
          bundlePreviewPanelEl.hidden = false;
          bundlePreviewPanelEl.className = 'bundle-preview blocked';
          bundlePreviewPanelEl.innerHTML = `<strong>미리보기 실패</strong><div>${escapeHtml(err.message)}</div>`;
        }
      } finally {
        bundleBusy = false;
        updateBundleSelectionPanel();
      }
    }
    async function createExistingSummaryBundle() {
      const selected = selectedBundleReports();
      if (!lastBundlePreview || !lastBundlePreview.ok || lastBundlePreviewSignature !== bundleSelectionSignature(selected)) return;
      bundleBusy = true;
      updateBundleSelectionPanel();
      try {
        const data = await api('/api/existing-summary-bundle/create', {
          method: 'POST',
          body: JSON.stringify({
            selections: bundleSelectionsPayload(selected),
            title: bundleTitleInput ? bundleTitleInput.value : '',
          }),
        });
        if (!data.ok) {
          lastBundlePreview = data;
          renderBundlePreview(data);
        } else {
          selectedBundleBases.clear();
          lastBundlePreview = null;
          lastBundlePreviewSignature = '';
          if (bundlePreviewPanelEl) {
            bundlePreviewPanelEl.hidden = false;
            bundlePreviewPanelEl.className = 'bundle-preview ok';
            bundlePreviewPanelEl.innerHTML = `
              <strong>${escapeHtml(data.message || '기존 요약 번들을 만들었습니다.')}</strong>
              ${data.href ? `<div><a href="${escapeHtml(data.href)}">생성된 번들 열기</a></div>` : ''}
            `;
          }
          await loadReports();
        }
      } catch (err) {
        if (bundlePreviewPanelEl) {
          bundlePreviewPanelEl.hidden = false;
          bundlePreviewPanelEl.className = 'bundle-preview blocked';
          bundlePreviewPanelEl.innerHTML = `<strong>생성 실패</strong><div>${escapeHtml(err.message)}</div>`;
        }
      } finally {
        bundleBusy = false;
        updateBundleSelectionPanel();
        render();
      }
    }

    function renderStreamerBar() {
      const parts = [`<button class="chip${activeStreamer === '__all__' ? ' active' : ''}" data-key="__all__">전체<span class="count">${catalogStats.total}</span></button>`];
      streamerFacets.forEach((facet) => {
        const key = String(facet.key || '__unknown__');
        const name = facet.label || key;
        parts.push(`<button class="chip${activeStreamer === key ? ' active' : ''}" data-key="${escapeHtml(key)}">${escapeHtml(name)}<span class="count">${Number(facet.count || 0)}</span></button>`);
      });
      streamerBarEl.innerHTML = parts.join('');
      streamerBarEl.querySelectorAll('.chip').forEach((btn) => {
        btn.addEventListener('click', () => {
          activeStreamer = btn.dataset.key;
          loadReports().catch(showCatalogError);
        });
      });
    }

    function reviewHref(path, base) {
      const context = new URLSearchParams({q: (searchEl.value || '').trim(), streamer: activeStreamer});
      return `${path}?${window.ReportNavigation.list(new URLSearchParams({base}), context)}`;
    }

    function renderCard(report) {
      const anchor = document.createElement('a');
      anchor.className = 'card';
      const reviewPath = report.workspace_ready ? '/report-workspace' : '/report';
      anchor.href = reviewHref(reviewPath, report.base);
      if (report.kind === 'bundle') {
        anchor.innerHTML = `
          <div class="title">📦 ${escapeHtml(report.title)}</div>
          <div class="row">
            <span class="badge">번들 (${report.vod_count || 0}VOD)</span>
            ${report.publish_date ? `<span>${escapeHtml(report.publish_date)}</span>` : ''}
            ${report.site_only ? '<span class="badge" title="공개 사이트에만 존재">공개만</span>' : ''}
          </div>
          ${renderStatusBadges(report)}
          <span class="arrow" aria-hidden="true">→</span>
        `;
      } else {
        anchor.innerHTML = `
          <div class="title">${escapeHtml(report.title)}</div>
          <div class="row">
            <span>${escapeHtml(report.video_no)}</span>
            <span class="badge">${escapeHtml(streamerLabel(report))}</span>
            ${report.publish_date ? `<span>${escapeHtml(report.publish_date)}</span>` : ''}
            ${report.site_only ? '<span class="badge" title="자막 원본이 없고 공개 사이트에만 존재하는 리포트">공개만</span>' : ''}
            ${renderCanonicalBadges(report)}
            <span class="badge">최신 저장본${report.summary_version_count > 1 ? ` · 이전 버전 ${report.summary_version_count - 1}개` : ''}</span>
          </div>
          ${renderStatusBadges(report)}
          ${renderWorkEvidenceHint(report)}
          <span class="arrow" aria-hidden="true">→</span>
        `;
      }
      if (isExistingSummaryBundleEligible(report)) {
        const shell = document.createElement('article');
        shell.className = `card-shell${selectedBundleBases.has(report.base) ? ' selected' : ''}`;
        const selector = document.createElement('label');
        selector.className = 'bundle-select-check';
        selector.innerHTML = `
          <input type="checkbox" data-testid="existing-summary-bundle-checkbox" ${selectedBundleBases.has(report.base) ? 'checked' : ''}>
          기존 요약 묶기에 선택
        `;
        const input = selector.querySelector('input');
        input.addEventListener('change', () => {
          if (input.checked) selectedBundleBases.add(report.base);
          else selectedBundleBases.delete(report.base);
          invalidateBundlePreview();
          updateBundleSelectionPanel();
          render();
        });
        shell.appendChild(selector);
        shell.appendChild(anchor);
        if (report.summary_version_count > 1) {
          const versions = document.createElement('details');
          const time = value => value ? new Date(value).toLocaleString('ko-KR') : '시간 미확인';
          versions.innerHTML = `<summary>이전 요약 버전 ${report.summary_version_count - 1}개</summary>` + report.summary_versions.slice(1).map(version =>
            `<p><a href="${escapeHtml(reviewHref(version.workspace_ready ? '/report-workspace' : '/report', version.base))}">${escapeHtml(version.summary_label)} · ${escapeHtml(time(version.saved_at))}</a></p>`).join('');
          shell.appendChild(versions);
        }
        return shell;
      }
      return anchor;
    }

    function setText(id, v) {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    }

    function renderKpi() {
      setText('kpiTotal', Number(catalogStats.total || 0).toLocaleString());
      setText('kpiStreamers', Number(streamerFacets.length || 0).toLocaleString());
    }

    function render() {
      const keyword = (searchEl.value || '').trim().toLowerCase();
      const filtered = reports;
      const visible = reports.length;
      const total = Number(catalogStats.matchedTotal || 0);
      metaEl.textContent = keyword || activeStreamer !== '__all__'
        ? `${total}개 중 ${visible}개 표시`
        : `전체 ${Number(catalogStats.total || 0)}개 중 ${visible}개 표시`;
      renderKpi();
      renderStreamerBar();
      updateBundleSelectionPanel();
      listEl.innerHTML = '';
      if (!filtered.length) {
        listEl.setAttribute('aria-busy', 'false');
        loadMoreBtn.hidden = true;
        const activeStreamerName = activeStreamer === '__all__'
          ? null
          : (() => {
              const ref = reports.find(r => streamerKey(r) === activeStreamer);
              return ref ? streamerLabel(ref) : activeStreamer;
            })();
        const filterDesc = [];
        if (keyword) filterDesc.push(`검색어 "${escapeHtml(keyword)}"`);
        if (activeStreamerName) filterDesc.push(`스트리머 "${escapeHtml(activeStreamerName)}"`);
        const hint = filterDesc.length
          ? `현재 필터: ${filterDesc.join(' + ')}`
          : '아직 리포트가 한 건도 없습니다.';
        const showReset = filterDesc.length > 0;
        listEl.innerHTML = `
          <div class="empty">
            검색 결과가 없습니다.
            <span class="hint">${hint}</span>
            ${showReset ? '<button type="button" class="reset-btn" id="resetFiltersBtn">필터 초기화</button>' : ''}
          </div>`;
        const resetBtn = document.getElementById('resetFiltersBtn');
        if (resetBtn) {
          resetBtn.addEventListener('click', () => {
            searchEl.value = '';
            activeStreamer = '__all__';
            loadReports().catch(showCatalogError);
          });
        }
        return;
      }

      const grid = document.createElement('div');
      grid.className = 'grid';
      filtered.forEach((r) => grid.appendChild(renderCard(r)));
      listEl.appendChild(grid);
      listEl.classList.toggle('bundle-mode', bundleMode);
      listEl.setAttribute('aria-busy', 'false');
      loadMoreBtn.hidden = !hasMore;
    }

    function syncExplorerUrl() {
      const params = new URLSearchParams();
      const keyword = (searchEl.value || '').trim();
      if (keyword) params.set('q', keyword);
      if (activeStreamer && activeStreamer !== '__all__') params.set('streamer', activeStreamer);
      const nextUrl = `${window.location.pathname}${params.toString() ? `?${params}` : ''}`;
      window.history.replaceState({}, '', nextUrl);
    }

    function showCatalogError(err) {
      listEl.setAttribute('aria-busy', 'false');
      metaEl.textContent = '요약을 불러오지 못했습니다.';
      errorEl.textContent = err.message;
    }

    function invalidateCatalogRead() {
      catalogGeneration++;
      catalogController?.abort();
      catalogController = null;
      if (loadMoreBtn) {
        loadMoreBtn.disabled = false;
        loadMoreBtn.textContent = '요약 더 보기';
      }
    }

    async function loadReports({append = false} = {}) {
      window.clearTimeout(searchTimer);
      searchTimer = null;
      invalidateCatalogRead();
      const generation = catalogGeneration;
      const controller = new AbortController();
      catalogController = controller;
      const filters = new URLSearchParams();
      const keyword = (searchEl.value || '').trim();
      if (keyword) filters.set('q', keyword);
      if (activeStreamer && activeStreamer !== '__all__') filters.set('streamer', activeStreamer);
      const filterKey = filters.toString();
      append = append && filterKey === loadedCatalogFilter;
      errorEl.textContent = '';
      if (!append) {
        listEl.setAttribute('aria-busy', 'true');
        loadMoreBtn.hidden = true;
      } else {
        loadMoreBtn.disabled = true;
        loadMoreBtn.textContent = '불러오는 중...';
      }
      const params = new URLSearchParams({limit: String(PAGE_SIZE), offset: String(append ? nextOffset : 0)});
      for (const [key, value] of filters) params.set(key, value);
      try {
        const data = await api(`/api/report-catalog?${params}`, {signal: controller.signal});
        if (generation !== catalogGeneration) return;
        const pageRows = Array.isArray(data.reports) ? data.reports : [];
        reports = append ? reports.concat(pageRows) : pageRows;
        streamerFacets = Array.isArray(data.streamers) ? data.streamers : [];
        catalogStats = {
          total: Number(data.total || 0),
          matchedTotal: Number(data.matched_total || 0),
        };
        nextOffset = reports.length;
        hasMore = !!data.has_more;
        loadedCatalogFilter = filterKey;
        const availableBases = new Set(reports.map((report) => report.base));
        Array.from(selectedBundleBases).forEach((base) => {
          if (!availableBases.has(base)) selectedBundleBases.delete(base);
        });
        syncExplorerUrl();
        render();
      } catch (err) {
        if (generation === catalogGeneration && !controller.signal.aborted) showCatalogError(err);
      } finally {
        if (generation === catalogGeneration) {
          catalogController = null;
          loadMoreBtn.disabled = false;
          loadMoreBtn.textContent = '요약 더 보기';
        }
      }
    }

    searchEl.addEventListener('input', () => {
      window.clearTimeout(searchTimer);
      invalidateCatalogRead();
      listEl.setAttribute('aria-busy', 'true');
      loadMoreBtn.hidden = true;
      searchTimer = window.setTimeout(() => loadReports().catch(showCatalogError), 240);
    });
    if (bundlePreviewBtn) bundlePreviewBtn.addEventListener('click', () => previewExistingSummaryBundle());
    if (bundleCreateBtn) bundleCreateBtn.addEventListener('click', () => createExistingSummaryBundle());
    if (bundleClearBtn) {
      bundleClearBtn.addEventListener('click', () => {
        selectedBundleBases.clear();
        invalidateBundlePreview();
        updateBundleSelectionPanel();
        render();
      });
    }
    if (bundleTitleInput) {
      bundleTitleInput.addEventListener('input', () => {
        invalidateBundlePreview();
        updateBundleSelectionPanel();
      });
    }
    if (bundleModeBtn) {
      bundleModeBtn.addEventListener('click', () => {
        bundleMode = !bundleMode;
        bundleModeBtn.setAttribute('aria-pressed', String(bundleMode));
        bundleModeBtn.textContent = bundleMode ? '묶기 끝내기' : '여러 요약 묶기';
        if (bundlePanelEl) bundlePanelEl.hidden = !bundleMode;
        render();
      });
    }
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener('click', () => {
        loadReports({append: true}).catch(showCatalogError);
      });
    }
    document.getElementById('reloadBtn').addEventListener('click', () => {
      loadReports().catch(showCatalogError);
    });

    if (operationsDetails) {
      operationsDetails.addEventListener('toggle', () => {
        if (!operationsDetails.open || operationsLoaded) return;
        operationsLoaded = true;
        loadLlmCallHomeStatus();
        loadLiveCollectorHomeStatus();
        loadServerRoleHomeStatus();
        api('/api/runtime-data-guard')
          .then((data) => renderRuntimeDataGuard(data.runtime_data_guard || null))
          .catch(() => renderRuntimeDataGuard(null));
      });
    }

    loadReports().catch(showCatalogError);
