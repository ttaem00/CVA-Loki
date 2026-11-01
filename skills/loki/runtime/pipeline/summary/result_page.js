/* Shared result presentation. Existing report DOM and native editor own content. */
(function () {
  'use strict';
  const VERSION = 'cva-result-tabs.v2';
  const clock = (sec) => {
    const n = Math.max(0, Math.floor(Number(sec) || 0));
    return [Math.floor(n / 3600), Math.floor(n / 60) % 60, n % 60]
      .map((part) => String(part).padStart(2, '0')).join(':');
  };
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }
  function initResultPage() {
    const main = document.querySelector('.main.bleed-inner');
    if (!main || main.dataset.resultTemplate === VERSION) return;
    main.dataset.resultTemplate = VERSION;
    document.body.classList.add('cva-result-page');
    document.querySelectorAll('body > .hero').forEach((banner) => banner.remove());
    // Saved flat reports keep their authored nodes. Only the reading structure
    // changes; member VOD links and stored evidence remain their own authority.
    main.querySelectorAll('.timeline').forEach((timeline, timelineIndex) => {
      if (timeline.matches('.viewer-detail-hierarchy,[data-manager-outline-hierarchy]')
          || timeline.querySelector('.viewer-detail-hierarchy')) return;
      const items = Array.from(timeline.children).filter((node) => node.matches('.t-item'));
      if (!items.length) return;
      const groups = new Map();
      timeline.classList.add('viewer-detail-hierarchy');
      items.forEach((item) => {
        const link = item.querySelector('a[href*="chzzk.naver.com/video/"]');
        const videoNo = link && /\/video\/(\d+)/.exec(new URL(link.href).pathname)?.[1];
        const badge = item.querySelector('.vod-badge')?.textContent.trim() || '';
        const key = videoNo || badge || 'timeline';
        let group = groups.get(key);
        if (!group) {
          const flow = element('section', 'viewer-detail-flow');
          flow.id = `result-timeline-${timelineIndex + 1}-${groups.size + 1}`;
          const heading = element('h3', 'viewer-detail-flow-title');
          const member = Array.from(main.querySelectorAll('.ct-list .ct-item'))
            .find((row) => badge && row.querySelector('.ct-tc')?.textContent.trim() === badge);
          const memberTitle = member?.querySelector('.ct-title')?.textContent.trim() || '';
          const duration = /\s*\((\d+:\d{2}:\d{2})\)\s*$/.exec(memberTitle);
          const title = (duration ? memberTitle.slice(0, duration.index).trim() : memberTitle)
            || badge || document.querySelector('body > header h1')?.textContent.trim() || '방송 타임라인';
          heading.append(element('span', '', title));
          flow.append(heading);
          timeline.append(flow);
          group = { flow, heading, first: link, duration: duration?.[1] };
          groups.set(key, group);
        }
        const heading = item.querySelector(':scope > .t-head');
        item.classList.remove('t-item', ...Array.from(item.classList).filter((name) => name.startsWith('mood-')));
        item.classList.add('viewer-detail-subflow');
        item.dataset.resultScene = 'true';
        if (heading) {
          heading.classList.replace('t-head', 'viewer-detail-subflow-title');
          heading.querySelectorAll('.vod-badge,.mood').forEach((badge) => badge.remove());
          heading.querySelector('.t-title')?.classList.remove('t-title');
          if (link) {
            link.classList.replace('tc', 'viewer-detail-heading-time');
            heading.append(link);
          }
        }
        item.querySelectorAll(':scope > .t-body,:scope > .t-evidence').forEach((body) => {
          const paragraph = element('p');
          Array.from(body.attributes).forEach((attr) => paragraph.setAttribute(attr.name, attr.value));
          paragraph.classList.remove('t-body', 't-evidence');
          paragraph.append(...Array.from(body.childNodes));
          body.replaceWith(paragraph);
        });
        group.flow.append(item);
      });
      groups.forEach((group) => {
        if (!group.first) return;
        const time = group.first.cloneNode(true);
        time.removeAttribute('id');
        time.className = 'viewer-detail-heading-time';
        if (group.duration) {
          const url = new URL(time.href);
          url.searchParams.set('currentTime', '0');
          time.href = url.href;
          time.textContent = `00:00:00–${group.duration}`;
        }
        group.heading.append(time);
      });
    });
    main.querySelectorAll('.hl').forEach((card, index) => {
      if (card.matches('.highlight-card')) return;
      const title = card.querySelector('.hl-title');
      if (!title) return;
      card.classList.remove('hl');
      card.classList.add('highlight-card');
      if (!card.id) card.id = `result-highlight-${index + 1}`;
      const heading = element('div', 'highlight-card-header');
      const copy = element('h3');
      Array.from(title.attributes).forEach((attr) => copy.setAttribute(attr.name, attr.value));
      copy.classList.remove('hl-title');
      copy.append(...Array.from(title.childNodes));
      title.replaceWith(copy);
      const time = copy.querySelector('a[href*="chzzk.naver.com/video/"]');
      heading.append(copy);
      if (time) {
        const ranges = element('div', 'highlight-card-time-ranges');
        time.classList.add('highlight-card-time-range');
        const url = new URL(time.href);
        const start = url.searchParams.get('currentTime');
        if (start !== null && Number.isFinite(Number(start))) time.dataset.start = start;
        const source = time.cloneNode(true);
        source.removeAttribute('id');
        source.className = 'highlight-link';
        ranges.append(time);
        heading.append(ranges, source);
      }
      const reason = card.querySelector('.hl-reason');
      if (reason) {
        reason.classList.add('highlight-card-reason');
        heading.append(reason);
      }
      card.prepend(heading);
    });
    const original = Array.from(main.children);
    main.classList.add('cva-result-shell');
    const hero = document.querySelector('body > header:not(.site-header):not(.cva-site-header)');
    const footer = element('footer', 'cva-result-footer');
    const identity = document.getElementById('summaryPipelineIdentity');
    if (identity) footer.append(identity);
    if (hero) {
      hero.classList.add('cva-report-hero');
      const inner = hero.querySelector(':scope > .bleed-inner');
      if (inner) {
        const tools = inner.querySelector('.public-report-hero-tools');
        if (tools) footer.append(tools);
        const copy = element('div', 'cva-hero-copy');
        const title = inner.querySelector('h1');
        const meta = inner.querySelector('.meta');
        const crumb = inner.querySelector('.crumb');
        if (title) copy.append(title);
        if (meta) {
          if (crumb) {
            const originalCrumb = crumb.textContent;
            crumb.textContent = originalCrumb.split(' · VOD ')[0];
            crumb.title = originalCrumb;
            meta.prepend(crumb);
          }
          copy.append(meta);
        } else if (crumb) copy.append(crumb);
        const entry = inner.querySelector('[data-editor-entry]');
        if (entry) {
          entry.classList.add('cva-hero-actions');
          const primary = entry.querySelector('[data-editor-entry-primary]');
          if (primary) {
            primary.textContent = '↗ 원본 창 열기';
            primary.setAttribute('aria-label', (primary.getAttribute('aria-label') || '영상 연결').replace('영상 연결', '원본 창 열기'));
          }
          const guide = entry.querySelector('[data-editor-companion-guide]');
          if (guide) {
            const dialog = element('dialog', 'cva-connection-dialog');
            const heading = element('h2', '', '원본 영상을 함께 보세요');
            heading.id = 'cva-connection-help-title';
            dialog.setAttribute('aria-labelledby', heading.id);
            const close = element('button', 'cva-dialog-close', '닫기');
            close.type = 'button';
            const intro = element('p', '', '원본 창을 연 뒤 브라우저의 분할 보기로 리포트와 영상을 나란히 놓으세요. 장면이나 시간을 누르면 연결한 원본 영상이 해당 시각으로 이동합니다.');
            dialog.append(heading, close, intro, guide);
            // Guidance is on demand. Native connection/error status stays in
            // the hero so failures remain visible where the action happened.
            const secondary = entry.querySelector('[data-editor-companion-secondary]');
            if (secondary) dialog.append(secondary);
            const help = element('button', 'cva-text-button cva-connection-help', '연결 방법');
            help.type = 'button';
            let helpNavigation = false;
            help.addEventListener('click', () => { helpNavigation = false; dialog.showModal(); close.focus(); });
            close.addEventListener('click', () => dialog.close());
            dialog.addEventListener('click', (event) => {
              if (event.target.closest?.('a[href="#youtube-editor-tools"]')) {
                helpNavigation = true;
                dialog.close();
              }
            });
            dialog.addEventListener('close', () => {
              if (helpNavigation) byKey.editor.panel.focus({ preventScroll: true });
              else help.focus();
            });
            entry.append(help);
            document.body.append(dialog);
          }
        }
        inner.prepend(copy);
      }
      main.prepend(hero);
    }
    const tabs = element('nav', 'cva-result-tabs');
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', '방송 결과');
    const views = [
      ['summary', '요약·타임라인'], ['highlights', '하이라이트'], ['editor', '편집자 워크스페이스'],
    ].map(([key, label]) => {
      const button = element('button', 'cva-result-tab', label);
      button.type = 'button';
      button.id = `cva-result-tab-${key}`;
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-controls', `cva-result-panel-${key}`);
      const panel = element('section', 'cva-result-panel');
      panel.id = `cva-result-panel-${key}`;
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', button.id);
      panel.tabIndex = 0;
      tabs.append(button);
      return { key, button, panel };
    });
    function activate(key, focus = false) {
      views.forEach((view) => {
        const selected = view.key === key;
        view.button.setAttribute('aria-selected', String(selected));
        view.button.tabIndex = selected ? 0 : -1;
        view.panel.hidden = !selected;
        if (selected && focus) view.button.focus();
      });
      // Native auto-fit and lazy observers still own axis layout and rendering.
      if (key === 'editor') window.dispatchEvent(new Event('resize'));
    }
    views.forEach((view, index) => {
      view.button.addEventListener('click', () => activate(view.key));
      view.button.addEventListener('keydown', (event) => {
        let next = null;
        if (event.key === 'ArrowRight') next = (index + 1) % views.length;
        if (event.key === 'ArrowLeft') next = (index + views.length - 1) % views.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = views.length - 1;
        if (next == null) return;
        event.preventDefault();
        activate(views[next].key, true);
      });
    });
    const tabbar = element('div', 'cva-result-tabbar');
    const counts = element('p', 'cva-report-counts');
    tabbar.append(tabs, counts);
    main.append(tabbar, ...views.map((view) => view.panel), footer);
    const byKey = Object.fromEntries(views.map((view) => [view.key, view]));
    original.forEach((node) => {
      const editor = node.id === 'youtube-editor-tools' || node.querySelector('#youtube-editor-tools,.ct-list');
      const highlight = node.id === 'editorial-highlights' || node.querySelector('.highlight-card,.hl,.user-clips-grid')
        || node.matches('[data-report-block="viewer-clips"]');
      byKey[editor ? 'editor' : highlight ? 'highlights' : 'summary'].panel.append(node);
    });
    const timelineCard = main.querySelector('.timeline')?.closest('.card');
    timelineCard?.classList.add('cva-timeline-card');
    const timelineHeading = timelineCard?.querySelector('.card-head h2');
    if (timelineHeading) timelineHeading.textContent = '타임라인';
    // Reading starts with the broadcast flow; the chart remains available with
    // the native editor rather than preceding the result's primary content.
    const chart = main.querySelector('#chatChart')?.closest('.card');
    if (chart) byKey.editor.panel.append(chart);
    views.forEach(({ panel, key }) => {
      if (!panel.children.length) panel.append(element('p', 'cva-result-empty',
        key === 'editor' ? '이 방송에는 편집자 타임라인 자료가 없습니다.' : '이 방송에는 하이라이트 자료가 없습니다.'));
    });
    let broadcast = main.querySelector('.broadcast-summary');
    if (!broadcast) {
      const editorial = main.querySelector('.editorial,.editorial-body,.notes');
      if (editorial) {
        broadcast = editorial.closest('.card');
        if (!broadcast) {
          broadcast = element('div', 'card');
          const body = element('div', 'card-body');
          editorial.replaceWith(broadcast);
          body.append(editorial);
          broadcast.append(body);
        }
        broadcast.classList.add('broadcast-summary');
        broadcast.classList.remove('editorial');
        broadcast.querySelectorAll('.editorial,.editorial-body').forEach((body) => {
          body.classList.remove('editorial', 'editorial-body');
          body.classList.add('notes');
        });
        byKey.summary.panel.prepend(broadcast);
      }
    }
    if (!broadcast) {
      byKey.summary.panel.prepend(element('p', 'cva-summary-unavailable', '저장된 전체 방송 요약 본문이 없습니다. 아래 타임라인에서 장면별 요약을 읽을 수 있습니다.'));
    }
    if (broadcast && broadcast.tagName !== 'DETAILS') {
      const details = element('details', 'cva-broadcast-summary');
      details.open = true;
      Array.from(broadcast.attributes).forEach((attr) => details.setAttribute(attr.name, attr.value));
      details.classList.add('cva-broadcast-summary');
      const toggle = element('summary', 'cva-summary-toggle');
      toggle.append(element('h2', '', '방송 요약'));
      const duration = document.querySelector('.stats .stat:nth-child(2) .stat-value')?.textContent || '';
      const parts = duration.split(':').map(Number);
      const durationLabel = parts.length === 3 && parts.every(Number.isFinite)
        ? `${parts[0]}시간 ${parts[1]}분` : duration;
      if (durationLabel) toggle.append(element('span', 'cva-summary-duration', `${durationLabel} 방송`));
      const action = element('span', 'cva-summary-action');
      action.append(element('span', 'cva-summary-closed-label', '전체 요약 읽기'), element('span', 'cva-summary-open-label', '요약 접기'));
      toggle.append(action);
      details.append(toggle);
      const head = broadcast.querySelector('.card-head');
      head?.remove();
      details.append(...Array.from(broadcast.childNodes));
      broadcast.replaceWith(details);
    }

    let payload = document.getElementById('viewerEditorToolsData')?.chzzResultPayload;
    let selected = null;
    let pendingInspect = null;
    function periods() {
      return Array.isArray(payload?.collaboration_periods) ? payload.collaboration_periods : [];
    }
    function renderMembers(host, period, label) {
      host.replaceChildren();
      const head = element('div', 'cva-collaboration-head');
      head.append(element('span', 'cva-collaboration-label', label));
      if (!period) {
        head.append(element('span', 'cva-collaboration-note', '이 시점의 합방 기록 없음'));
        host.append(head);
        return;
      }
      const note = element('p', 'cva-collaboration-note',
        period.support_status === 'provisional' ? '미확인 기록　 ↗ 다시보기 처음부터 열기' : '↗ 다시보기 처음부터 열기');
      head.append(element('span', 'cva-period-time', `${clock(period.start_sec)}–${clock(period.end_sec)}`));
      const list = element('div', 'cva-member-list');
      (period.members || []).forEach((name) => {
        const links = (period.member_vods || []).filter((row) => row.member_name === name && /^[1-9][0-9]*$/.test(String(row.vod_no)));
        if (!links.length) {
          list.append(element('span', 'cva-member-chip', name));
          return;
        }
        let target = list;
        if (links.length > 1) {
          const picker = element('details', 'cva-member-vod-picker');
          const toggle = element('summary', 'cva-member-chip is-linked', `${name} · ${links.length}개 ⌄`);
          const options = element('div', 'cva-member-vod-options');
          picker.append(toggle, options);
          list.append(picker);
          target = options;
        }
        links.forEach((row) => {
          const chip = element('a', 'cva-member-chip is-linked', links.length > 1 ? `VOD ${row.vod_no} ↗` : `${name} ↗`);
          chip.href = `https://chzzk.naver.com/video/${row.vod_no}`;
          chip.target = '_blank';
          chip.rel = 'noopener noreferrer';
          chip.setAttribute('aria-label', `${name} 다시보기 ${row.vod_no}, 새 탭에서 처음부터 열기`);
          chip.title = `VOD ${row.vod_no} · 시점 동기화 없음`;
          target.append(chip);
        });
      });
      if (!list.children.length) list.append(element('span', 'cva-collaboration-note', '이 구간에 등록된 멤버 없음'));
      host.append(head, list, note);
    }
    function context(parent, start, end) {
      let host = parent.querySelector(':scope > .cva-collaboration-context');
      if (!host) {
        host = element('div', 'cva-collaboration-context');
        const title = parent.querySelector(':scope > .cva-flow-range,:scope > .viewer-detail-flow-title,.viewer-editor-head');
        if (title) title.insertAdjacentElement('afterend', host);
        else parent.prepend(host);
      }
      const matches = periods().filter((period) => period.start_sec < end && period.end_sec > start);
      if (!matches.length) { renderMembers(host, null, '합방 멤버'); return; }
      renderMembers(host, matches[0], '합방 멤버');
      if (matches.length > 1) {
        const select = element('select', 'cva-period-select');
        select.setAttribute('aria-label', '합방 기록 구간 선택');
        matches.forEach((period, index) => {
          const option = element('option', '', `${clock(period.start_sec)}–${clock(period.end_sec)}`);
          option.value = String(index);
          select.append(option);
        });
        const head = host.querySelector('.cva-collaboration-head');
        head.append(select);
        head.querySelector('.cva-period-time')?.remove();
        select.addEventListener('change', () => {
          const index = Number(select.value);
          renderMembers(host, matches[index], '합방 멤버');
          host.querySelector('.cva-collaboration-head').append(select);
          host.querySelector('.cva-period-time')?.remove();
        });
      }
    }
    const workspace = main.querySelector('#youtube-editor-tools');
    function workspaceMembers() {
      if (!workspace || !periods().length) return;
      let host = workspace.querySelector('.viewer-editor-head > .cva-collaboration-context');
      if (!host) {
        host = element('div', 'cva-collaboration-context');
        workspace.querySelector('.viewer-editor-head')?.append(host);
      }
      const baseVideo = String(payload?.video_no || '');
      const hasBaseSelection = selected && selected.videoNo === baseVideo && Number.isFinite(selected.sec);
      const period = hasBaseSelection ? periods().find((row) => row.start_sec <= selected.sec && selected.sec < row.end_sec) : null;
      renderMembers(host, period, hasBaseSelection ? `${clock(selected.sec)} 합방` : '선택한 시점의 합방');
      if (!hasBaseSelection) host.querySelector('.cva-collaboration-note').textContent =
        selected ? '선택한 영상의 합방 기록 없음' : '타임라인에서 시각을 선택하세요';
    }
    function enhanceHierarchy() {
      let changed = false;
      main.querySelectorAll('.viewer-detail-hierarchy').forEach((hierarchy) => {
        const flows = Array.from(hierarchy.querySelectorAll(':scope > .viewer-detail-flow'));
        if (!flows.length || hierarchy.dataset.resultLayout === VERSION) return;
        hierarchy.dataset.resultLayout = VERSION;
        changed = true;
        hierarchy.classList.add('cva-timeline-layout');
        hierarchy.querySelector(':scope > .timeline-navigator')?.remove();
        const nav = element('nav', 'cva-chapter-nav');
        nav.setAttribute('aria-label', '방송 목차');
        nav.append(element('h2', '', '방송 목차'));
        const mobile = element('select', 'cva-chapter-select');
        mobile.setAttribute('aria-label', '방송 흐름 선택');
        const controls = [];
        function choose(index) {
          flows.forEach((flow, i) => { flow.hidden = i !== index; });
          controls.forEach((button, i) => button.setAttribute('aria-current', i === index ? 'true' : 'false'));
          mobile.value = String(index);
        }
        flows.forEach((flow, index) => {
          const heading = flow.querySelector(':scope > .viewer-detail-flow-title');
          const title = heading?.querySelector('span')?.textContent || `방송 흐름 ${index + 1}`;
          const timeLink = heading?.querySelector('.viewer-detail-heading-time');
          const time = timeLink?.textContent || '';
          const button = element('button', 'cva-chapter-button');
          button.type = 'button';
          button.setAttribute('aria-controls', flow.id);
          const copy = element('span', 'cva-chapter-copy');
          copy.append(element('strong', 'cva-chapter-title', title), element('span', 'cva-chapter-time', time));
          button.append(element('span', 'cva-chapter-number', String(index + 1).padStart(2, '0')), copy);
          button.addEventListener('click', () => choose(index));
          controls.push(button);
          nav.append(button);
          const option = element('option', '', `${time} · ${title}`);
          option.value = String(index);
          mobile.append(option);
          flow.chzzResultShow = () => choose(index);
          if (heading) {
            const titleRow = element('div', 'cva-flow-heading');
            const titleCopy = element('div');
            titleCopy.append(element('p', 'cva-flow-eyebrow', `방송 흐름 ${String(index + 1).padStart(2, '0')}`), heading);
            titleRow.append(titleCopy);
            const range = element('p', 'cva-flow-range');
            if (timeLink) {
              range.append(timeLink);
              const source = timeLink.cloneNode(true);
              source.className = 'cva-flow-source';
              source.textContent = '↗';
              source.setAttribute('aria-label', `${title} 원본 보기`);
              titleRow.append(source);
            }
            flow.prepend(titleRow, range);
          }
          const row = (payload?.outline_projection || []).find((row) => `manager-outline-${String(row.id).replace(/[^a-zA-Z0-9_-]/g, '')}` === flow.id);
          if (row && periods().length) context(flow, Number(row.start_sec), Number(row.end_sec));
          const subflows = [];
          flow.querySelectorAll('.viewer-detail-subflow').forEach((subflow) => {
            if (subflow.tagName === 'DETAILS') return;
            const heading = subflow.querySelector(':scope > .viewer-detail-subflow-title');
            // A title/time-only interval is a valid source row, not a disclosure.
            // Count authored prose, media and controls too, not only Point rows.
            const hasBody = Array.from(subflow.childNodes).some((node) => {
              if (node === heading) return false;
              if (node.nodeType === Node.TEXT_NODE) return Boolean(node.textContent.trim());
              if (node.nodeType !== Node.ELEMENT_NODE || node.matches('script,style,template')) return false;
              return Boolean(node.textContent.trim()) || node.matches('img,video,audio,iframe,canvas,svg,input,button,a[href]')
                || Boolean(node.querySelector('img,video,audio,iframe,canvas,svg,input,button,a[href]'));
            });
            const details = element(hasBody ? 'details' : 'section');
            Array.from(subflow.attributes).forEach((attr) => details.setAttribute(attr.name, attr.value));
            details.removeAttribute('tabindex');
            const toggle = element(hasBody ? 'summary' : 'div', 'cva-subflow-toggle');
            if (!hasBody) toggle.classList.add('cva-subflow-static');
            if (heading) {
              const copy = element('span', 'cva-subflow-copy');
              const time = heading.querySelector('.viewer-detail-heading-time');
              let play = null;
              if (time) {
                time.classList.add('cva-subflow-range');
                play = time.cloneNode(true);
                play.className = 'cva-subflow-play';
                play.textContent = '▶';
                play.setAttribute('aria-label', `${heading.querySelector('span')?.textContent || '세부 흐름'} 원본 보기`);
              }
              copy.append(heading);
              if (time) copy.append(time);
              toggle.append(copy);
              if (play) toggle.append(play);
            } else toggle.textContent = '세부 흐름';
            const pointCount = subflow.querySelectorAll('.viewer-detail-scene-list > .t-item').length;
            if (pointCount) toggle.insertBefore(element('span', 'cva-point-count', `주요 장면 ${pointCount}`), toggle.querySelector('.cva-subflow-play'));
            details.prepend(toggle);
            details.append(...Array.from(subflow.childNodes));
            subflow.replaceWith(details);
            subflows.push(details);
          });
          let showAll = false;
          const more = element('button', 'cva-show-more');
          more.type = 'button';
          function visibleSubflows(all) {
            showAll = all;
            subflows.forEach((subflow, i) => { subflow.hidden = !all && i >= 3; });
            more.textContent = all ? '핵심 세부 흐름만 보기' : `나머지 세부 흐름 ${subflows.length - 3}개 보기`;
            more.setAttribute('aria-expanded', String(all));
            if (!all) more.append(element('span', 'cva-show-more-total', `전체 ${subflows.length}개`));
          }
          subflows.forEach((subflow) => { subflow.chzzResultReveal = () => visibleSubflows(true); });
          if (subflows.length > 3) {
            more.addEventListener('click', () => visibleSubflows(!showAll));
            flow.append(more);
          }
          visibleSubflows(false);
          const readingFooter = element('div', 'cva-reading-footer');
          readingFooter.append(element('span', '', `주요 장면 ${flow.querySelectorAll('.viewer-detail-scene-list > .t-item,[data-result-scene]').length}개`));
          if (workspace && row && Number.isFinite(Number(row.start_sec))) {
            const inspect = element('button', 'cva-text-button', '시간축에서 보기 →');
            inspect.type = 'button';
            inspect.addEventListener('click', () => inspectAt(Number(row.start_sec)));
            readingFooter.append(inspect);
          }
          flow.append(readingFooter);
        });
        const duration = Number(payload?.duration_sec || 0);
        const navSummary = element('p', 'cva-chapter-summary');
        navSummary.append(element('span', '', duration ? `◷ ${Math.floor(duration / 3600)}시간 ${Math.floor(duration % 3600 / 60)}분의 방송` : '전체 방송 흐름'), element('span', '', `${flows.length}개 흐름 · ${hierarchy.querySelectorAll('.viewer-detail-subflow').length}개 세부 흐름`));
        nav.append(navSummary);
        const highlights = main.querySelectorAll('.highlight-card').length;
        const points = hierarchy.querySelectorAll('.viewer-detail-scene-list > .t-item,[data-result-scene]').length;
        const clips = main.querySelectorAll('.user-clips-grid > .user-clip-card').length;
        const pointCount = element('span', '', '주요 장면 ');
        pointCount.append(element('strong', '', String(points)));
        const clipCount = element('span', '', '시청자 클립 ');
        clipCount.append(element('strong', '', String(clips)));
        counts.replaceChildren(pointCount, element('span', '', '·'), clipCount);
        const count = byKey.highlights.button.querySelector('.cva-tab-count') || element('span', 'cva-tab-count');
        count.textContent = clips ? ` ${highlights} · 클립 ${clips}` : ` ${highlights}`;
        byKey.highlights.button.append(count);
        mobile.addEventListener('change', () => choose(Number(mobile.value)));
        hierarchy.prepend(nav, mobile);
        choose(0);
      });
      return changed;
    }
    function inspectAt(sec, highlightFragment) {
      activate('editor');
      pendingInspect = { sec, highlightFragment };
      if (payload) {
        document.dispatchEvent(new CustomEvent('chzz-result-inspect', { detail: pendingInspect }));
        pendingInspect = null;
      }
      workspace?.scrollIntoView({ block: 'start', behavior: 'auto' });
      byKey.editor.panel.focus({ preventScroll: true });
    }
    const highlightRows = [];
    const highlightFragment = (id) => 'editorial-highlight-' + String(id)
      .replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
    function updateHighlightLabels() {
      const projected = (payload?.outline_projection || []).filter((row) => row.level === 'Highlight')
        .map((row) => ({ row, spans: (row.source_spans || []).filter((span) => Number(span?.end_sec) >= Number(span?.start_sec)) }))
        .filter(({ spans }) => spans.length)
        .sort((a, b) => Math.max(0, Number(a.spans[0].start_sec) || 0) - Math.max(0, Number(b.spans[0].start_sec) || 0));
      const labels = new Map(projected.map(({ row }, i) => [highlightFragment(row.highlight_id || row.id), `H${i + 1}`]));
      // Once rendered, the native group is authoritative for its visible label.
      workspace?.querySelectorAll('[data-highlight-id][data-highlight-group-label]').forEach((marker) => {
        labels.set(highlightFragment(marker.dataset.highlightId), marker.dataset.highlightGroupLabel);
      });
      highlightRows.forEach((row, index) => {
        const number = row.querySelector('.cva-highlight-number');
        const label = labels.get(row.id) || `H${index + 1}`;
        if (number.textContent !== label) number.textContent = label;
      });
    }
    main.querySelectorAll('.highlight-card').forEach((card, index) => {
      const sceneImage = card.querySelector('.editorial-scene-image');
      const heading = card.querySelector('.highlight-card-header');
      const link = card.querySelector('.highlight-card-time-range[data-start],.highlight-link[data-start]');
      const row = element('details');
      Array.from(card.attributes).forEach((attr) => row.setAttribute(attr.name, attr.value));
      row.classList.add('cva-highlight-row');
      row.open = index < 3;
      const summary = element('summary', 'cva-highlight-toggle');
      summary.append(element('span', 'cva-highlight-number', '—'));
      const copy = element('span', 'cva-highlight-copy');
      const title = heading?.querySelector('h3');
      if (title) copy.append(title);
      const timeRanges = card.querySelector('.highlight-card-time-ranges');
      if (timeRanges) copy.append(timeRanges);
      summary.append(copy);
      const tools = element('span', 'cva-highlight-tools');
      const source = card.querySelector('.highlight-link');
      if (source) {
        if (source.matches('a[href]:not([aria-disabled="true"]):not(.is-disabled)')) source.textContent = '▶ 원본 보기';
        tools.append(source);
      }
      summary.append(tools);
      summary.append(element('span', 'cva-highlight-caret', '⌄'));
      const detail = element('div', 'cva-highlight-detail');
      if (sceneImage) { detail.classList.add('has-scene-image'); detail.append(sceneImage); }
      const reason = heading?.querySelector('.highlight-card-reason');
      if (reason) detail.append(reason);
      const actions = card.querySelector('.highlight-card-actions');
      if (actions) detail.append(actions);
      const edit = card.querySelector('.highlight-card-details');
      if (edit) detail.append(edit);
      row.append(summary, detail);
      // Preserve any extra authored children rather than dropping new fields.
      if (heading) {
        detail.append(...Array.from(heading.childNodes));
        heading.remove();
      }
      detail.append(...Array.from(card.childNodes));
      card.replaceWith(row);
      highlightRows.push(row);
      if (workspace && link) {
        const inspect = element('button', 'cva-inspect-button cva-text-button', '시간축에서 보기 →');
        inspect.type = 'button';
        inspect.addEventListener('click', () => {
          inspectAt(Number(link.dataset.start), row.id);
        });
        tools.append(inspect);
      }
      if (actions && !actions.childNodes.length) actions.remove();
    });
    function progressiveList(items, limit, label) {
      if (items.length <= limit) return;
      let expanded = false;
      const more = element('button', 'cva-show-more');
      more.type = 'button';
      const render = () => {
        items.forEach((item, index) => { item.hidden = !expanded && index >= limit; });
        more.textContent = expanded ? `핵심 ${label}만 보기` : `나머지 ${label} ${items.length - limit}개 보기`;
        more.setAttribute('aria-expanded', String(expanded));
      };
      more.addEventListener('click', () => { expanded = !expanded; render(); });
      items.forEach((item) => { item.chzzResultReveal = () => { expanded = true; render(); }; });
      items.at(-1).parentElement.append(more);
      render();
    }
    progressiveList(highlightRows, 3, '하이라이트');
    const highlightHeading = highlightRows[0]?.closest('.card')?.querySelector('.card-head h2');
    if (highlightHeading) highlightHeading.textContent = `편집 하이라이트 ${highlightRows.length}`;
    else if (highlightRows.length) {
      const host = highlightRows[0].closest('.editorial-highlights');
      if (host && !host.querySelector(':scope > h2')) host.prepend(
        element('h2', 'cva-highlight-section-title', `편집 하이라이트 ${highlightRows.length}`));
    }
    updateHighlightLabels();
    main.querySelectorAll('.editorial-partial-status').forEach((status) => {
      const details = element('details', 'cva-result-status');
      details.append(element('summary', 'cva-result-status-toggle', '일부 결과 · 보완 내역 보기'));
      status.replaceWith(details);
      details.append(status);
    });
    const nativeAxis = workspace?.querySelector('#viewerEditorAxis');
    if (nativeAxis) new MutationObserver(updateHighlightLabels).observe(nativeAxis, { childList: true, subtree: true });
    main.querySelectorAll('.user-clips-grid').forEach((grid) => {
      grid.closest('.card')?.classList.add('cva-clips-card');
      const heading = grid.closest('.card')?.querySelector('.card-head h2');
      if (heading) heading.textContent = `시청자 클립 ${grid.querySelectorAll(':scope > .user-clip-card').length}`;
      const card = grid.closest('.card');
      const health = card?.querySelector('.user-clips-source-health');
      const description = card?.querySelector('.user-clips-summary');
      const warnings = health?.querySelectorAll('.user-clips-chip-warn');
      if (warnings?.length) {
        const notice = element('div', 'cva-clip-warnings');
        notice.append(...warnings);
        grid.before(notice);
      }
      if (health || description) {
        const info = element('details', 'cva-clip-info');
        info.append(element('summary', 'cva-text-button', '클립 정보'));
        const body = element('div', 'cva-clip-info-body');
        if (description) body.append(description);
        if (health) body.append(health);
        info.append(body);
        const head = card.querySelector('.card-head');
        if (head) head.append(info);
        else grid.before(info);
      }
      progressiveList(Array.from(grid.querySelectorAll(':scope > .user-clip-card')), 3, '클립');
    });
    byKey.highlights.panel.querySelectorAll('.standalone-points').forEach((points) => {
      const disclosure = element('details', 'cva-all-points');
      const summary = element('summary', 'cva-all-points-toggle', `주요 장면 전체 ${points.querySelectorAll('.standalone-point-card').length}개`);
      const heading = points.querySelector(':scope > h3');
      if (heading) heading.hidden = true;
      disclosure.append(summary, points);
      byKey.highlights.panel.append(disclosure);
    });
    function showTarget(target) {
      const view = views.find(({ panel }) => panel.contains(target));
      if (!view) return;
      activate(view.key);
      target.closest('.viewer-detail-flow')?.chzzResultShow?.();
      target.closest('.viewer-detail-subflow')?.chzzResultReveal?.();
      target.closest('.cva-highlight-row')?.chzzResultReveal?.();
      if (target.tagName === 'DETAILS') target.open = true;
      for (let parent = target.parentElement; parent && parent !== main; parent = parent.parentElement) {
        if (parent.tagName === 'DETAILS') parent.open = true;
      }
    }
    function hashTarget(scroll = false) {
      let id;
      try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { return; }
      const target = id && document.getElementById(id);
      if (target) {
        showTarget(target);
        if (scroll === true) target.scrollIntoView({ block: 'start', behavior: 'auto' });
      }
    }
    document.addEventListener('click', (event) => {
      const anchor = event.target.closest?.('a[href]');
      if (!anchor || event.button > 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const url = new URL(anchor.href, location.href);
      if (url.origin === location.origin && url.pathname === location.pathname && url.hash) {
        const target = document.getElementById(decodeURIComponent(url.hash.slice(1)));
        if (target) showTarget(target);
      }
      const match = /^\/video\/(\d+)\/?$/.exec(url.pathname);
      const sec = url.searchParams.get('currentTime');
      if (url.hostname === 'chzzk.naver.com' && match && sec != null && String(payload?.video_no) === match[1]) {
        selected = { videoNo: match[1], sec: Number(sec) };
        workspaceMembers();
      }
    }, true);
    document.addEventListener('chzz-result-data', (event) => {
      payload = event.detail;
      updateHighlightLabels();
      if (periods().length) {
        // Raw roster history remains in source HTML for non-JS readers.
        main.querySelectorAll('.collaboration-periods').forEach((node) => { node.hidden = true; });
      }
      enhanceHierarchy();
      workspaceMembers();
      if (pendingInspect) queueMicrotask(() => {
        document.dispatchEvent(new CustomEvent('chzz-result-inspect', { detail: pendingInspect }));
        pendingInspect = null;
      });
    });
    document.addEventListener('chzz-result-selection', (event) => {
      selected = event.detail;
      workspaceMembers();
    });
    window.addEventListener('hashchange', hashTarget);
    let scheduled = false;
    const observer = new MutationObserver(() => {
      if (scheduled) return;
      scheduled = true;
      queueMicrotask(() => {
        scheduled = false;
        if (enhanceHierarchy()) hashTarget(true);
      });
    });
    observer.observe(byKey.summary.panel, { childList: true, subtree: true });
    activate('summary');
    if (periods().length) main.querySelectorAll('.collaboration-periods').forEach((node) => { node.hidden = true; });
    enhanceHierarchy();
    workspaceMembers();
    hashTarget();
    document.dispatchEvent(new Event('chzz-result-data-request'));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initResultPage);
  else initResultPage();
})();
