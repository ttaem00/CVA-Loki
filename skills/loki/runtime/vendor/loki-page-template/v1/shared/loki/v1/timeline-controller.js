/* Viewport controls only; scene selection stays with the parent page. */
(() => {
  'use strict';
  const MAX_ZOOM = 256;
  window.createLokiTimelineController = options => {
    const { scroller, canvas, ruler, seekLink, seekHint, zoomIn, zoomOut, zoomReset } = options;
    const duration = Number(options.duration);
    const maxZoom = Number.isFinite(options.maxZoom) && options.maxZoom >= 1 ? Math.min(options.maxZoom, MAX_ZOOM) : MAX_ZOOM;
    if (!scroller || !canvas || !ruler || !seekLink || !(duration > 0) || !Number.isFinite(duration)) {
      throw new TypeError('Timeline requires its viewport, canvas, ruler, link and a positive duration.');
    }
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
    const clock = options.formatTime;
    const currentTime = () => {
      const value = Number(options.getCurrentTime?.());
      return Number.isFinite(value) ? clamp(value, 0, duration) : 0;
    };
    let zoom = 1;
    let geometry = null;
    let updating = false;
    let frame = 0;
    let frameNotice = false;
    let lastNotice = '';
    let hovered = false;
    let focused = false;
    let pointerX = null;
    let seekTime = currentTime();

    const measure = () => {
      const viewport = scroller.getBoundingClientRect();
      const track = ruler.getBoundingClientRect();
      return {
        viewport, track, viewportWidth: scroller.clientWidth,
        trackOffset: track.left - viewport.left + scroller.scrollLeft
      };
    };
    const setScroll = left => {
      scroller.scrollLeft = clamp(left, 0, Math.max(0, scroller.scrollWidth - scroller.clientWidth));
    };
    const showHint = () => seekHint?.classList.toggle('is-visible', hovered || focused);
    const setSeek = value => {
      seekTime = clamp(value, 0, duration);
      const label = clock(seekTime);
      const href = options.vodURL(seekTime);
      if(href){seekLink.href=href;seekLink.removeAttribute('aria-disabled');}
      else{seekLink.removeAttribute('href');seekLink.setAttribute('aria-disabled','true');}
      seekLink.setAttribute('aria-label', href ? `${label}부터 원본 영상 보기` : `${label} · 원본 링크 없음`);
      if (seekHint) {
        const track = ruler.getBoundingClientRect();
        const viewport = scroller.getBoundingClientRect();
        const left = Math.max(0, viewport.left - track.left);
        const right = Math.max(left, Math.min(track.width, viewport.left + scroller.clientWidth - track.left));
        const margin = Math.min(60, (right - left) / 2);
        seekHint.textContent = `${label}${href?' 원본 보기':''}`;
        seekHint.style.left = `${clamp(seekTime / duration * track.width, left + margin, right - margin)}px`;
      }
      showHint();
    };
    const seekAt = clientX => {
      const rect = ruler.getBoundingClientRect();
      if (rect.width > 0 && Number.isFinite(clientX)) setSeek((clientX - rect.left) / rect.width * duration);
    };
    const updateControls = () => {
      canvas.dataset.zoom = String(zoom);
      if (zoomOut) zoomOut.disabled = zoom <= 1;
      if (zoomIn) zoomIn.disabled = zoom >= maxZoom;
      if (zoomReset) {
        zoomReset.textContent = '맞춤';
        zoomReset.title = `방송 전체 너비에 맞추기 · 현재 ${zoom.toFixed(2)}배`;
        zoomReset.setAttribute('aria-label', `방송 전체 너비에 맞추기, 현재 ${zoom.toFixed(2)}배`);
      }
    };
    const notify = (force = false) => {
      const key = `${zoom}|${scroller.clientWidth}|${canvas.style.width}`;
      if (!force && key === lastNotice) return;
      lastNotice = key;
      options.onViewportChange?.();
    };
    const scheduleNotice = (withNotice = false) => {
      if (withNotice === true) frameNotice = true;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const shouldNotify = frameNotice;
        frameNotice = false;
        if (updating) return;
        updating = true;
        try {
          geometry = measure();
          if (hovered && pointerX !== null) seekAt(pointerX);
          else setSeek(seekTime);
          if (shouldNotify) notify();
        } finally { updating = false; }
      });
    };
    const apply = (nextZoom, anchorRatio, anchorX, resetScroll = false, deferNotice = false, visibleRange = null) => {
      if (updating) return zoom;
      updating = true;
      try {
        zoom = clamp(nextZoom, 1, maxZoom);
        canvas.style.width = `${Math.max(320, scroller.clientWidth) * zoom}px`;
        const rect = ruler.getBoundingClientRect();
        if (resetScroll) setScroll(0);
        else if (rect.width > 0 && Number.isFinite(anchorRatio) && Number.isFinite(anchorX)) {
          setScroll(scroller.scrollLeft + rect.left + anchorRatio * rect.width - anchorX);
        }
        if (visibleRange) {
          const visible = measure();
          const leftEdge = visible.viewport.left + Math.max(0, visible.trackOffset) + visibleRange.padding;
          const rightEdge = visible.viewport.left + visible.viewportWidth - visibleRange.padding;
          const startX = visible.track.left + visibleRange.start / duration * visible.track.width;
          const endX = visible.track.left + visibleRange.end / duration * visible.track.width;
          // Preserve the click anchor unless a small pan can expose every member.
          if (endX - startX <= rightEdge - leftEdge) {
            if (startX < leftEdge) setScroll(scroller.scrollLeft + startX - leftEdge);
            else if (endX > rightEdge) setScroll(scroller.scrollLeft + endX - rightEdge);
          }
        }
        geometry = measure();
        updateControls();
        if (hovered && pointerX !== null) seekAt(pointerX);
        else setSeek(seekTime);
        if (deferNotice) scheduleNotice(true);
        else notify(resetScroll);
      } finally { updating = false; }
      return zoom;
    };
    const zoomBy = (factor, clientX) => {
      if (updating || !Number.isFinite(factor) || factor <= 0) return zoom;
      const nextZoom = clamp(zoom * factor, 1, maxZoom);
      if (nextZoom === zoom) return zoom;
      const before = measure();
      const x = Number.isFinite(clientX) ? clientX : before.viewport.left + before.viewportWidth / 2;
      const ratio = before.track.width > 0 ? clamp((x - before.track.left) / before.track.width, 0, 1) : currentTime() / duration;
      return apply(nextZoom, ratio, x);
    };
    const revealCluster = ({ minimumTrackWidth, start, end, anchors, anchorTime, clientX, padding = 24 }) => {
      const previousZoom = zoom;
      const unchanged = () => ({ zoom, changed: false, separated: false });
      if (updating || !Number.isFinite(minimumTrackWidth) || minimumTrackWidth <= 0 ||
          !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end > duration || end < start) return unchanged();
      const before = measure();
      if (before.viewportWidth <= 0 || before.track.width <= 0) return unchanged();
      const baseWidth = Math.max(320, before.viewportWidth);
      const nextZoom = clamp(Math.max(zoom, zoom + (minimumTrackWidth - before.track.width) / baseWidth), 1, maxZoom);
      const trackWidth = before.track.width + (nextZoom - zoom) * baseWidth;
      const inset = Math.max(0, before.trackOffset);
      const margin = Math.min(Math.max(0, padding), Math.max(0, (before.viewportWidth - inset) / 3));
      const availableWidth = before.viewportWidth - inset - margin * 2;
      let anchor = Number.isFinite(anchorTime) ? clamp(anchorTime, start, end) : (start + end) / 2;
      let visibleStart = start, visibleEnd = end;
      const sourceAnchors = (anchors || []).filter(value => Number.isFinite(value) && value >= start && value <= end);
      // A widely spread group may not fit at the separating scale. Keep an actual
      // member and its nearest neighbour in view when that pair fits.
      if ((end - start) / duration * trackWidth > availableWidth && sourceAnchors.length) {
        anchor = sourceAnchors.reduce((nearest, value) => Math.abs(value - anchor) < Math.abs(nearest - anchor) ? value : nearest);
        const others = sourceAnchors.filter(value => value !== anchor);
        if (others.length) {
          const neighbour = others.reduce((nearest, value) => Math.abs(value - anchor) < Math.abs(nearest - anchor) ? value : nearest);
          if (Math.abs(neighbour - anchor) / duration * trackWidth <= availableWidth) {
            visibleStart = Math.min(anchor, neighbour);
            visibleEnd = Math.max(anchor, neighbour);
          }
        }
      }
      const leftEdge = before.viewport.left + inset + margin;
      const rightEdge = before.viewport.left + before.viewportWidth - margin;
      const x = clamp(Number.isFinite(clientX) ? clientX : (leftEdge + rightEdge) / 2, leftEdge, Math.max(leftEdge, rightEdge));
      apply(nextZoom, anchor / duration, x, false, false, { start: visibleStart, end: visibleEnd, padding: margin });
      return { zoom, changed: zoom !== previousZoom, separated: ruler.getBoundingClientRect().width + 0.5 >= minimumTrackWidth };
    };
    const reset = () => apply(1, 0, 0, true);
    const refreshWidth = () => {
      if (updating) return zoom;
      const now = measure();
      if (now.viewportWidth <= 0) return zoom;
      const width = Math.max(320, now.viewportWidth) * zoom;
      if (geometry && geometry.viewportWidth === now.viewportWidth && canvas.style.width === `${width}px`) return zoom;
      const previous = geometry || now;
      const ratio = previous.track.width > 0
        ? clamp((scroller.scrollLeft + previous.viewportWidth / 2 - previous.trackOffset) / previous.track.width, 0, 1)
        : currentTime() / duration;
      return apply(zoom, ratio, now.viewport.left + now.viewportWidth / 2);
    };

    scroller.addEventListener('wheel', event => {
      if (event.defaultPrevented) return;
      if (event.ctrlKey || event.altKey) {
        event.preventDefault();
        zoomBy(Math.exp(clamp(-event.deltaY * 0.0025, -20, 20)), event.clientX);
      } else if (event.shiftKey && event.deltaY && !event.deltaX) {
        event.preventDefault();
        setScroll(scroller.scrollLeft + event.deltaY);
        scheduleNotice();
      }
    }, { passive: false });
    scroller.addEventListener('scroll', scheduleNotice, { passive: true });
    zoomIn?.addEventListener('click', () => zoomBy(1.5));
    zoomOut?.addEventListener('click', () => zoomBy(1 / 1.5));
    zoomReset?.addEventListener('click', reset);

    const point = event => {
      pointerX = event.clientX;
      hovered = true;
      seekAt(pointerX);
    };
    ruler.addEventListener('pointerenter', point);
    ruler.addEventListener('pointermove', point);
    ruler.addEventListener('pointerdown', point);
    ruler.addEventListener('pointerleave', () => { hovered = false; pointerX = null; showHint(); });
    ruler.addEventListener('click', event => {
      if (event.detail > 0) seekAt(event.clientX);
    });
    seekLink.addEventListener('focus', () => { focused = true; setSeek(currentTime()); });
    seekLink.addEventListener('blur', () => { focused = false; showHint(); });
    seekLink.addEventListener('keydown', event => {
      if (event.ctrlKey || event.altKey || event.metaKey) return;
      const values = { ArrowLeft: seekTime - 10, ArrowRight: seekTime + 10, Home: 0, End: duration };
      if (!Object.hasOwn(values, event.key)) return;
      event.preventDefault();
      setSeek(values[event.key]);
    });

    apply(1, 0, 0, true, true);
    // Scrollbars and layout changes can resize the map without a window resize.
    if (typeof ResizeObserver === 'function') {
      const viewportObserver = new ResizeObserver(() => refreshWidth());
      viewportObserver.observe(scroller);
    }
    return Object.freeze({ refreshWidth, reset, zoomBy, revealCluster, getZoom: () => zoom, getMaxZoom: () => maxZoom });
  };
})();
