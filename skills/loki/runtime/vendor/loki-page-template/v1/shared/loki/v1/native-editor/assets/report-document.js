const reportDocumentData = JSON.parse(document.getElementById('reportDocumentData')?.textContent || '{}');
const labels = reportDocumentData.labels || [];
const counts = reportDocumentData.counts || [];
const bucketSecs = reportDocumentData.bucketSecs || labels.map(label => String(label).split(':').reduce((seconds, part) => seconds * 60 + Number(part), 0));
const peakSecs = reportDocumentData.peakSecs || [];
const audioPeakSecs = reportDocumentData.audioPeakSecs || [];
const vodVideoNo = reportDocumentData.vodVideoNo || '';
const windowSec = reportDocumentData.windowSec;
// chat-chart-bucket-mapping:start
const peakBucketIndex = (seconds) => Number.isFinite(seconds)
  ? bucketSecs.findIndex((start, index) => seconds >= start
    && (index + 1 === bucketSecs.length || seconds < bucketSecs[index + 1]))
  : -1;
const peakSet = new Set(peakSecs.map(peakBucketIndex).filter(index => index >= 0));
const audioPeakSet = new Set(audioPeakSecs.map(peakBucketIndex).filter(index => index >= 0));
const bgColors = labels.map((_, i) =>
  audioPeakSet.has(i) ? 'rgba(224,175,104,0.85)' :
  peakSet.has(i) ? 'rgba(247,118,142,0.75)' : 'rgba(122,162,247,0.35)'
);
const audioPeakMarkers = labels.map((_, i) =>
  audioPeakSet.has(i) ? Math.max(counts[i] || 0, 1) : null
);
// chat-chart-bucket-mapping:end
if (labels.length > 0) {
  new Chart(document.getElementById('chatChart'), {
    type: 'bar',
    data: { labels, datasets: [
      { label: '채팅 밀도', data: counts, backgroundColor: bgColors, borderWidth: 0, borderRadius: 2 },
      {
        type: 'line',
        label: '오디오 피크',
        data: audioPeakMarkers,
        showLine: false,
        pointStyle: 'triangle',
        pointRadius: 6,
        pointHoverRadius: 8,
        borderColor: 'rgba(224,175,104,1)',
        backgroundColor: 'rgba(224,175,104,1)'
      }
    ] },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: audioPeakSecs.length > 0, labels: { color: '#a9b1d6' } }, tooltip: {
        backgroundColor: '#24283b', borderColor: '#363b54', borderWidth: 1,
        titleColor: '#c0caf5', bodyColor: '#c0caf5'
      }},
      onClick: (_event, elements) => {
        if (!elements.length) return;
        const sec = bucketSecs[elements[0].index];
        if (typeof sec !== 'number') return;
        const report = document.getElementById('viewerEditorToolsData')?.chzzResultPayload;
        const source = report?.source_vods?.find((row) =>
          sec >= Number(row.offset_sec) && sec < Number(row.offset_sec) + Number(row.duration_sec));
        const videoNo = source ? String(source.video_no || '') : vodVideoNo;
        if (!videoNo) return;
        const seekSec = source ? sec - Number(source.offset_sec) : sec;
        const url = typeof sourceVideoUrlAt === 'function'
          ? sourceVideoUrlAt(videoNo, Math.max(0, seekSec), source?.source_platform || report?.source_platform || '')
          : (/^\d{1,16}$/.test(videoNo) ? `https://chzzk.naver.com/video/${videoNo}?currentTime=${Math.max(0, Math.floor(seekSec))}` : '');
        if (!url) return;
        if (typeof navigateChzzkCompanion === 'function') {
          navigateChzzkCompanion(url);
        } else {
          window.open(url, '_blank', 'noopener');
        }
      },
      onHover: (event, elements) => {
        if (event.native && event.native.target) {
          event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
        }
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: '#565c7e', font: { size: 10 }, maxTicksLimit: 12 } },
        y: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: '#565c7e', font: { size: 11 } } }
      }
    }
  });
}

// 타임라인 아이템 클릭 시 근거 토글
document.querySelectorAll('.t-item').forEach(item => {
  item.addEventListener('click', (e) => {
    if (e.target.closest('a, button')) return;
    const open = item.getAttribute('data-open') === '1';
    item.setAttribute('data-open', open ? '0' : '1');
  });
});
function toggleAll(btn) {
  const items = document.querySelectorAll('.t-item');
  const anyClosed = Array.from(items).some(i => i.getAttribute('data-open') !== '1');
  items.forEach(i => i.setAttribute('data-open', anyClosed ? '1' : '0'));
  btn.textContent = anyClosed ? '근거 모두 접기' : '근거 모두 펼치기';
}

async function copyNaverCafeHtml(btn) {
  const template = document.getElementById('naverCafeTemplate');
  const status = document.getElementById('naverCafeCopyStatus');
  const sandbox = document.getElementById('naverCafeClipboard');
  if (!template || !status || !sandbox) return;

  const html = template.innerHTML.trim();
  const text = (template.content.textContent || '')
    .replace(/\u00a0/g, ' ')
    .replace(/\n\s*\n\s*\n+/g, '\n\n')
    .trim();

  try {
    if (navigator.clipboard && window.ClipboardItem) {
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([text], { type: 'text/plain' })
        })
      ]);
    } else {
      sandbox.innerHTML = html;
      const range = document.createRange();
      range.selectNodeContents(sandbox);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      const ok = document.execCommand('copy');
      selection.removeAllRanges();
      sandbox.innerHTML = '';
      if (!ok) throw new Error('execCommand copy failed');
    }
    status.textContent = '복사됨. 네이버 카페 글쓰기 창에 바로 붙여넣으세요.';
    btn.blur();
  } catch (err) {
    status.textContent = '브라우저가 HTML 복사를 막았습니다. 미리보기 내용을 직접 복사해 주세요.';
  }
}

async function copyChzzkCommentText(btn) {
  const pre = document.getElementById('chzzkCommentPreview');
  const status = document.getElementById('chzzkCommentCopyStatus');
  if (!pre || !status) return;
  const text = pre.textContent || '';
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const range = document.createRange();
      range.selectNodeContents(pre);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      const ok = document.execCommand('copy');
      selection.removeAllRanges();
      if (!ok) throw new Error('execCommand copy failed');
    }
    status.textContent = '복사됨. 치지직 다시보기 댓글창에 바로 붙여넣으세요.';
    btn.blur();
  } catch (err) {
    status.textContent = '브라우저가 복사를 막았습니다. 미리보기 내용을 직접 복사해 주세요.';
  }
  }
