    function inspectTypedTimecode() {
      const sec = parseTimeInput(cockpitTimecodeEl && cockpitTimecodeEl.value);
      if (!Number.isFinite(sec)) {
        setStatus('타임코드를 1:23 또는 00:01:23 형식으로 입력해 주세요.', 'err');
        return;
      }
      selectCockpitTime(sec);
      setStatus(`${fmtSeconds(sec)} 주변 근거를 열었습니다.`, 'ok');
    }

    async function draftTimelineCandidate() {
      const sec = Number.isFinite(selectedCockpitSec)
        ? selectedCockpitSec
        : parseTimeInput(cockpitTimecodeEl && cockpitTimecodeEl.value);
      if (!Number.isFinite(sec)) {
        setStatus('먼저 시간축을 클릭하거나 타임코드를 입력해 주세요.', 'err');
        return;
      }
      selectCockpitTime(sec);
      timelineCandidateMarkdown = '';
      openCockpitSection('summaryCandidateSection');
      updateCockpitSectionStates();
      if (insertTimelineCandidateBtn) insertTimelineCandidateBtn.disabled = true;
      if (timelineCandidatePreviewEl) timelineCandidatePreviewEl.textContent = '후보 생성 중...';
      setStatus('타임라인 후보 1개 생성 중...', 'info');
      const data = await api('/api/timeline-candidate', {
        method: 'POST',
        body: JSON.stringify({
          base,
          md: mdEditor.value,
          timecode: fmtSeconds(sec),
          manager_context: timelineSceneContextInputEl ? timelineSceneContextInputEl.value : '',
        }),
      });
      timelineCandidateMarkdown = String(data.candidate_markdown || '').trim();
      if (!timelineCandidateMarkdown) throw new Error('빈 후보가 반환되었습니다.');
      if (timelineCandidatePreviewEl) timelineCandidatePreviewEl.textContent = timelineCandidateMarkdown;
      if (insertTimelineCandidateBtn) insertTimelineCandidateBtn.disabled = false;
      openCockpitSection('summaryCandidateSection');
      updateCockpitSectionStates();
      setStatus('후보를 만들었습니다. 확인 후 [본문에 넣기]를 누르세요.', 'ok');
    }

    function insertTimelineCandidateIntoMd(md, candidate) {
      const item = String(candidate || '').trim();
      if (!item) return md;
      const normalized = item.endsWith('\n') ? item : item + '\n';
      const headingRe = /(###\s*(?:📍\s*)?(?![^\n]*치지직 댓글 타임라인)[^\n]*타임라인[^\n]*\n)/;
      const match = headingRe.exec(md);
      if (!match) {
        return md.replace(/\s*$/, '') + '\n\n### 📍 타임라인 상세 요약\n\n' + normalized;
      }
      const insertAt = match.index + match[0].length;
      return md.slice(0, insertAt).replace(/\s*$/, '\n') + '\n' + normalized + md.slice(insertAt);
    }

    function insertTimelineCandidate() {
      if (!timelineCandidateMarkdown.trim()) {
        setStatus('먼저 후보를 만든 뒤 확인해 주세요.', 'err');
        return;
      }
      mdEditor.value = insertTimelineCandidateIntoMd(mdEditor.value, timelineCandidateMarkdown);
      setDirty(mdEditor.value !== savedMd);
      recordSceneFinderUse('added_to_summary');
      setStatus('후보를 본문에 넣었습니다. 저장 전 미리보기로 확인하세요.', 'ok');
    }
