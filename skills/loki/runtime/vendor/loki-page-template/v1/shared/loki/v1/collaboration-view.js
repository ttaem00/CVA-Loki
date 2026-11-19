/* Read-only collaboration display. Three independent public lanes; no attendance or synchronization inference. */
(() => {
  'use strict';
  window.createLokiCollaborationView = ({data, esc, time, icon, openModal, container}) => {
    const list = value => Array.isArray(value) ? value : [];
    const object = value => value && typeof value === 'object' && !Array.isArray(value);
    const own = (value,key) => object(value) && Object.prototype.hasOwnProperty.call(value,key);
    const finite = value => typeof value === 'number' && Number.isFinite(value);
    const nameKey = value => value.normalize('NFKC').toLowerCase();
    const validName = value => typeof value === 'string' && value.length > 0 && value === value.trim() && [...value].length <= 64 && !/\p{C}/u.test(value);
    const validReplayName = value => typeof value === 'string' && value.length > 0 && value === value.trim() && [...value].length <= 80 && !/\p{C}/u.test(value);
    const validId = value => typeof value === 'string' && /^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(value);
    const validVod = value => typeof value === 'string' && /^[1-9][0-9]{0,19}$/.test(value);
    const validChannel = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
    const raw = data.rawViewerPayload || data.rawReport || {};
    const duration = finite(raw.duration_sec) ? raw.duration_sec : data.duration;
    const videoNo = raw.video_no || data.videoNo;
    const sourceLabels = {manager_declared:'관리자 등록',source_evidence:'자료 근거',viewer_comment:'시청자 댓글',manager_context:'관리자 메모',vod_title:'방송 제목'};
    const supportLabels = {supported:'기록된 구간',provisional:'미확인'};
    const notices = {periods:0,links:0,candidates:0,replays:0};
    const rawPeriods = own(raw,'collaboration_periods') ? raw.collaboration_periods : list(data.collaborationPeriods).map(row=>row.raw || row);
    const rawCandidates = own(raw,'collaboration_candidates') ? raw.collaboration_candidates : data.collaborationCandidates;
    const rawReplays = own(raw,'collaboration_replays') ? raw.collaboration_replays : data.collaborationReplays;
    const validateNames = value => Array.isArray(value) && value.length <= 32 && value.every(validName) && new Set(value.map(nameKey)).size === value.length;
    const validSources = ['manager_declared','source_evidence'];
    const usablePeriods = [];
    if (Array.isArray(rawPeriods) && rawPeriods.length <= 128) {
      for (const row of rawPeriods) {
        if (!object(row) || !validId(row.id) || !finite(row.start_sec) || !finite(row.end_sec) || row.start_sec < 0 || row.end_sec <= row.start_sec || !finite(duration) || row.end_sec > duration || !validateNames(row.members) || !validSources.includes(row.source_kind) || !own(supportLabels,row.support_status)) {notices.periods++;continue;}
        usablePeriods.push({raw:row,id:row.id,start:row.start_sec,end:row.end_sec,members:row.members.slice(),source:row.source_kind,support:row.support_status,links:[]});
      }
    } else if (rawPeriods !== undefined) notices.periods++;
    // Conflicting intervals and IDs are withheld together, never resolved by first match.
    const conflicted = new Set();
    for(let i=0;i<usablePeriods.length;i++) for(let j=i+1;j<usablePeriods.length;j++) {
      const a=usablePeriods[i], b=usablePeriods[j];
      if(a.id===b.id || a.start<b.end && b.start<a.end){conflicted.add(i);conflicted.add(j);}
    }
    notices.periods += conflicted.size;
    const periods = usablePeriods.filter((_,i)=>!conflicted.has(i)).sort((a,b)=>a.start-b.start || a.end-b.end);
    for(const period of periods) {
      const input=period.raw.member_vods;
      if(input===undefined)continue;
      if(!Array.isArray(input) || input.length>4096){notices.links++;continue;}
      const links=[],seen=new Set();
      for(const link of input) {
        if(!object(link) || !period.members.includes(link.member_name) || !validVod(link.vod_no) || own(link,'channel_id') && !validChannel(link.channel_id)) {notices.links++;continue;}
        const key=JSON.stringify([link.member_name,link.vod_no,link.channel_id||'']);
        if(seen.has(key))continue;
        seen.add(key);links.push({member_name:link.member_name,vod_no:link.vod_no,...(link.channel_id?{channel_id:link.channel_id}:{}),raw:link});
      }
      const channelSets=new Map(), ownerSets=new Map();
      for(const link of links) {
        if(!ownerSets.has(link.vod_no))ownerSets.set(link.vod_no,new Set());
        ownerSets.get(link.vod_no).add(link.member_name);
        if(link.channel_id){if(!channelSets.has(link.member_name))channelSets.set(link.member_name,new Set());channelSets.get(link.member_name).add(link.channel_id);}
      }
      period.links=links.filter(link=>{
        const conflict=ownerSets.get(link.vod_no).size>1 || (channelSets.get(link.member_name)?.size||0)>1;
        if(conflict)notices.links++;return !conflict;
      });
    }
    const periodsById=new Map(periods.map(period=>[period.id,period]));
    const candidates=[];
    if(Array.isArray(rawCandidates) && rawCandidates.length<=128) {
      const ids=new Map();
      for(const candidate of rawCandidates) {
        if(!object(candidate) || !validId(candidate.id) || !validateNames(candidate.members) || !candidate.members.length || !['viewer_comment','manager_context','vod_title'].includes(candidate.source_kind) || candidate.support_status!=='provisional' || candidate.lookup_scope!=='document'){notices.candidates++;continue;}
        ids.set(candidate.id,(ids.get(candidate.id)||0)+1);
        candidates.push({id:candidate.id,members:candidate.members.slice(),source:candidate.source_kind,raw:candidate});
      }
      for(let i=candidates.length-1;i>=0;i--)if(ids.get(candidates[i].id)>1){candidates.splice(i,1);notices.candidates++;}
    } else if(rawCandidates!==undefined)notices.candidates++;
    const candidateNames=[...new Set(candidates.flatMap(candidate=>candidate.members))];
    const replayStatuses={pending:'확인 대기',live:'방송 중',open:'방송 중',waiting_vod:'다시보기 대기',linked:'다시보기 연결',no_vod:'다시보기 없음',complete:'관찰 완료',unknown:'상태 미확인',identity_ambiguous:'채널 확인 필요',unavailable:'조회 불가',ambiguous:'확인 필요',vod_ambiguous:'다시보기 선택 필요',retired:'명단에서 제외'};
    const epoch=value=>Number.isInteger(value) && value>0 && value<1e14;
    const replays=[];
    if(rawReplays!==undefined && rawReplays!==null) {
      if(!object(rawReplays) || rawReplays.schema_version!=='cva.collaboration_replays.v1' || !validVod(rawReplays.video_no) || rawReplays.video_no!==videoNo || !Array.isArray(rawReplays.members) || rawReplays.members.length>32) notices.replays++;
      else {
        const seen=new Map();
        for(const member of rawReplays.members){if(object(member)&&typeof member.name==='string')seen.set(member.name,(seen.get(member.name)||0)+1);}
        for(const member of rawReplays.members) {
          if(!object(member)||!validReplayName(member.name)||seen.get(member.name)!==1||!own(replayStatuses,member.status)||(own(member,'channel_id')&&!validChannel(member.channel_id))||!Array.isArray(member.broadcasts)||member.broadcasts.length>4096){notices.replays++;continue;}
          const broadcasts=[];
          for(const record of member.broadcasts) {
            if(!object(record)||!own(replayStatuses,record.status)||record.alignment!=='broadcast_start_unverified'||![null,'provider_close','observed_not_live','vod_duration'].includes(record.end_basis)){notices.replays++;continue;}
            const endOnly=record.start_at_ms===null && epoch(record.end_at_ms) && record.end_basis==='observed_not_live' && ['waiting_vod','no_vod'].includes(record.status);
            if(!endOnly && (!epoch(record.start_at_ms)||record.end_at_ms!==null&&(!epoch(record.end_at_ms)||record.end_at_ms<record.start_at_ms))){notices.replays++;continue;}
            const linked=record.status==='linked' && validVod(record.vod_no) && record.vod_no!==videoNo && validChannel(member.channel_id);
            // A malformed optional VOD cannot become a clickable link; the known lifecycle still survives.
            if(record.vod_no!==null&&!linked)notices.links++;
            broadcasts.push({start:record.start_at_ms,end:record.end_at_ms,endBasis:record.end_basis,status:record.status,vodNo:linked?record.vod_no:null,raw:record});
          }
          replays.push({name:member.name,status:member.status,channelId:member.channel_id||null,broadcasts,raw:member});
        }
      }
    }
    // Reject contradictory replay link owners/channels without erasing independent status records.
    const replayOwners=new Map(), replayChannels=new Map();
    for(const member of replays) {
      if(member.channelId){if(!replayChannels.has(member.name))replayChannels.set(member.name,new Set());replayChannels.get(member.name).add(member.channelId);}
      for(const record of member.broadcasts)if(record.vodNo){if(!replayOwners.has(record.vodNo))replayOwners.set(record.vodNo,new Set());replayOwners.get(record.vodNo).add(member.name);}
    }
    for(const member of replays)for(const record of member.broadcasts)if(record.vodNo && (replayOwners.get(record.vodNo).size>1||(replayChannels.get(member.name)?.size||0)>1)){record.vodNo=null;notices.links++;}
    const at=sec=>{
      if(!finite(sec)||sec<0||finite(duration)&&sec>duration)return {period:null,reason:'unusable_time'};
      if(typeof data.getCollaborationAt==='function') {
        const result=data.getCollaborationAt(sec);
        if(result && !result.period)return {period:null,reason:result.reason||'no_period'};
        if(result?.period){const p=periodsById.get(result.period.id);return {period:p && p.start<=sec&&sec<p.end?p:null,reason:p?null:'unusable_period'};}
        return {period:null,reason:'no_period'};
      }
      const covering=periods.filter(period=>period.start<=sec&&sec<period.end);
      return {period:covering.length===1?covering[0]:null,reason:covering.length>1?'ambiguous_periods':'no_period'};
    };
    const clock=sec=>esc(time(sec));
    const periodTime=period=>`${clock(period.start)} – ${clock(period.end)}`;
    const pillList=(period,limit=Infinity)=>period.members.slice(0,limit).map(name=>{
      const links=period.links.filter(link=>link.member_name===name);
      if(links.length===1)return `<a class="member-pill collab-member-link is-direct" href="https://chzzk.naver.com/video/${links[0].vod_no}" target="_blank" rel="noopener noreferrer" aria-label="${esc(name)} 다시보기 ${links[0].vod_no}, 새 탭에서 처음부터 열기" title="다시보기 처음부터 열기">${esc(name)}${icon('out')}</a>`;
      if(links.length>1)return `<button type="button" class="member-pill collab-member-link is-picker" data-member="${esc(name)}" data-period="${esc(period.id)}" aria-label="${esc(name)}의 다시보기 ${links.length}개 중 선택" aria-haspopup="dialog">${esc(name)} <span class="collab-vod-count">다시보기 ${links.length}개</span>${icon('chev')}</button>`;
      return `<span class="member-pill" title="다시보기 미연결">${esc(name)}</span>`;
    }).join('');
    const namesPreview=names=>`${names.slice(0,3).map(esc).join(' · ')}${names.length>3?`<span class="collab-more-names"> 외 ${names.length-3}명</span>`:''}`;
    const overviewAction='<button type="button" class="text-button collab-record-button" data-action="collaboration">전체 기록 '+icon('chev')+'</button>';
    const render=sec=>{
      const {period,reason}=at(sec);
      container.classList.add('loki-collaboration');
      container.classList.toggle('is-unrecorded',!period);
      let body;
      if(period) body=`<div class="collab-strip-main"><h3 id="collaborationHeading">이 시각의 합방</h3><div class="collab-strip-names">${period.members.length?pillList(period,3)+(period.members.length>3?`<span class="collab-more-names">외 ${period.members.length-3}명</span>`:''):'<span class="collab-state-text">멤버 없음으로 기록</span>'}</div><span class="collab-provenance">${esc(sourceLabels[period.source])}${period.support==='provisional'?' · 미확인':''}</span></div>`;
      else if(candidateNames.length) body=`<div class="collab-strip-main"><h3 id="collaborationHeading">합방 후보</h3><span class="collab-brief-names">${namesPreview(candidateNames)}</span><span class="collab-count">${candidateNames.length}명</span><span class="collab-provenance">참여 시점 미확인</span></div>`;
      else if(periods.length||notices.periods) body=`<div class="collab-strip-main"><h3 id="collaborationHeading">합방</h3><span class="collab-state-text">${['ambiguous_periods','unusable_period'].includes(reason)?'구간 기록 확인 필요':'이 시점의 명단 미확인'}</span>${periods.length?`<span class="collab-count">기록 ${periods.length}구간</span>`:''}</div>`;
      else if(replays.length) body=`<div class="collab-strip-main"><h3 id="collaborationHeading">합방 다시보기</h3><span class="collab-brief-names">${namesPreview(replays.map(member=>member.name))}</span><span class="collab-provenance">참여 시점 미확인</span></div>`;
      else body='<div class="collab-strip-main"><h3 id="collaborationHeading">합방</h3><span class="collab-state-text">참여 구간 기록 없음</span></div>';
      container.innerHTML=body+overviewAction;
    };
    const epochLabel=value=>value===null?'시작 미상':new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
    const replayRow=(member,record)=>{
      const status=esc(replayStatuses[record.status]);
      const hours=`${esc(epochLabel(record.start))}${record.end!==null?` – ${esc(epochLabel(record.end))}${record.endBasis==='observed_not_live'?' · 종료 확인 시각':''}`:''}`;
      const caption=`<span><strong>${status}</strong><small>${hours} · 한국 시각</small></span>`;
      return record.vodNo?`<a class="collab-replay-row" href="https://chzzk.naver.com/video/${record.vodNo}" target="_blank" rel="noopener noreferrer" aria-label="${esc(member.name)} 다시보기 ${record.vodNo}, 새 탭에서 처음부터 열기">${caption}<span class="collab-replay-action">처음부터 ${icon('out')}</span></a>`:`<div class="collab-replay-row">${caption}</div>`;
    };
    const show=sec=>{
      const {period,reason}=at(sec);
      const unresolved=['ambiguous_periods','unusable_period'].includes(reason);
      let html=`<div class="collab-current-note"><strong>${clock(sec)} 기준</strong><span>${period?(period.members.length?'이 시각을 포함하는 합방 구간이 기록되어 있습니다.':'이 구간에는 합방 멤버가 없다고 기록되어 있습니다.'):unresolved?'이 시각의 합방 기록은 확인이 필요합니다.':'이 시각의 참여 멤버는 확인되지 않았습니다.'}</span></div>`;
      if(periods.length) html+=`<section class="collab-modal-section"><h3>시간별 합방 기록 <span>${periods.length}구간</span></h3><div class="collab-period-list">${periods.map(p=>`<section class="collab-period-record${period?.id===p.id?' is-current':''}"><div class="collab-record-heading"><time>${periodTime(p)}</time>${period?.id===p.id?'<span class="collab-current-label">현재 구간</span>':''}</div><p class="collab-record-source">${esc(sourceLabels[p.source])} · ${esc(supportLabels[p.support])}</p><div class="member-list">${p.members.length?pillList(p):'<span class="collab-state-text">합방 멤버 없음으로 기록</span>'}</div></section>`).join('')}</div></section>`;
      if(candidates.length) html+=`<section class="collab-modal-section"><h3>문서에 나온 멤버 후보 <span>${candidateNames.length}명 · ${candidates.length}개 기록</span></h3><p class="collab-section-note">이름을 찾기 위한 후보입니다. 각 기록은 참여 시간이나 팀 구성을 뜻하지 않습니다.</p>${candidates.map((candidate,i)=>`<section class="collab-candidate-record"><div class="collab-record-heading"><h4>후보 기록 ${i+1}</h4><span>${esc(sourceLabels[candidate.source])} · 미확인</span></div><p>${candidate.members.map(esc).join(' · ')}</p></section>`).join('')}</section>`;
      if(replays.length) html+=`<section class="collab-modal-section"><h3>멤버 다시보기 <span>${replays.length}명</span></h3><p class="collab-section-note">방송과 다시보기의 확인 상태입니다. 이 목록만으로 특정 시각의 참여나 같은 장면의 정렬을 확정하지 않습니다.</p>${replays.map(member=>`<section class="collab-replay-member"><div class="collab-record-heading"><h4>${esc(member.name)}</h4><span>${esc(replayStatuses[member.status])}</span></div>${member.broadcasts.length?member.broadcasts.map(record=>replayRow(member,record)).join(''):'<p class="collab-section-note">기록된 방송 구간이 없습니다.</p>'}</section>`).join('')}</section>`;
      const held=Object.values(notices).reduce((sum,value)=>sum+value,0);
      if(held)html+='<p class="collab-held-note">형식이나 연결 정보가 맞지 않는 일부 기록은 표시를 보류했습니다. 정상 기록과 원본 데이터는 유지됩니다.</p>';
      if(!periods.length&&!candidates.length&&!replays.length)html+='<p class="collab-section-note">이 결과에 표시할 수 있는 합방 구간·멤버 후보·연결된 다시보기 기록이 없습니다.</p>';
      if(periods.some(p=>p.links.length)||replays.some(m=>m.broadcasts.some(r=>r.vodNo)))html+='<p class="modal-note">멤버 다시보기는 새 탭에서 처음부터 열립니다. 같은 장면으로의 시점 동기화는 확인되지 않았습니다.</p>';
      else if(candidateNames.length||periods.some(p=>p.members.length))html+='<p class="collab-unlinked-note"><strong>다시보기 미연결</strong>현재 기록에는 멤버별 다시보기 링크가 제공되지 않았습니다.</p>';
      openModal('합방 기록',html);
    };
    const openMember=(periodId,name)=>{
      const period=periodsById.get(periodId), links=period?.links.filter(link=>link.member_name===name)||[];
      if(!links.length){openModal('멤버 다시보기','<p>확인된 다시보기 연결이 없습니다.</p>');return;}
      openModal(`${name}의 다시보기`,`<p class="collab-section-note">${periodTime(period)} 합방 구간에 연결된 영상입니다.</p><div class="member-vod-list">${links.map((link,index)=>`<a class="vod-choice" href="https://chzzk.naver.com/video/${link.vod_no}" target="_blank" rel="noopener noreferrer" aria-label="${esc(name)} 다시보기 ${link.vod_no}, 새 탭에서 처음부터 열기"><span>다시보기 ${index+1}<small>VOD ${link.vod_no}</small></span><span>처음부터 ${icon('out')}</span></a>`).join('')}</div><p class="modal-note">각 영상의 시작부터 열립니다. 기준 방송의 시각을 다른 영상에 적용하지 않습니다.</p>`);
    };
    return Object.freeze({render,show,openMember});
  };
})();
