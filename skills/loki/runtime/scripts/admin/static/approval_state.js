/* Shared approval result wording and the response fields both admin views consume. */
(() => {
  const decisions = {pending:'대기',approved:'승인',rejected:'거절',stale:'기한 지남'};
  const resultStates = new Set(['applying','applied','already_present','failed','skipped']);
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  function validItem(item) {
    return object(item) && typeof item.approval_id === 'string' && item.approval_id.length > 0
      && typeof item.status === 'string' && Object.hasOwn(decisions,item.status)
      && ['request_text','risk','expected_effect','source_artifact'].every(key=>typeof item[key] === 'string')
      && object(item.proposed_action) && item.proposed_action.type === 'vocab_candidate_save'
      && (item.application_result == null || (object(item.application_result)
        && (item.application_result.status === undefined || resultStates.has(item.application_result.status))));
  }
  function decisionLabel(item) {return decisions[item?.status] || '확인 필요';}
  function applicationView(item) {
    if(item?.status === 'pending') return {text:'아직 결정하지 않아 사전에 적용하지 않았습니다.',canApply:false,tone:'muted'};
    if(item?.status === 'stale') return {text:'기한이 지나 결정·적용할 수 없습니다.',canApply:false,tone:'warn'};
    if(item?.status === 'rejected') return {text:'거절 기록을 남겼습니다. 사전에는 적용하지 않았습니다.',canApply:false,tone:'muted'};
    if(item?.status !== 'approved') return {text:'현재 결정과 적용 결과를 확인하세요.',canApply:false,tone:'warn'};
    const status=item.application_result?.status;
    if(status === 'applied') return {text:'단어 사전에 적용했습니다.',canApply:false,tone:'success'};
    if(status === 'already_present') return {text:'이미 있는 단어입니다. 이번 요청으로 기존 별칭·메모를 바꾸지 않았습니다.',canApply:false,tone:'muted'};
    if(status === 'failed' && ['invalid_payload','invalid_term'].includes(item.application_result.reason)) {
      return {text:'승인 기록은 유지했습니다. 요청 내용이 사전에 적용할 수 있는 형식이 아니므로 내용을 확인·수정해 새로 검토해야 합니다.',canApply:false,tone:'error'};
    }
    if(status === 'failed') return {text:'승인은 기록됐지만 단어 사전 적용에 실패했습니다.',canApply:true,tone:'error'};
    if(status === 'applying') return {text:'승인은 기록됐습니다. 적용 결과는 아직 확인되지 않았습니다.',canApply:true,tone:'warn'};
    if(status === 'skipped') return {text:'승인은 기록됐지만 사전 적용은 하지 않았습니다.',canApply:false,tone:'muted'};
    return {text:'승인은 기록됐지만 적용 결과 기록이 없습니다.',canApply:true,tone:'warn'};
  }
  function checkedList(data) {
    if(!object(data) || !Array.isArray(data.approvals) || !data.approvals.every(validItem)) {
      throw new Error('승인함 응답을 확인하지 못했습니다. 기존 결과를 유지합니다.');
    }
    return data.approvals;
  }
  function checkedReply(data,expectedId,expectedDecision='') {
    const item=data?.approval;
    if(data?.ok !== true || !validItem(item) || item.approval_id !== expectedId
      || !['approved','rejected'].includes(item.status) || (expectedDecision && item.status !== expectedDecision)
      || !object(item.application_result) || !resultStates.has(item.application_result.status)
      || !object(data.applied_action) || data.applied_action.status !== item.application_result.status
      || (item.status === 'rejected' && item.application_result.status !== 'skipped')) {
      throw new Error('처리 결과 응답을 확인하지 못했습니다. 현재 결정·적용 결과를 확인하세요.');
    }
    return item;
  }
  function mergeList(previous,incoming,retainIds=new Set()) {
    const current=new Map(incoming.map(item=>[item.approval_id,item]));
    const known=new Set(previous.map(item=>item.approval_id));
    const ordered=previous.flatMap(item=>current.has(item.approval_id)
      ? [current.get(item.approval_id)] : retainIds.has(item.approval_id) ? [item] : []);
    return ordered.concat(incoming.filter(item=>!known.has(item.approval_id)));
  }
  window.ReportAdminApprovalState = {decisionLabel,applicationView,checkedList,checkedReply,mergeList};
})();
