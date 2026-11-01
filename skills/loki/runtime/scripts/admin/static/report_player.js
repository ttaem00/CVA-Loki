(() => {
  'use strict';

  class ReportPlayer {
    constructor(video, onState) {
      this.video = video;
      this.hls = null;
      this.source = null;
      this.pendingSeek = null;
      this.mediaListeners = [];
      this.failed = false;
      this.onState = onState || (() => {});
    }

    async load(source, resume = null) {
      const pendingSeek = resume || this.pendingSeek;
      this.destroy();
      this.source = source;
      this.pendingSeek = pendingSeek;
      this.failed = false;
      const listen = (event, callback) => {
        this.video.addEventListener(event, callback);
        this.mediaListeners.push([event, callback]);
      };
      listen('loadedmetadata', () => this.applyPendingSeek());
      listen('error', () => {
        this.failed = true;
        this.onState('error', '영상 연결 실패 · 다시 연결하거나 원본으로 확인하세요.');
      });
      for (const event of ['waiting', 'seeking']) {
        listen(event, () => {
          if (!this.failed) this.onState('loading', '장면을 불러오는 중');
        });
      }
      for (const event of ['canplay', 'seeked']) {
        listen(event, () => {
          if (!this.failed && this.video.readyState >= 3 && !this.video.seeking) {
            this.onState('ready', '재생 준비됨');
          }
        });
      }
      this.onState('loading', '영상 연결 중');
      if (/^\/api\/report-media\/[a-f0-9]{20}\.mp4$/.test(source)) {
        this.video.src = source;
      } else if (window.Hls && window.Hls.isSupported()) {
        const hls = this.hls = new window.Hls({enableWorker: true, backBufferLength: 90});
        hls.on(window.Hls.Events.ERROR, (_event, data) => {
          if (this.hls !== hls) return;
          if (data && data.fatal) {
            this.failed = true;
            this.onState('error', '영상 연결 실패 · 다시 연결하거나 원본으로 확인하세요.');
          }
        });
        hls.loadSource(source);
        hls.attachMedia(this.video);
      } else if (this.video.canPlayType('application/vnd.apple.mpegurl')) {
        this.video.src = source;
      } else {
        throw new Error('이 브라우저는 HLS 재생을 지원하지 않습니다.');
      }
    }

    async reconnect() {
      if (!this.source) return;
      const resume = this.pendingSeek || {
        seconds: Math.max(0, Number(this.video.currentTime) || 0),
        play: !this.video.paused,
      };
      await this.load(this.source, resume);
    }

    applyPendingSeek() {
      if (!this.pendingSeek || this.video.readyState < 1) return;
      const pending = this.pendingSeek;
      this.pendingSeek = null;
      this.video.currentTime = pending.seconds;
      if (pending.play) this.video.play().catch(() => {});
    }

    seek(seconds) {
      if (!Number.isFinite(seconds)) return;
      this.pendingSeek = {seconds: Math.max(0, seconds), play: true};
      this.onState('loading', '장면을 불러오는 중');
      this.applyPendingSeek();
      if (!this.failed && this.video.readyState >= 3 && !this.video.seeking) {
        this.onState('ready', '재생 준비됨');
      }
    }

    async pictureInPicture() {
      if (!document.pictureInPictureEnabled || typeof this.video.requestPictureInPicture !== 'function') {
        throw new Error('이 브라우저에서는 작게 띄우기를 지원하지 않습니다.');
      }
      if (document.pictureInPictureElement) await document.exitPictureInPicture();
      else await this.video.requestPictureInPicture();
    }

    destroy() {
      this.failed = true;
      for (const [event, callback] of this.mediaListeners) this.video.removeEventListener(event, callback);
      this.mediaListeners = [];
      this.pendingSeek = null;
      this.source = null;
      const hls = this.hls;
      this.hls = null;
      if (hls) hls.destroy();
      this.video.removeAttribute('src');
      this.video.load();
    }
  }

  window.ReportPlayer = ReportPlayer;
})();
