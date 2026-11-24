/* 기존 화면을 유지하는 DB 연결. 정의가 다른 항목은 저장하지 않습니다. */
'use strict';
let LIVE_DB=null,LIVE_ME=null,LIVE_READY=false,LIVE_BUSY=false;
const unlinked=message=>toast(message||'이 기능은 화면과 DB의 처리 기준을 맞춘 뒤 연결할 예정입니다. 아직 저장되지 않습니다.');
async function liveApi(path,body){
  const r=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:body?{'Content-Type':'application/json','X-CSRF-Token':LIVE_ME.csrf}:{},body:body?JSON.stringify(body):undefined});
  let d;try{d=await r.json()}catch{throw Error('서버 응답을 확인할 수 없습니다.')}
  if(r.status===401){location.replace('/login?relogin=1');throw Error('다시 로그인해 주세요.')}
  if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'입력값과 서버 상태를 확인하세요.');
  return d;
}
const livePolicy=(team,position)=>(LIVE_DB?.policies||[]).find(p=>p.team_id===team&&p.position_code===position);
const liveStatus={ACTIVE:'ACTIVE',LOCKED:'SUSPENDED',DORMANT:'DORMANT',RETIRED:'RETIRED'};
Object.assign(STATUS,{DORMANT:{label:'휴면',desc:'휴면 상태의 계정'},RETIRED:{label:'퇴사',desc:'퇴사 처리된 계정'}});
IDENTITY.HR_VERIFIED='직원 확정';IDENTITY_DESC.HR_VERIFIED='관리자가 직원 정보를 확정함. 인사 시스템 자동 대조를 의미하지 않습니다.';
EMP_STATUS.OFFICIAL='직원 확정';EMP_DESC.OFFICIAL='관리자가 소속 팀과 직책을 확인한 직원';
EMP_STATUS.EMPLOYEE_CANDIDATE='확인 전';EMP_DESC.EMPLOYEE_CANDIDATE='DB의 직원 정보가 아직 미확정';
EMP_STATUS.UNRESOLVED='검토 필요';EMP_DESC.UNRESOLVED='DB의 직원 정보가 검토 필요 상태';
AUDIT_TYPE.DB_EVENT='계정·운영 이력';AUDIT_ICON.DB_EVENT='•';AUDIT_ORDER.push('DB_EVENT');
AUDIT_TYPE.PERSON_FIX='인물 검토 처리';AUDIT_ICON.PERSON_FIX='👥';AUDIT_TYPE.PERSON_VIEW='인물 검토 원문 열람';AUDIT_ICON.PERSON_VIEW='👁';AUDIT_ORDER.push('PERSON_FIX','PERSON_VIEW');
const PERSON_ACTION_TEXT={link:'기존 PID에 연결',create:'새 PID 생성',dismiss:'제외',assign:'정보 귀속',shared:'공유 값 표시',undo:'수동 처리 되돌리기'};

// ── 활동 기록을 읽기 쉬운 문장으로 ─────────────────────────────────────────
// DB에는 {"entity":"team_position_policy","values":{"search_scope":"OWN",...}} 형태로 저장되어 있습니다.
// 화면에는 "검색 범위 본인 담당 문서 → 소속 팀 문서"처럼 바뀐 항목만 보여 줍니다. DB 기록 자체는 바꾸지 않습니다.
const LOG_FIELD={search_scope:'검색 범위',can_restore:'실명 복원',team_id:'소속 팀',position_code:'직책',verification_status:'확인 상태',
  team_name:'팀 이름',is_active:'사용 여부',role_code:'역할',status:'계정 상태',employee_id:'직원(사번)',owner_employee_id:'담당 직원',
  metadata_status:'문서 상태',login_id:'아이디',invited:'발급 방식'};
const LOG_VALUE={
  search_scope:{OWN:'본인 담당 문서',TEAM:'소속 팀 문서',NONE:'검색 불가'},
  can_restore:{true:'허용',false:'불가'},is_active:{true:'사용',false:'미사용'},invited:{true:'회원 초대',false:'직접 발급'},
  verification_status:{PENDING:'확인 전',CONFIRMED:'직원 확정',REVIEW_REQUIRED:'검토 필요'},
  metadata_status:{PENDING:'미확정',CONFIRMED:'확정',REVIEW_REQUIRED:'검토 필요'},
  status:{ACTIVE:'사용',LOCKED:'정지',DORMANT:'휴면',RETIRED:'퇴사'},
  role_code:{STAFF:'일반 직원',TEAM_LEAD:'팀장',ADMIN:'관리자'}};
function logTeam(id,d){const t=(d?.teams||[]).find(x=>x.team_id===id);return t?`${t.team_name}(${id})`:id}
function logValue(key,value,d){
  if(value===null||value===undefined||value==='')return '없음';
  if(key==='team_id')return logTeam(value,d);
  return LOG_VALUE[key]?.[String(value)]??String(value);
}
const LOG_ORDER=Object.keys(LOG_FIELD);   // 항목은 LOG_FIELD에 적은 순서로 보여 줌 (DB는 알파벳순으로 저장)
function logFields(values,keys,d,withLabel=true){return [...keys].sort((x,y)=>(LOG_ORDER.indexOf(x)+1||99)-(LOG_ORDER.indexOf(y)+1||99)).map(k=>(withLabel?LOG_FIELD[k]||k:'')+(withLabel?' ':'')+logValue(k,values?.[k],d)).join(' · ')}
function describeLog(l,d){
  const b=l.before_value,a=l.after_value,src=a||b||{};
  // 대상: 정책은 "팀 · 직책", 직원·문서는 키, 팀은 팀 이름
  const target=src.entity==='team_position_policy'?`${logTeam(src.team_id,d)} · ${src.position_code}`
    :src.entity==='teams'?logTeam(src.key,d):src.entity==='employees'?`직원 ${src.key}`
    :src.entity==='documents'?`문서 ${src.key}`:src.key||src.login_id||src.employee_id||'';
  // {"entity":..,"values":{..}} 형태면 values만, 아니면(계정 발급 등) 객체 그대로 사용
  const pick=v=>v&&typeof v==='object'?(v.entity?v.values:Object.fromEntries(Object.entries(v).filter(([k])=>!['entity','key'].includes(k)))):null;
  const bv=pick(b),av=pick(a);
  if(!bv&&!av)return {target,before:'',after:''};
  if(!bv)return {target,before:'',after:'새로 등록: '+logFields(av,Object.keys(av),d)};
  if(!av)return {target,before:(l.action==='TEST_DATA_DELETED'?'삭제: ':'')+logFields(bv,Object.keys(bv),d),after:''};
  const keys=[...new Set([...Object.keys(bv),...Object.keys(av)])].filter(k=>JSON.stringify(bv[k]??null)!==JSON.stringify(av[k]??null));
  if(!keys.length)return {target,before:'',after:'변경 없음: '+logFields(av,Object.keys(av),d)};
  // 바뀐 항목이 하나면 "검색 범위 본인 담당 문서 → 소속 팀 문서", 여러 개면 양쪽에 항목 이름을 붙임
  return {target,before:logFields(bv,keys,d),after:logFields(av,keys,d,keys.length>1)};
}

function applyLiveData(d){
  LIVE_DB=d;
  MEMBERS.length=0;EMPLOYEES.length=0;AUDIT.length=0;PERM_LOG.length=0;
  for(const k of Object.keys(TEAMS))delete TEAMS[k];
  for(const k of Object.keys(TEAM_POLICY))delete TEAM_POLICY[k];
  for(const t of d.teams){TEAMS[t.team_id]=t.team_name;TEAM_POLICY[t.team_id]={maxScope:'NONE',financial:false,restore:false,kinds:[],positions:d.policies.filter(p=>p.team_id===t.team_id)}}
  for(const e of d.employees){
    EMPLOYEES.push({id:e.employee_id,employee_no:e.employee_id,team_id:e.team_id||'',position:e.position_code||'',title:'',
      status:e.verification_status==='CONFIRMED'?'OFFICIAL':e.verification_status==='REVIEW_REQUIRED'?'UNRESOLVED':'EMPLOYEE_CANDIDATE',
      official:e.verification_status==='CONFIRMED'?'HR_VERIFIED':'NOT_VERIFIED',evidence:'직원 DB',risk:e.verification_status==='REVIEW_REQUIRED'?'검토 필요':'',
      strong_docs:0,candidate_docs:0,financial_docs:0,first_date:'—',last_date:'—',possible_match:'',rules:'',db:e});
  }
  for(const u of d.users){
    const e=d.employees.find(e=>e.employee_id===u.employee_id),p=livePolicy(e?.team_id,e?.position_code);
    MEMBERS.push({id:u.user_id,login:u.login_id,name:'—',email:'—',role:u.role_code,status:liveStatus[u.status],identity:e?.verification_status==='CONFIRMED'?'HR_VERIFIED':'UNVERIFIED',
      employee_id:u.employee_id||'',team:e?.team_id||'',position:e?.position_code||'',joined:new Date(u.created_at),lastLogin:u.last_login_at?new Date(u.last_login_at):null,
      scope:p?.search_scope||'NONE',restore:p?.can_restore||false,policyExists:!!p,history:[],db:u});
  }
  const actionNames={BOOTSTRAP:'최초 업무 관리자 생성',PASSWORD_RECOVERY:'운영자 비밀번호 복구',PASSWORD_RESET:'초기 비밀번호 재발급',PASSWORD_CHANGED:'본인 비밀번호 변경',LOGIN_FAILED:'로그인 인증 실패',USER_CREATED:'계정 발급',USERS_SAVED:'계정 정보 변경',EMPLOYEES_SAVED:'직원 정보 변경',TEAMS_SAVED:'팀 정보 변경',DOCUMENTS_SAVED:'문서 권한 정보 변경',TEAM_POSITION_POLICY_SAVED:'팀·직책 정책 변경',TEST_DATA_DELETED:'테스트 데이터 삭제',PERSON_REVIEW_VIEW:'인물 검토 원문 열람',PERSON_REVIEW_LINK:'인물 검토 · 기존 PID 연결',PERSON_REVIEW_CREATE:'인물 검토 · 새 PID 생성',PERSON_REVIEW_DISMISS:'인물 검토 · 제외',PERSON_REVIEW_ASSIGN:'인물 검토 · 정보 귀속',PERSON_REVIEW_SHARED:'인물 검토 · 공유 값 표시',PERSON_REVIEW_UNDO:'인물 검토 · 되돌리기'};
  for(const l of d.logs){
    const target=MEMBERS.find(m=>m.id===l.target_user_id);
    const person=l.action.startsWith('PERSON_REVIEW_');
    const type=l.action==='PERSON_REVIEW_VIEW'?'PERSON_VIEW':person?'PERSON_FIX':l.action==='TEAM_POSITION_POLICY_SAVED'?'TEAM_POLICY':l.action==='TEAMS_SAVED'?'TEAM_CREATE':l.action==='USERS_SAVED'?'PERMISSION':'DB_EVENT';
    const text=describeLog(l,d);   // DB의 변경 전·후 JSON을 사람이 읽는 문장으로 바꿈 (원본은 l.before_value / l.after_value)
    const row={id:l.log_id,at:new Date(l.changed_at),actor:l.actor,type,action:actionNames[l.action]||l.action,target:target?.login||text.target||l.target_role_code||'—',
      before:text.before,after:text.after,reason:l.reason,detail:'',result:''};
    if(person){const a=l.after_value||{};row.target=a.doc_id?'문서 '+a.doc_id:'인물 검토 항목 '+(a.mention_id||'');row.before='';row.after=l.action==='PERSON_REVIEW_VIEW'?'원문 열람 (검토 항목 '+a.mention_id+')':(PERSON_ACTION_TEXT[a.action]||a.action)+(a.pid?' → PID '+a.pid:'')+(a.person_deleted?' · 남은 정보가 없어 PID 삭제':'');row.detail='사유: '+l.reason}
    AUDIT.push(row);if(target)target.history.push({at:row.at,text:row.action+' · '+row.reason});
    if(['USERS_SAVED','TEAM_POSITION_POLICY_SAVED'].includes(l.action))PERM_LOG.push({at:row.at,login:row.target,summary:row.action,reason:row.reason});
  }
  AUDIT.unsort=true;
  for(const m of MEMBERS)if(!m.history.length)m.history.push({at:m.joined,text:'계정 생성'});
  MOCK.roles=d.roles.map((r,i)=>({name:r.role_name,code:r.role_code,count:MEMBERS.filter(m=>m.role===r.role_code).length,color:['#A84A12','#E8892B','#5B8C5A'][i%3]}));  // 어흥이 테마 색 (진한 주황·주황·초록)
}
async function liveRefresh(){applyLiveData(await liveApi('/api/admin/data'));route()}
async function liveSave(kind,key,values,reason,version){
  if(LIVE_BUSY)throw Error('저장 중입니다. 잠시 기다려 주세요.');LIVE_BUSY=true;
  try{await liveApi('/api/admin/save',{kind,key:String(key),values,reason,version});await liveRefresh()}
  finally{LIVE_BUSY=false}
}

drawKpis=function(){
  const active=MEMBERS.filter(m=>m.db.status==='ACTIVE').length;
  // '승인 대기'는 가입 승인 기능이 없어 집계할 수 없으므로, 화면에서 계산하는 '점검 필요' 항목 수를 보여 줍니다.
  const need=dataChecks().filter(c=>c.level!=='info'&&c.n).length;
  $('kpis').innerHTML=[['k-blue',ICON.users,'전체 회원',num(MEMBERS.length)+'<small>명</small>','#members'],['k-orange',ICON.wait,'점검 필요',need?num(need)+'<small>건</small>':'없음','#settings'],['k-green',ICON.shield,'활성 계정',num(active)+'<small>명</small>','#members'],['k-purple',ICON.doc,'검색 요청 수','미집계','#activity']]
    .map(([cls,icon,label,value,href])=>`<div class="kpi ${cls}"><span class="ic" aria-hidden="true">${icon||''}</span><div><div class="l">${label}</div><div class="n">${value}</div></div><a class="more" href="${href}">자세히 보기 ›</a></div>`).join('');
};
drawTables=function(){
  $('newMembers').innerHTML=[...MEMBERS].sort((a,b)=>b.joined-a.joined).slice(0,5).map(m=>`<tr><td>${ymd(m.joined)}</td><td>${esc(m.login)}</td><td>—</td><td>${esc(STATUS[m.status].label)}</td><td>${esc(ROLE_LABEL[m.role])}</td></tr>`).join('')||'<tr><td colspan="5">등록된 계정이 없습니다.</td></tr>';
  $('activity').innerHTML=sortedAudit().slice(0,5).map(r=>`<tr><td>${fmtDT(r.at)}</td><td>${esc(r.actor)}</td><td>${esc(r.action)}</td><td>${esc(r.target)}</td></tr>`).join('')||'<tr><td colspan="4">기록이 없습니다.</td></tr>';
};
drawSecurity=async function(){
  const checks=$('checks'),info=$('secText');try{const h=await liveApi('/api/admin/health');if(!document.contains(checks))return;
    const me=MEMBERS.find(m=>m.login===LIVE_ME.login_id);
    checks.innerHTML=`<li>${ICON.ok}<span>관리자 API·DB 연결 확인</span></li><li>${ICON.wait}<span>최근 로그인: ${me?.lastLogin?esc(fmtDT(me.lastLogin)):'기록 없음'}</span></li><li>${ICON.wait}<span>보안 경고·백업·외부 AI 상태: 미연결</span></li>`;
    info.textContent='DB 연결 결과입니다. 전체 시스템 또는 개인정보 보호가 정상이라는 판정은 제공하지 않습니다.';
  }catch(e){if(document.contains(info)){info.textContent=e.message;checks.textContent='연결 확인 실패'}}
};
effective=function(m,over={}){
  const v={...m,...over},p=livePolicy(v.team,v.position);
  const enabled=v.db?.status==='ACTIVE'&&v.identity==='HR_VERIFIED'&&LIVE_DB.teams.find(t=>t.team_id===v.team)?.is_active;
  return {scope:enabled?(p?.search_scope||'NONE'):'NONE',restore:false,kinds:[],financial:false,manage:v.role==='ADMIN',scopeCapped:!enabled,restoreCapped:!!p?.can_restore,restoreWhy:p?.can_restore?'복원 기능 미연결':''};
};
canDoList=function(e,team){return [[e.scope!=='NONE',e.scope==='TEAM'?`${team}의 확정 문서 검색`:e.scope==='OWN'?'본인 담당 확정 문서 검색':'현재 검색 권한 없음'],[false,'금융 문서별 세부 정책은 연결 보류'],[false,'실명 복원 기능은 연결되지 않음'],[e.manage,e.manage?'계정·팀 관리 권한 있음':'계정 관리 권한 없음']]};
lockReason=function(m){return m.db.status==='ACTIVE'?'':'현재 '+STATUS[m.status].label+' 상태입니다.'};
// 계정 상태 변경: 정지(LOCKED)·퇴사(RETIRED)·다시 사용(ACTIVE). DB가 허용하는 상태값만 씁니다.
// 일반 직원 계정은 DB 규칙상 직원 연결을 끊을 수 없어서, '연결 해제' 대신 퇴사 처리로 로그인을 막습니다.
const ACCOUNT_ACTION={
  suspend:{to:'LOCKED',title:'계정을 정지할까요?',yes:'정지',danger:true,text:'정지하면 이 계정으로 로그인할 수 없어요. 나중에 정지 해제로 되돌릴 수 있어요.'},
  retire:{to:'RETIRED',title:'퇴사 처리할까요?',yes:'퇴사 처리',danger:true,text:'퇴사 처리하면 이 계정으로 로그인할 수 없어요. 계정과 직원 정보는 지우지 않고 이력으로 남겨요.'},
  resume:{to:'ACTIVE',title:'계정을 다시 사용할까요?',yes:'다시 사용',danger:false,text:'계정을 사용 상태로 되돌려 다시 로그인할 수 있게 해요. 검색 범위는 소속 팀·직책 정책을 따라요.'}};
actionsFor=function(m){
  const a=[['detail','상세정보 보기'],['perm','권한 수정']],s=m.db.status;
  if(m.login!==LIVE_ME.login_id){
    if(s==='ACTIVE')a.push(['suspend','계정 정지',true]);
    if(s==='LOCKED')a.push(['resume','정지 해제']);
    if(['DORMANT','RETIRED'].includes(s))a.push(['resume',s==='RETIRED'?'퇴사 취소 (다시 사용)':'휴면 해제']);
    if(s!=='RETIRED')a.push(['retire','퇴사 처리 (계정 사용 중지)',true]);
  }
  return a;
};
act=async function(kind,id){
  const m=MEMBERS.find(x=>x.id===id);if(!m)return;
  try{
    if(kind==='detail')return openDetail(m);
    if(kind==='perm'){setMemberView('perm');return openPermEdit(m)}
    const spec=ACCOUNT_ACTION[kind];
    if(!spec)return unlinked('가입 승인·신원 대조 절차는 DB와 의미를 맞춘 뒤 연결합니다.');
    if(m.login===LIVE_ME.login_id)return toast('본인 계정의 상태는 바꿀 수 없어요.');
    const docs=m.employee_id?ownedDocs(m.employee_id):[];
    const docNote=kind==='retire'&&docs.length?`<br>⚠️ 이 직원(${esc(m.employee_id)})이 담당한 문서 <b>${docs.length}건</b>은 그대로 남아 같은 팀 직원은 계속 검색할 수 있어요. 필요하면 처리 후 문서 관리에서 담당자를 바꿔 주세요.`:'';
    const r=await ask({title:spec.title,yes:spec.yes,danger:spec.danger,
      text:`<b>${esc(m.login)}</b> 계정 · 현재 <b>${esc(STATUS[m.status]?.label||m.db.status)}</b><br>${spec.text}${docNote}`,
      fields:reasonField(kind==='retire'?'예: 2026-11-30 퇴사':'변경 사유'),check:v=>(v.reason||'').trim().length>=3?'':'사유를 3자 이상 적어 주세요. 활동 기록에 남아요.'});
    if(!r)return;
    await liveSave('users',m.id,{status:spec.to},r.reason,m.db.updated_at);closeDetail();toast('DB에 저장했습니다.');
    if(kind==='retire'&&docs.length){
      const go=await ask({title:'담당 문서 정리',yes:'문서 관리로 이동',text:`퇴사 처리한 직원이 담당한 문서 <b>${docs.length}건</b>을 문서 관리에서 모아 보여 드릴게요.`});
      if(go)openDocuments({q:m.employee_id});
    }
  }catch(e){toast(e.message)}
};
// 회원 초대: 관리자가 사번만 입력 → 아이디=사번, 일반 직원(STAFF) 계정 발급. 임시 비밀번호는 한 번만 보여 주고 화면에서 지웁니다.
invite=async function(){
  const r=await ask({title:'회원 초대',yes:'초대하기',
    text:'초대할 직원의 <b>사번</b>만 입력하세요. 아이디는 사번으로, 역할은 일반 직원(STAFF)으로 계정을 발급합니다.<br>직원 정보가 없으면 \'확인 전\' 직원으로 함께 등록되며, 팀 관리에서 팀·직책을 확정하기 전에는 검색할 수 없습니다.',
    fields:'<label for="fEmpNo">사번</label><input id="fEmpNo" name="employee_id" maxlength="100" autocomplete="off" spellcheck="false" placeholder="예: E2026001">',
    check:v=>!v.employee_id?'사번을 입력하세요.':!/^[A-Za-z0-9_.-]+$/.test(v.employee_id)?'사번은 영문·숫자와 _ . - 만 쓸 수 있어요.'
      :MEMBERS.some(m=>m.employee_id===v.employee_id||m.login===v.employee_id)?'이미 계정이 있는 사번입니다.':''});
  if(!r)return;
  if(LIVE_BUSY)return toast('저장 중입니다. 잠시 기다려 주세요.');
  LIVE_BUSY=true;let d;
  try{d=await liveApi('/api/admin/invite',{employee_id:r.employee_id})}catch(e){LIVE_BUSY=false;return toast(e.message)}
  try{await liveRefresh()}catch(e){toast(e.message)}finally{LIVE_BUSY=false}
  await ask({title:'초대 완료 · 임시 비밀번호',yes:'전달했어요',cancel:false,
    text:`아이디 <b>${esc(d.login_id)}</b> 계정을 발급했어요.${d.employee_created?' 직원 정보도 \'확인 전\'으로 함께 등록했어요.':''}<br>아래 임시 비밀번호는 <b>지금 한 번만</b> 보여요. 직원에게 안전하게 전달해 주세요. 첫 로그인 때 비밀번호를 바꿔야 해요.`,
    fields:`<label for="fTemp">임시 비밀번호</label><input id="fTemp" readonly value="${esc(d.temporary_password)}" onfocus="this.select()">`});
  $('dlgFields').innerHTML='';d=null;
};
const originalBindMembers=bindMembers;
bindMembers=function(){originalBindMembers();$('mRefresh').onclick=()=>liveRefresh().then(()=>toast('DB 목록을 새로 불러왔습니다.')).catch(e=>toast(e.message));$('mInvite').title='사번으로 일반 직원 계정 발급';const tiles=$('mTiles').children;for(const i of [2,3]){tiles[i].querySelector('.n').textContent='미연결';tiles[i].querySelector('.d').textContent='가입 승인·인사 대조 흐름 협의 필요'}const notice=$('mListView').querySelector('.notice span');if(notice)notice.textContent='현재는 DB의 계정 상태와 직원 확인 상태를 각각 조회합니다. 가입 승인·인사 시스템 대조는 연결 보류입니다.'};
const originalDrawMembers=drawMembers;
drawMembers=function(){originalDrawMembers();const tiles=$('mTiles').children;for(const i of [2,3]){tiles[i].querySelector('.n').textContent='미연결';tiles[i].querySelector('.d').textContent='가입 승인·인사 대조 흐름 협의 필요'}for(const key of ['PENDING','IDENTITY_PENDING']){const tab=$('mTabs').querySelector(`[data-tab="${key}"]`);if(tab){tab.disabled=true;tab.title='DB 처리 기준 협의 후 연결';tab.querySelector('.c').textContent='—'}}const options=$('mIdentity').options;for(const o of options){if(o.value==='HR_VERIFIED')o.textContent='직원 확정';if(o.value==='DOC_CONFIRMED'){o.disabled=true;o.textContent='문서 대조 (미연결)'}}};
const originalDrawRoles=drawRoles;
drawRoles=function(){originalDrawRoles();document.querySelectorAll('#pBody .diffMark').forEach(n=>n.remove());const stat=$('pStats');if(stat){const last=stat.lastElementChild;last.querySelector('.t').textContent='정책이 없는 회원';last.querySelector('.n').textContent=MEMBERS.filter(m=>!m.policyExists).length}};
const originalOpenPermEdit=openPermEdit;
openPermEdit=function(m){
  originalOpenPermEdit(m);const form=$('pForm');
  form.team.disabled=true;
  form.querySelectorAll('input[name=scope],input[name=restore]').forEach(n=>n.disabled=true);
  form.querySelectorAll('input[name=role]').forEach(n=>n.replaceWith(n.cloneNode(true)));
  if(m.login===LIVE_ME.login_id)form.querySelectorAll('input[name=role]').forEach(n=>{if(n.value!=='ADMIN')n.disabled=true});
  $('roleHint').textContent='역할은 계정 관리 권한을 구분합니다. 검색 범위는 소속 팀·직책의 정책으로 정해집니다.';
  $('teamHint').textContent='소속 팀 변경은 직원 정보 변경과 연결돼 있어 이 화면에서는 연결을 보류했습니다.';
  $('restoreHint').textContent='복원은 설정값만 존재하며 실제 복원 기능은 미연결입니다.';
  form.onsubmit=async e=>{e.preventDefault();const role=form.querySelector('input[name=role]:checked').value,reason=form.reason.value.trim();if(reason.length<3){$('pErr').textContent='변경 사유를 3자 이상 입력하세요.';return}if(role===m.role){$('pErr').textContent='역할 변경 내용이 없습니다.';return}
    try{await liveSave('users',m.id,{role_code:role},reason,m.db.updated_at);closeDetail();toast('계정 역할을 DB에 저장했습니다.')}catch(error){if($('pErr'))$('pErr').textContent=error.message;else toast(error.message)}};
};
const originalBindRoles=bindRoles;
bindRoles=function(){originalBindRoles();$('qDefaults').onclick=()=>ask({title:'권한 적용 기준',yes:'닫기',cancel:false,text:'계정 관리 권한은 역할로, 검색 범위는 소속 팀과 직책이 일치하는 정책으로 정합니다. 정책이 없으면 검색을 차단합니다. 개인별 검색 범위·복원 예외는 아직 연결하지 않았습니다.'});const text=$('mPermView').querySelector('.rolesHead p');if(text)text.textContent='역할 변경은 DB에 저장합니다. 검색 범위는 팀·직책 정책을 조회하며 개인별 예외와 복원 기능은 연결 보류입니다.'};
savePerm=function(){unlinked('개인별 검색 범위·복원 예외는 연결을 보류했습니다.')};

const originalDrawTeams=drawTeams;
drawTeams=function(){originalDrawTeams();$('tSource').textContent=`직원 DB: ${EMPLOYEES.length}명 · 팀 DB: ${Object.keys(TEAMS).length}개. 사번·팀·직책을 조회하며 문서 이름 토큰과 연결하지 않습니다. 문서 근거·인사 대조 항목은 미연결입니다.`;const hero=$('view').querySelector('.hero p');if(hero)hero.textContent='직원 DB의 사번·소속 팀·직책과 계정 연결을 관리합니다. 문서 속 인물사전과는 별도로 관리합니다.';for(const [key,label] of [['OFFICIAL','직원 확정'],['EMPLOYEE_CANDIDATE','확인 전'],['UNRESOLVED','검토 필요']]){const tile=$('tTiles').querySelector(`[data-tile="${key}"] .l`);if(tile)tile.textContent=label}const pending=$('tTiles').querySelector('[data-tile="DOC_CONFIRMED"]');if(pending){pending.disabled=true;pending.querySelector('.n').textContent='미연결'}for(const o of $('tStatus').options){if(EMP_STATUS[o.value])o.textContent=EMP_STATUS[o.value];if(o.value==='DOC_CONFIRMED'){o.disabled=true;o.textContent='문서 대조 (미연결)'}}};
const originalBindTeams=bindTeams;
bindTeams=function(){originalBindTeams();$('tSource').textContent=`직원 DB ${EMPLOYEES.length}명 · 문서 인물과 별도로 관리합니다. 인사 시스템 대조는 미연결입니다.`;const add=$('tNewEmp');if(add){add.onclick=()=>openEmployeeForm(null);add.title='사번·소속 팀·직책으로 직원 등록'}};
const originalDrawEmpCard=drawEmpCard;
drawEmpCard=function(){originalDrawEmpCard();const card=$('tCard');if(!card)return;for(const li of card.querySelectorAll('li')){const key=li.firstElementChild?.textContent||'';if(/문서|최초|최근|근거/.test(key)&&li.lastElementChild)li.lastElementChild.textContent='미연결'}
  // 직원 카드의 [직원 정보 수정] 버튼
  const e=EMPLOYEES.find(x=>x.id===tState.sel);
  // '근거 문서'(문서 속 인물 대조, 미연결) 자리에 실제 '담당 문서' 수를 보여 주고 문서 관리로 연결합니다.
  if(e){
    for(const li of card.querySelectorAll('li')){
      const key=li.firstElementChild?.textContent||'';
      if(key==='근거 문서'){const docs=ownedDocs(e.id),conf=docs.filter(d=>d.metadata_status==='CONFIRMED').length;li.firstElementChild.textContent='담당 문서';
        li.lastElementChild.innerHTML=docs.length?`<button type="button" class="linkBtn" data-emp-docs>${docs.length}건 (확정 ${conf}건) ›</button>`:'없음';
        li.querySelector('[data-emp-docs]')?.addEventListener('click',ev=>{ev.stopPropagation();openDocuments({q:e.id})})}
      if(key==='문서 날짜')li.remove();
    }
    const un=card.querySelector('[data-unlink]');if(un){un.textContent='계정 퇴사 처리';un.title='일반 계정은 직원 연결을 끊을 수 없어 계정 사용을 중지합니다'}
  }
  if(e&&e.db&&!card.querySelector('[data-emp-edit]')){const row=document.createElement('div');row.className='btnRow';row.style.margin='4px 0 12px';
    row.innerHTML='<button type="button" class="ghostBtn" data-emp-edit>✎ 직원 정보 수정</button>';
    (card.querySelector('.ctr')||card.querySelector('h3'))?.after(row);row.firstElementChild.onclick=()=>openEmployeeForm(e)}
};

// ── 직원 등록·수정 (계획서 2단계) ─────────────────────────────────────────
// 기존 저장 API(/api/admin/save, kind=employees)를 그대로 씁니다. 서버가 확정 조건·동시 수정 충돌·변경 이력을 처리합니다.
// 직책은 고른 팀의 직책별 정책(team_position_policy)에 등록된 것만 고릅니다. 정책은 직책 글자가 정확히 같아야 적용되기 때문입니다.
const EMP_DB_STATUS={PENDING:'확인 전',CONFIRMED:'직원 확정',REVIEW_REQUIRED:'검토 필요'};
const POSITION_ORDER=['사원','주임','대리','과장','차장','부장','팀장'];
function teamPositions(teamId){
  if(!teamId)return [];
  const rank=p=>{const i=POSITION_ORDER.indexOf(p);return i<0?99:i};
  return [...new Set((LIVE_DB?.policies||[]).filter(p=>p.team_id===teamId).map(p=>p.position_code))].sort((a,b)=>rank(a)-rank(b)||a.localeCompare(b));
}
function positionOptions(teamId,current){
  const list=teamPositions(teamId);
  let html='<option value="">직책 없음</option>'+list.map(p=>`<option value="${esc(p)}"${p===current?' selected':''}>${esc(p)}</option>`).join('');
  // 정책에 없는 기존 값은 지우지 않고 그대로 보여 줍니다 (새로 고를 수는 없음).
  if(current&&!list.includes(current))html+=`<option value="${esc(current)}" selected>${esc(current)} (이 팀 정책에 없음)</option>`;
  return html;
}
function employeeFields(e){
  const db=e?.db||{},team=db.team_id||'',status=db.verification_status||'PENDING';
  const teams=(LIVE_DB?.teams||[]).filter(t=>t.is_active||t.team_id===team);
  return (e?`<p class="fhint" style="margin:0 0 8px">사번 <b>${esc(db.employee_id)}</b>은(는) 바꿀 수 없습니다.</p>`
      :`<label for="fEmpId">사번</label><input id="fEmpId" name="employee_id" maxlength="100" autocomplete="off" spellcheck="false" placeholder="예: E2026034">`)
    +`<label for="fEmpTeam">소속 팀</label><select id="fEmpTeam" name="team_id"><option value="">팀 없음</option>${teams.map(t=>`<option value="${esc(t.team_id)}"${t.team_id===team?' selected':''}>${esc(t.team_name)} (${esc(t.team_id)})${t.is_active?'':' · 미사용'}</option>`).join('')}</select>`
    +`<label for="fEmpPos">직책</label><select id="fEmpPos" name="position_code">${positionOptions(team,db.position_code||'')}</select><p class="fhint" id="fEmpPosHint"></p>`
    +`<label for="fEmpStatus">확인 상태</label><select id="fEmpStatus" name="verification_status">${Object.entries(EMP_DB_STATUS).map(([k,v])=>`<option value="${k}"${k===status?' selected':''}>${v}</option>`).join('')}</select>`
    +`<div class="warnBox" id="fEmpImpact" style="margin:8px 0 10px"></div>`
    +reasonField(e?'예: 인사팀 확인 후 소속 팀 변경':'예: 신규 입사자 등록');
}
// ── 소속 팀·직책·확인 상태를 바꿀 때의 영향 (allowed_documents 와 같은 규칙으로 계산) ──
const SCOPE_TEXT={OWN:'본인 담당 문서',TEAM:'소속 팀 문서',NONE:'검색 불가'};
const ownedDocs=empId=>(LIVE_DB?.documents||[]).filter(d=>d.owner_employee_id===empId);
function scopeFor(teamId,pos,status){
  if(status!=='CONFIRMED'||!teamId||!(LIVE_DB?.teams||[]).find(t=>t.team_id===teamId)?.is_active)return 'NONE';
  return livePolicy(teamId,pos)?.search_scope||'NONE';
}
function employeeImpact(e,teamId,pos,status){
  const before=e?scopeFor(e.db.team_id,e.db.position_code,e.db.verification_status):'NONE',after=scopeFor(teamId,pos,status);
  const acct=e&&linkedMember(e),lines=[];
  lines.push(`🔍 검색 범위: ${e?`${SCOPE_TEXT[before]} → `:''}<b>${SCOPE_TEXT[after]}</b> ${acct?`(계정 <b>${esc(acct.login)}</b>에 바로 적용)`:'(연결된 계정 없음)'}`);
  if(status==='CONFIRMED'&&teamId&&pos&&!livePolicy(teamId,pos))lines.push('⚠️ 이 팀·직책 조합의 검색 정책이 없어 검색할 수 없어요.');
  if(e){
    const docs=ownedDocs(e.id),conf=docs.filter(d=>d.metadata_status==='CONFIRMED').length;
    if(docs.length&&teamId!==(e.db.team_id||''))
      lines.push(`⚠️ 이 직원이 담당한 문서 <b>${docs.length}건</b>(확정 ${conf}건)은 <b>${esc(TEAMS[e.db.team_id]||'이전 팀')}</b> 문서로 그대로 남아요. 담당자 본인은 팀을 옮겨도 계속 이 문서를 볼 수 있으니, 저장한 뒤 <b>문서 관리</b>에서 담당자를 다시 지정하세요.`);
    else if(docs.length&&status!=='CONFIRMED')
      lines.push(`⚠️ 확정 직원이 아니게 되면 담당 문서 ${docs.length}건은 담당 불일치가 됩니다. 문서 관리에서 정리하세요.`);
  }
  return lines.join('<br>');
}
// 문서 관리 화면을 특정 조건으로 엽니다 (예: 이 직원이 담당한 문서만)
function openDocuments(filter={}){Object.assign(docState,{q:'',team:'',type:'',status:''},filter);docState.sel.clear();if(location.hash==='#documents')route();else location.hash='#documents'}
// 새 직원은 입력한 값만, 수정은 바뀐 값만 보냅니다.
function employeeChanges(v,e){
  const next={team_id:v.team_id||null,position_code:v.position_code||null,verification_status:v.verification_status};
  if(!e)return Object.fromEntries(Object.entries(next).filter(([,val])=>val!==null));
  return Object.fromEntries(Object.entries(next).filter(([k,val])=>(e.db[k]??null)!==val));
}
function checkEmployee(v,e){
  if(!e){
    if(!v.employee_id)return '사번을 입력하세요.';
    if(!/^[A-Za-z0-9_.-]+$/.test(v.employee_id))return '사번은 영문·숫자와 _ . - 만 쓸 수 있어요.';
    if(EMPLOYEES.some(x=>x.id===v.employee_id))return '이미 등록된 사번입니다. 목록에서 골라 수정하세요.';
  }
  if(v.position_code&&!v.team_id)return '직책을 고르려면 소속 팀을 먼저 고르세요.';
  const keepOld=e&&v.team_id===(e.db.team_id||'')&&v.position_code===(e.db.position_code||'');
  if(v.position_code&&!keepOld&&!teamPositions(v.team_id).includes(v.position_code))return '직책은 목록에서 고르세요.';
  if(v.verification_status==='CONFIRMED'&&(!v.team_id||!v.position_code))return '직원 확정에는 소속 팀과 직책이 모두 필요합니다.';
  if(e&&!Object.keys(employeeChanges(v,e)).length)return '바뀐 내용이 없습니다.';
  if((v.reason||'').length<3)return '사유를 3자 이상 적어 주세요. 활동 기록에 남아요.';
  return '';
}
async function openEmployeeForm(e){
  if(!LIVE_READY)return toast('DB 목록을 불러온 뒤 다시 시도하세요.');
  if(LIVE_BUSY)return toast('저장 중입니다. 잠시 기다려 주세요.');
  const pending=ask({title:e?`직원 정보 수정 · ${e.id}`:'직원 추가',yes:e?'수정 저장':'직원 등록',
    text:e?'소속 팀·직책·확인 상태를 바꿉니다. 바뀐 항목만 저장하고 활동 기록에 남깁니다.'
      :'새 직원을 등록합니다. 직책은 고른 팀의 <b>직책별 검색 정책</b>에 있는 것만 고를 수 있어요.<br>로그인 계정은 만들지 않습니다. 계정은 회원·권한 관리의 <b>회원 초대</b>에서 사번으로 발급하세요.',
    fields:employeeFields(e),check:v=>checkEmployee(v,e)});
  // ask()는 입력 칸을 바로 그려 두므로, 팀을 바꾸면 그 팀의 직책 목록으로 다시 채웁니다.
  const team=$('fEmpTeam'),pos=$('fEmpPos'),hint=$('fEmpPosHint');
  const sync=()=>{
    const prev=pos.value,orig=team.value===(e?.db.team_id||'')?(e?.db.position_code||''):'',list=teamPositions(team.value);
    pos.innerHTML=positionOptions(team.value,orig);if(list.includes(prev))pos.value=prev;
    pos.disabled=!team.value;
    hint.textContent=!team.value?'팀을 고르면 그 팀의 직책 목록이 나옵니다.'
      :list.length?'직책 목록은 이 팀의 직책별 검색 정책에서 가져옵니다.'
      :'이 팀은 정책에 등록된 직책이 없어요. 팀 목록 → 권한 설정에서 직책을 먼저 추가하세요.';
    showImpact();
  };
  const status=$('fEmpStatus'),impact=$('fEmpImpact');
  const showImpact=()=>{impact.innerHTML=employeeImpact(e,team.value,pos.value,status.value)};
  team.onchange=sync;pos.onchange=status.onchange=showImpact;sync();
  const r=await pending;if(!r)return;
  const id=e?e.id:r.employee_id;
  const moved=e&&(r.team_id||'')!==(e.db.team_id||''),docs=e?ownedDocs(e.id):[];
  try{
    tState.sel=id;
    await liveSave('employees',id,employeeChanges(r,e),r.reason,e?e.db.updated_at:null);
    toast(e?'직원 정보를 DB에 저장했습니다.':`직원 ${id}을(를) DB에 등록했습니다.`);
  }catch(error){return toast(error.message)}
  // 팀을 옮긴 직원에게 담당 문서가 있으면 바로 정리하러 갈 수 있게 합니다.
  if(moved&&docs.length){
    const go=await ask({title:'담당 문서 정리',yes:'문서 관리로 이동',
      text:`<b>${esc(id)}</b> 직원이 담당한 문서 <b>${docs.length}건</b>이 이전 팀에 남아 있어요.<br>문서 관리에서 이 직원의 문서만 모아 보여 드릴게요. 새 담당자를 지정하거나 상태를 바꿔 주세요.`});
    if(go)openDocuments({q:id});
  }
}
const originalDrawTeamList=drawTeamList;
drawTeamList=function(){originalDrawTeamList();for(const tr of $('tTeamBody').querySelectorAll('tr')){const id=tr.children[1]?.textContent.trim(),team=LIVE_DB.teams.find(t=>t.team_id===id);if(tr.children[5])tr.children[5].textContent=team?ymd(new Date(team.created_at)):'—'}};
policyChips=function(p){const positions=p.positions||[];return `<span class="pchip scope">직책 정책 ${positions.length}개</span><span class="pchip">금융 미연결</span><span class="pchip">복원 미연결</span>`};
policyText=function(p){return `직책별 정책 ${(p.positions||[]).length}개 · 금융 문서·개별 복원 항목은 미연결`};
let teamPolicyEditor=null;
const TEAM_SCOPE_LABEL={TEAM:'소속 팀 전체 문서',OWN:'본인 담당 문서',NONE:'검색 불가'};
function teamPolicyRowsFromData(id){
  const stored=LIVE_DB.policies.filter(p=>p.team_id===id);
  const positions=[...new Set(['팀장','과장','사원',...stored.map(p=>p.position_code),...LIVE_DB.employees.filter(e=>e.team_id===id&&e.position_code).map(e=>e.position_code)])];
  const order=['팀장','부장','차장','과장','대리','사원'];
  positions.sort((a,b)=>(order.includes(a)?order.indexOf(a):100)-(order.includes(b)?order.indexOf(b):100)||a.localeCompare(b,'ko'));
  return positions.map((position,index)=>{const old=stored.find(p=>p.position_code===position);return{key:index,position,scope:old?.search_scope||'NONE',version:old?.updated_at||null,existing:!!old,fixed:true,originalScope:old?.search_scope||null}});
}
function teamPolicyRowMarkup(row,id){
  const count=LIVE_DB.employees.filter(e=>e.team_id===id&&e.position_code===row.position).length;
  const title=row.fixed?`<strong>${esc(row.position)}</strong><input type="hidden" data-tp-position value="${esc(row.position)}">`:`<input type="text" data-tp-position aria-label="새 직책 이름" maxlength="100" placeholder="직책 이름 · 예: 대리" value="${esc(row.position)}">`;
  return `<fieldset class="tp-row" data-tp-row="${row.key}"><div class="tp-head">${title}<small>${count}명 · ${row.existing?'저장된 정책':'미설정'}</small>${row.existing?'<span class="ev" title="검색을 막으려면 검색 불가를 선택하세요">등록됨</span>':`<button type="button" class="linkBtn" data-tp-remove="${row.key}" aria-label="${esc(row.position||'새 직책')} 행 제거">제거</button>`}</div><label class="lbl" for="tpScope${row.key}">검색 범위</label><select id="tpScope${row.key}" data-tp-scope aria-label="${esc(row.position||'새 직책')} 검색 범위">${Object.entries(TEAM_SCOPE_LABEL).map(([value,label])=>`<option value="${value}"${row.scope===value?' selected':''}>${label}</option>`).join('')}</select></fieldset>`;
}
function collectTeamPolicyRows(editor){
  return [...$('tpRows').querySelectorAll('[data-tp-row]')].map(element=>{const old=editor.rows.find(r=>r.key===Number(element.dataset.tpRow));return{...old,position:element.querySelector('[data-tp-position]').value.trim(),scope:element.querySelector('[data-tp-scope]').value}});
}
function validateTeamPolicyRows(rows,reason){
  if(!rows.length)return '직책을 한 개 이상 추가하세요.';
  if(rows.length>100)return '한 팀에는 한 번에 100개 이하의 직책을 설정하세요.';
  if(rows.some(r=>!r.position||r.position.length>100))return '직책 이름을 1~100자로 입력하세요.';
  if(new Set(rows.map(r=>r.position)).size!==rows.length)return '같은 직책이 두 번 있습니다. 직책 이름을 확인하세요.';
  if(rows.some(r=>!Object.hasOwn(TEAM_SCOPE_LABEL,r.scope)))return '검색 범위를 확인하세요.';
  if(reason.trim().length<3)return '변경 사유를 3자 이상 입력하세요.';
  return '';
}
editTeamPolicy=function(id){
  const team=LIVE_DB.teams.find(t=>t.team_id===id);if(!team)return toast('팀을 찾을 수 없습니다. 목록을 새로고침하세요.');
  if(!$('sheet').classList.contains('open'))sheetReturn=document.activeElement;
  const rows=teamPolicyRowsFromData(id);
  const editor={teamId:id,rows,nextKey:rows.length,saving:false};teamPolicyEditor=editor;
  $('sheetBody').innerHTML=`<h3 id="sheetTitle">${esc(team.team_name)} · 팀 권한 설정<button class="x" type="button" data-sheet-close aria-label="닫기">×</button></h3><p class="fhint">각 직책의 검색 범위를 따로 설정합니다. 직원 정보의 소속 팀·직책과 일치하는 정책이 적용되며, 미설정 직책은 검색할 수 없습니다.</p>${team.is_active?'':'<div class="warnBox">현재 미사용 팀입니다. 정책을 저장해도 팀을 사용 상태로 바꾸기 전에는 검색할 수 없습니다.</div>'}<form id="teamPolicyForm" class="pform"><div id="tpRows">${rows.map(row=>teamPolicyRowMarkup(row,id)).join('')}</div><button type="button" class="ghostBtn" id="tpAdd">＋ 직책 추가</button><div><label class="lbl" for="tpReason">변경 사유 (필수)</label><textarea id="tpReason" rows="2" maxlength="500" placeholder="예: 팀 내 직책별 업무 범위 설정"></textarea></div><p class="fhint">검색은 확정된 직원·사용 중인 팀·확정된 문서에 한해 적용됩니다. 개인정보 복원 기능은 미연결이며, 검색 불가로 바꾸면 해당 직책의 복원 허용 설정도 꺼집니다.</p><div id="tpError" class="ferr" role="alert"></div></form><div class="sheetActs"><button type="button" class="ghostBtn" data-sheet-close>취소</button><button type="submit" class="primaryBtn" form="teamPolicyForm" id="tpSave">직책별 권한 저장</button></div>`;
  $('tpAdd').onclick=()=>{editor.rows=collectTeamPolicyRows(editor);if(editor.rows.length>=100)return toast('직책은 한 번에 100개까지 설정할 수 있습니다.');const row={key:editor.nextKey++,position:'',scope:'NONE',version:null,existing:false,fixed:false,originalScope:null};editor.rows.push(row);$('tpRows').insertAdjacentHTML('beforeend',teamPolicyRowMarkup(row,id));$('tpRows').querySelector(`[data-tp-row="${row.key}"] [data-tp-position]`).focus()};
  $('tpRows').onclick=e=>{const b=e.target.closest('[data-tp-remove]');if(!b||editor.saving)return;editor.rows=collectTeamPolicyRows(editor).filter(r=>r.key!==Number(b.dataset.tpRemove));b.closest('[data-tp-row]').remove()};
  $('teamPolicyForm').onsubmit=async e=>{
    e.preventDefault();if(editor.saving||LIVE_BUSY)return;
    editor.rows=collectTeamPolicyRows(editor);const reason=$('tpReason').value.trim(),error=validateTeamPolicyRows(editor.rows,reason);
    if(error){$('tpError').textContent=error;return}
    const changed=editor.rows.filter(r=>!r.existing||r.scope!==r.originalScope);
    if(!changed.length){$('tpError').textContent='변경한 권한이 없습니다.';return}
    editor.saving=true;LIVE_BUSY=true;let committed=false;
    const controls=[...$('sheetBody').querySelectorAll('button,input,select,textarea')];controls.forEach(n=>n.disabled=true);$('tpError').textContent='저장 중입니다…';
    try{
      const result=await liveApi('/api/admin/policies/batch',{team_id:id,policies:changed.map(r=>({position_code:r.position,search_scope:r.scope,version:r.version})),reason});
      committed=true;const wasOpen=$('sheet').classList.contains('open');await liveRefresh();
      if(wasOpen&&teamPolicyEditor===editor)editTeamPolicy(id);
      toast(`${result.saved}개 직책 정책을 저장했습니다. 해당 팀·직책의 다음 검색부터 적용됩니다.`);
    }catch(error){const message=committed?'정책 저장은 완료됐지만 목록을 불러오지 못했습니다. 새로고침해 확인하세요.':error.message;if(teamPolicyEditor===editor&&$('tpError'))$('tpError').textContent=message;else toast(message)}
    finally{editor.saving=false;LIVE_BUSY=false;controls.forEach(n=>{if(document.contains(n))n.disabled=false})}
  };
  $('sheet').classList.add('open');$('sheet').setAttribute('aria-hidden','false');$('sheetBody').querySelector('[data-tp-scope]')?.focus();
};

openTeamSheet=function(id){
  sheetReturn=document.activeElement;const team=LIVE_DB.teams.find(t=>t.team_id===id);if(!team)return;
  const rows=LIVE_DB.policies.filter(p=>p.team_id===id);
  $('sheetBody').innerHTML=`<h3 id="sheetTitle">🏢 ${esc(team.team_name)}<button class="x" type="button" data-sheet-close aria-label="닫기">×</button></h3><dl class="dl"><dt>팀 코드</dt><dd>${esc(id)}</dd><dt>사용 여부</dt><dd>${team.is_active?'사용':'미사용'}</dd><dt>직원 수</dt><dd>${EMPLOYEES.filter(e=>e.team_id===id).length}명</dd></dl><div><h4>저장된 직책별 정책</h4><table class="roleGrid"><thead><tr><th>직책</th><th>검색 범위</th><th>복원 설정</th></tr></thead><tbody>${rows.map(p=>`<tr><td>${esc(p.position_code)}</td><td>${esc(p.search_scope)}</td><td>${p.can_restore?'허용(기능 미연결)':'거부'}</td></tr>`).join('')||'<tr><td colspan="3">등록된 정책 없음 · 검색 차단</td></tr>'}</tbody></table></div><p>권한 설정에서 팀장·과장·사원 등 직책별 검색 범위를 수정할 수 있습니다. 정책이 없는 직책은 검색이 차단됩니다.</p><div class="sheetActs"><button class="ghostBtn" data-sheet-close>닫기</button><button class="primaryBtn" id="sheetPolicy">이 팀 권한 설정</button></div>`;
  $('sheetPolicy').onclick=()=>editTeamPolicy(id);$('sheet').classList.add('open');$('sheet').setAttribute('aria-hidden','false');$('sheetBody').querySelector('.x').focus();
};
createTeam=async function(){
  const id=nextTeamId();const r=await ask({title:'새 팀 만들기 · 팀 정보',yes:'팀 저장',text:`팀 코드 <b>${esc(id)}</b>로 기본 정보만 DB에 등록합니다. 팀·직책 정책은 자동 생성하지 않습니다.`,fields:'<label for="fTeamName">팀 이름</label><input id="fTeamName" name="team_name" maxlength="100">'+reasonField('팀 생성 사유'),check:v=>!v.team_name?'팀 이름을 입력하세요.':v.reason.length<3?'사유를 3자 이상 입력하세요.':''});
  if(!r)return;try{await liveSave('teams',id,{team_name:r.team_name,is_active:true},r.reason,null);toast('팀을 DB에 등록했습니다. 정책은 아직 없습니다.')}catch(e){toast(e.message)}
};
linkEmployee=async function(e){
  const options=MEMBERS.map(m=>`<option value="${m.id}">${esc(m.login)} · ${esc(m.employee_id||'직원 연결 없음')}</option>`).join('');
  const r=await ask({title:'직원과 계정 연결',yes:'연결 저장',text:`사번 <b>${esc(e.id)}</b>와 계정을 연결합니다. 계정 상태·직원 확인 상태는 자동 변경하지 않습니다.`,fields:`<label>계정</label><select name="member">${options}</select>`+reasonField('연결 근거'),check:v=>v.reason.length>=3?'':'사유를 3자 이상 입력하세요.'});
  if(!r)return;const m=MEMBERS.find(m=>m.id===Number(r.member));try{await liveSave('users',m.id,{employee_id:e.id},r.reason,m.db.updated_at);toast('직원과 계정을 DB에서 연결했습니다.')}catch(error){toast(error.message)}
};
// 직원 카드의 '연결 해제': 일반 계정은 DB 규칙상 직원 연결이 필수라 끊지 않고, 계정 퇴사 처리로 안내합니다.
unlinkEmployee=function(e){const m=e&&linkedMember(e);if(!m)return toast('연결된 계정이 없어요.');act('retire',m.id)};
saveFix=dSuggest=dKeep=dEdit=dBulk=function(){unlinked('탐지 수정 결과를 저장·재가명처리하는 기준이 없어 연결을 보류했습니다.')};
const originalBindDetect=bindDetect;
bindDetect=function(){originalBindDetect();if($('dSource'))$('dSource').textContent='실제 탐지 검토 데이터 연결 보류 · 예시 후보를 불러오지 않습니다.';if($('dList')&&!DETECTION_REVIEW.length)$('dList').textContent='검토 데이터와 수정 반영 기준을 맞춘 뒤 연결할 예정입니다.';for(const b of $('dStats').querySelectorAll('b'))b.textContent='미연결';$('dNote').textContent='검토 데이터와 수정 결과 저장·재가명처리·재색인 기준은 연결 보류입니다.';const note=$('view').querySelector('.readonly');if(note)note.textContent='원문 보기와 탐지 수정 기능은 연결하지 않았습니다. 예시 문서나 원문은 불러오지 않습니다.'};
exportFixes=function(){unlinked('탐지 수정 저장 기능을 연결한 뒤 내보내기를 제공합니다.')};
// ── 문서 담당자·팀 확정 (관리자 연결 4단계) ─────────────────────────────────
// 기존 저장 API(/api/admin/save, kind=documents)를 그대로 씁니다. 서버가 '확정 = 확정된 담당 직원 + 그 직원의 사용 중인 팀' 조건,
// 동시 수정 충돌(version), 변경 이력을 처리합니다. 문서 본문·개인정보·vault 는 이 화면에서 다루지 않습니다.
const DOC_STATUS={PENDING:'미확정',CONFIRMED:'확정',REVIEW_REQUIRED:'검토 필요'};
const DOC_STATUS_CLASS={PENDING:'st-EMPLOYEE_CANDIDATE',CONFIRMED:'st-OFFICIAL',REVIEW_REQUIRED:'st-UNRESOLVED'};
const docState={q:'',team:'',type:'',status:'',sel:new Set()};
const liveEmp=id=>(LIVE_DB?.employees||[]).find(e=>e.employee_id===id);
const liveTeam=id=>(LIVE_DB?.teams||[]).find(t=>t.team_id===id);
const posRank=p=>{const i=POSITION_ORDER.indexOf(p);return i<0?99:i};
function docTeamLabel(id){if(!id)return '<span class="linkTag none">미지정</span>';const t=liveTeam(id);return esc(t?t.team_name:id)+(t&&!t.is_active?' <small>(미사용)</small>':'')}
function docOwnerLabel(id){if(!id)return '<span class="linkTag none">미지정</span>';const e=liveEmp(id);return `<b>${esc(id)}</b>${e?.position_code?` <small style="color:var(--ink3)">${esc(e.position_code)}</small>`:''}`}
// 확정 조건(서버와 같은 기준): 사용 중인 팀 + 그 팀에 소속된 확정 직원
function docConfirmable(teamId,ownerId){const e=liveEmp(ownerId),t=liveTeam(teamId);return !!(t?.is_active&&e&&e.verification_status==='CONFIRMED'&&e.team_id===teamId)}
// 확정됐지만 지금 기준으로는 조건이 깨진 문서 (담당자가 팀을 옮겼거나 미확정이 됐거나 팀이 미사용)
const docMismatch=d=>d.metadata_status==='CONFIRMED'&&!docConfirmable(d.team_id,d.owner_employee_id);
// 확정 문서인데 담당 직원의 계정이 퇴사·정지 상태
const docInactiveOwner=d=>{if(d.metadata_status!=='CONFIRMED')return false;const m=MEMBERS.find(x=>x.employee_id===d.owner_employee_id);return !!m&&['RETIRED','LOCKED'].includes(m.db.status)};
const DOC_FILTER={NO_OWNER:d=>!d.owner_employee_id||!d.team_id,MISMATCH:docMismatch,INACTIVE_OWNER:docInactiveOwner};
function filteredDocs(){
  const q=docState.q.trim().toLowerCase();
  return (LIVE_DB?.documents||[]).filter(d=>
    (!docState.status||(DOC_FILTER[docState.status]?DOC_FILTER[docState.status](d):d.metadata_status===docState.status))
    &&(!docState.team||(docState.team==='NONE'?!d.team_id:d.team_id===docState.team))
    &&(!docState.type||d.document_type===docState.type)
    &&(!q||[d.document_id,d.document_type,d.owner_employee_id].some(v=>String(v||'').toLowerCase().includes(q))));
}
const DOC_ICON={
  '':'<svg viewBox="0 0 24 24" fill="none" stroke="#A84A12" stroke-width="2"><path d="M6 3h8l4 4v14H6z"/><path d="M9 12h6M9 16h4"/></svg>',
  CONFIRMED:'<svg viewBox="0 0 24 24" fill="none" stroke="#5B8C5A" stroke-width="2.2"><circle cx="12" cy="12" r="9"/><path d="m8 12.5 3 3 5-6"/></svg>',
  PENDING:'<svg viewBox="0 0 24 24" fill="none" stroke="#C9671A" stroke-width="2.2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2" stroke-linecap="round"/></svg>',
  REVIEW_REQUIRED:'<svg viewBox="0 0 24 24" fill="none" stroke="#B43B3B" stroke-width="2.2"><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5" stroke-linecap="round"/></svg>',
  NO_OWNER:'<svg viewBox="0 0 24 24" fill="none" stroke="#8A6A48" stroke-width="2"><circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.5 2.7-5.5 6-5.5 1.3 0 2.5.3 3.4.8M16 15l5 5M21 15l-5 5"/></svg>',
  MISMATCH:'<svg viewBox="0 0 24 24" fill="none" stroke="#B43B3B" stroke-width="2.2" stroke-linejoin="round"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17v.5" stroke-linecap="round"/></svg>'};
const DOC_FILTER_LABEL={MISMATCH:'담당 불일치',INACTIVE_OWNER:'퇴사·정지 직원 담당',NO_OWNER:'담당 미지정'};
function drawDocumentsPage(){
  const docs=LIVE_DB?.documents||[],teams=LIVE_DB?.teams||[];
  const types=[...new Set(docs.map(d=>d.document_type).filter(Boolean))].sort();
  const n={'':docs.length,NO_OWNER:docs.filter(DOC_FILTER.NO_OWNER).length,MISMATCH:docs.filter(docMismatch).length};
  for(const k of Object.keys(DOC_STATUS))n[k]=docs.filter(d=>d.metadata_status===k).length;
  const tile=(key,label)=>`<button type="button" class="stile" data-dtile="${key}" aria-pressed="${docState.status===key}">${DOC_ICON[key]}<span class="n">${n[key]}</span><span class="l">${label}</span></button>`;
  // 타일에 없는 필터(예: 퇴사·정지 직원 담당)로 들어왔을 때 안내
  const extra=docState.status&&!(docState.status in n)?`<p class="dnote">🔎 지금 <b>${esc(DOC_FILTER_LABEL[docState.status]||docState.status)}</b> 문서만 보고 있어요. <button type="button" class="linkBtn" data-dtile="${esc(docState.status)}">필터 해제</button></p>`:'';
  $('view').innerHTML=`<div class="pageHead"><div class="crumb">관리자센터 · 문서 관리</div><h1>문서 관리</h1>
    <p>문서마다 <b>담당 팀</b>과 <b>담당 직원</b>을 정하고 확정합니다. <b>확정된 문서만</b> 챗봇에서 검색되며, 누가 볼 수 있는지는 팀·직책 정책으로 정해집니다. 문서 본문과 개인정보는 이 화면에서 다루지 않습니다.</p></div>
    <div class="stiles docTiles">${tile('','전체 문서')}${tile('CONFIRMED','확정 (검색됨)')}${tile('PENDING','미확정')}${tile('REVIEW_REQUIRED','검토 필요')}${tile('NO_OWNER','담당 미지정')}${tile('MISMATCH','담당 불일치')}</div>${extra}
    <div class="bar3">
      <input id="docQ" type="search" placeholder="문서 ID·문서 유형·담당 사번으로 검색" aria-label="문서 검색" value="${esc(docState.q)}">
      <select id="docTeam" aria-label="담당 팀"><option value="">모든 팀</option><option value="NONE">팀 미지정</option>${teams.map(t=>`<option value="${esc(t.team_id)}">${esc(t.team_name)} (${esc(t.team_id)})</option>`).join('')}</select>
      <select id="docType" aria-label="문서 유형"><option value="">모든 문서 유형</option>${types.map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join('')}</select>
      <button type="button" class="ghostBtn" id="docExport">↓ 내보내기</button>
      <button type="button" class="ghostBtn" id="docImport" title="팀원이 채운 문서분류 엑셀을 CSV로 저장해 올리면 미리 보기 후 반영합니다">↑ 분류 결과 반영 (CSV)</button>
      <input type="file" id="docImportFile" accept=".csv,text/csv" hidden>
    </div>
    <div class="actions" style="align-items:center;flex-wrap:wrap">
      <span id="docSelInfo" style="color:var(--ink2);font-size:14px;margin-right:auto"></span>
      <button type="button" class="ghostBtn" id="docBulkReview">선택 문서 검토 필요로</button>
      <button type="button" class="primaryBtn" id="docBulkConfirm">선택 문서 확정</button>
    </div>
    <div class="etable"><div class="tableWrap"><table><thead><tr>
      <th style="width:44px"><input type="checkbox" id="docAll" aria-label="보이는 문서 모두 선택"></th>
      <th>문서 ID</th><th>문서 유형</th><th>담당 팀</th><th>담당 직원</th><th>문서 상태</th><th>마지막 수정</th><th></th></tr></thead>
      <tbody id="docBody"></tbody></table></div></div>
    <p class="pager2"><span id="docCount"></span><span>행을 누르면 담당 팀·직원·상태를 바꿀 수 있어요.</span></p>`;
  $('docTeam').value=docState.team;$('docType').value=docState.type;
  $('docQ').oninput=e=>{docState.q=e.target.value;drawDocRows()};
  $('docTeam').onchange=e=>{docState.team=e.target.value;drawDocRows()};
  $('docType').onchange=e=>{docState.type=e.target.value;drawDocRows()};
  $('docExport').onclick=exportDocuments;
  $('docImport').onclick=()=>$('docImportFile').click();
  $('docImportFile').onchange=e=>{const f=e.target.files[0];e.target.value='';if(f)importDocumentCsv(f)};
  $('docAll').onchange=e=>{for(const d of filteredDocs())e.target.checked?docState.sel.add(d.document_id):docState.sel.delete(d.document_id);drawDocRows()};
  $('docBulkConfirm').onclick=()=>bulkDocStatus('CONFIRMED');
  $('docBulkReview').onclick=()=>bulkDocStatus('REVIEW_REQUIRED');
  $('view').onclick=e=>{
    const t=e.target.closest('[data-dtile]');
    if(t){docState.status=docState.status===t.dataset.dtile?'':t.dataset.dtile;return drawDocumentsPage()}
    const c=e.target.closest('[data-doc-check]');
    if(c){c.checked?docState.sel.add(c.dataset.docCheck):docState.sel.delete(c.dataset.docCheck);return drawDocRows()}
    const tr=e.target.closest('tr[data-doc]');
    if(tr)openDocumentForm((LIVE_DB?.documents||[]).find(d=>d.document_id===tr.dataset.doc));
  };
  drawDocRows();
}
function drawDocRows(){
  const rows=filteredDocs();
  $('docBody').innerHTML=rows.length?rows.map(d=>{const on=docState.sel.has(d.document_id);return `<tr data-doc="${esc(d.document_id)}"${on?' class="sel"':''}>
    <td><input type="checkbox" data-doc-check="${esc(d.document_id)}"${on?' checked':''} aria-label="${esc(d.document_id)} 선택"></td>
    <td><b>${esc(d.document_id)}</b></td><td>${esc(d.document_type||'—')}</td><td>${docTeamLabel(d.team_id)}</td><td>${docOwnerLabel(d.owner_employee_id)}</td>
    <td><span class="st ${DOC_STATUS_CLASS[d.metadata_status]||''}">${esc(DOC_STATUS[d.metadata_status]||d.metadata_status)}</span>${docMismatch(d)?' <span class="diffMark" style="background:var(--err-bg);color:var(--err)" title="담당 직원의 팀·확인 상태가 문서와 맞지 않아요">담당 불일치</span>':''}${docInactiveOwner(d)?' <span class="diffMark" title="담당 직원 계정이 퇴사·정지 상태예요">담당자 계정 중지</span>':''}</td>
    <td>${d.updated_at?ymd(new Date(d.updated_at)):'—'}</td>
    <td><button type="button" class="editBtn" data-doc-edit="${esc(d.document_id)}">수정</button></td></tr>`}).join('')
    :`<tr><td colspan="8" class="emptyRow">조건에 맞는 문서가 없어요.</td></tr>`;
  $('docCount').textContent=`${rows.length}건 표시 · 전체 ${(LIVE_DB?.documents||[]).length}건`;
  const shown=rows.filter(d=>docState.sel.has(d.document_id)).length;
  $('docAll').checked=rows.length>0&&shown===rows.length;$('docAll').indeterminate=shown>0&&shown<rows.length;
  $('docSelInfo').textContent=docState.sel.size?`${docState.sel.size}건 선택됨`:'문서를 체크하면 여러 건을 한 번에 확정하거나 검토 필요로 바꿀 수 있어요.';
  $('docBulkConfirm').disabled=$('docBulkReview').disabled=!docState.sel.size;
}
// 담당 직원 후보: 고른 팀에 소속된 '확정' 직원만 (서버 확정 조건과 같음)
function docOwnerOptions(teamId,current){
  const list=(LIVE_DB?.employees||[]).filter(e=>teamId&&e.team_id===teamId&&e.verification_status==='CONFIRMED')
    .sort((a,b)=>posRank(a.position_code)-posRank(b.position_code)||a.employee_id.localeCompare(b.employee_id));
  let html='<option value="">담당 직원 없음</option>'+list.map(e=>`<option value="${esc(e.employee_id)}"${e.employee_id===current?' selected':''}>${esc(e.employee_id)} · ${esc(e.position_code||'직책 없음')}</option>`).join('');
  if(current&&!list.some(e=>e.employee_id===current))html+=`<option value="${esc(current)}" selected>${esc(current)} (이 팀의 확정 직원 아님)</option>`;
  return html;
}
function documentFields(d){
  const teams=(LIVE_DB?.teams||[]).filter(t=>t.is_active||t.team_id===d.team_id);
  return `<p class="fhint" style="margin:0 0 8px">문서 <b>${esc(d.document_id)}</b> · ${esc(d.document_type||'유형 없음')}</p>`
    +`<label for="fDocTeam">담당 팀</label><select id="fDocTeam" name="team_id"><option value="">팀 없음</option>${teams.map(t=>`<option value="${esc(t.team_id)}"${t.team_id===d.team_id?' selected':''}>${esc(t.team_name)} (${esc(t.team_id)})${t.is_active?'':' · 미사용'}</option>`).join('')}</select>`
    +`<label for="fDocOwner">담당 직원</label><select id="fDocOwner" name="owner_employee_id"></select><p class="fhint" id="fDocOwnerHint"></p>`
    +`<label for="fDocStatus">문서 상태</label><select id="fDocStatus" name="metadata_status">${Object.entries(DOC_STATUS).map(([k,v])=>`<option value="${k}"${k===d.metadata_status?' selected':''}>${v}</option>`).join('')}</select>`
    +`<div class="warnBox" id="fDocImpact" style="margin:8px 0 10px"></div>`
    +reasonField('예: 문서 내용 확인 후 영업1팀 카드발급 담당자로 지정');
}
function documentChanges(v,d){
  const next={team_id:v.team_id||null,owner_employee_id:v.owner_employee_id||null,metadata_status:v.metadata_status};
  return Object.fromEntries(Object.entries(next).filter(([k,val])=>(d[k]??null)!==val));
}
function checkDocument(v,d){
  if(v.owner_employee_id&&!v.team_id)return '담당 직원을 고르려면 담당 팀을 먼저 고르세요.';
  if(v.metadata_status==='CONFIRMED'){
    if(!v.team_id||!v.owner_employee_id)return '확정하려면 담당 팀과 담당 직원이 모두 필요합니다.';
    if(!liveTeam(v.team_id)?.is_active)return '사용 중인 팀만 확정할 수 있어요.';
    if(!docConfirmable(v.team_id,v.owner_employee_id))return '담당 직원은 이 팀에 소속된 확정 직원이어야 합니다.';
  }
  if(!Object.keys(documentChanges(v,d)).length)return '바뀐 내용이 없습니다.';
  if((v.reason||'').length<3)return '사유를 3자 이상 적어 주세요. 활동 기록에 남아요.';
  return '';
}
// 확정했을 때 누가 검색할 수 있는지 미리 보여 줍니다 (allowed_documents 와 같은 규칙: 팀·직책 정책 TEAM=팀 전체, OWN=본인 담당)
function docImpact(teamId,ownerId,status){
  if(status!=='CONFIRMED')return `🔒 <b>${DOC_STATUS[status]||status}</b> 상태에서는 아무도 이 문서를 챗봇에서 검색할 수 없어요.`;
  if(!teamId||!ownerId)return '확정하려면 담당 팀과 담당 직원을 모두 골라 주세요.';
  const pol=(LIVE_DB?.policies||[]).filter(p=>p.team_id===teamId);
  const scope=pos=>pol.find(p=>p.position_code===pos)?.search_scope||'NONE';
  const viewers=(LIVE_DB?.employees||[]).filter(e=>e.team_id===teamId&&e.verification_status==='CONFIRMED'
    &&(scope(e.position_code)==='TEAM'||(scope(e.position_code)==='OWN'&&e.employee_id===ownerId)));
  const teamPos=pol.filter(p=>p.search_scope==='TEAM').map(p=>p.position_code).sort((a,b)=>posRank(a)-posRank(b));
  const owner=liveEmp(ownerId),ownerScope=owner?scope(owner.position_code):'NONE',parts=[];
  if(teamPos.length)parts.push(`${esc(liveTeam(teamId)?.team_name||teamId)} <b>${teamPos.map(esc).join('·')}</b> (팀 전체 문서 정책)`);
  if(ownerScope==='OWN')parts.push(`담당 직원 <b>${esc(ownerId)}</b> 본인`);
  let html=`🔍 확정하면 검색 가능: ${parts.join(' + ')||'없음'} → 직원 <b>${viewers.length}명</b>`;
  if(ownerScope==='NONE')html+=`<br>⚠️ 담당 직원의 직책 정책이 '검색 불가'라 담당자 본인은 이 문서를 검색할 수 없어요.`;
  return html+'<br><small>계정이 있는 직원만 실제로 로그인해 검색할 수 있어요.</small>';
}
async function openDocumentForm(d){
  if(!d)return;
  if(!LIVE_READY)return toast('DB 목록을 불러온 뒤 다시 시도하세요.');
  if(LIVE_BUSY)return toast('저장 중입니다. 잠시 기다려 주세요.');
  const pending=ask({title:`문서 담당·확정 · ${d.document_id}`,yes:'저장',
    text:'담당 팀·담당 직원·문서 상태를 바꿉니다. <b>확정</b>하면 그 팀의 검색 정책에 따라 챗봇에서 검색됩니다. 바뀐 항목만 저장하고 활동 기록에 남깁니다.',
    fields:documentFields(d),check:v=>checkDocument(v,d)});
  // ask()가 입력 칸을 바로 그려 두므로, 팀을 바꾸면 그 팀의 확정 직원으로 담당자 목록을 다시 채웁니다.
  const team=$('fDocTeam'),owner=$('fDocOwner'),status=$('fDocStatus'),hint=$('fDocOwnerHint'),impact=$('fDocImpact');
  const showImpact=()=>{impact.innerHTML=docImpact(team.value,owner.value,status.value)};
  const sync=()=>{
    const prev=owner.value,orig=team.value===(d.team_id||'')?d.owner_employee_id:'';
    owner.innerHTML=docOwnerOptions(team.value,orig);
    if([...owner.options].some(o=>o.value===prev&&prev))owner.value=prev;
    owner.disabled=!team.value;
    hint.textContent=!team.value?'팀을 고르면 그 팀의 확정 직원이 담당자 후보로 나옵니다.'
      :owner.options.length>1?'담당자 후보는 이 팀에 소속된 확정 직원입니다.':'이 팀에는 확정된 직원이 없어요. 팀 관리에서 직원을 먼저 확정하세요.';
    showImpact();
  };
  team.onchange=sync;owner.onchange=status.onchange=showImpact;sync();
  const r=await pending;if(!r)return;
  try{
    await liveSave('documents',d.document_id,documentChanges(r,d),r.reason,d.updated_at);
    toast(`문서 ${d.document_id}을(를) 저장했습니다.`);
  }catch(error){toast(error.message)}
}
// 여러 문서의 상태만 한 번에 바꿉니다. 문서마다 기존 저장 API를 차례로 호출하고(각각 버전 확인·이력 기록), 끝나면 한 번만 새로 불러옵니다.
async function bulkDocStatus(target){
  if(!LIVE_READY)return toast('DB 목록을 불러온 뒤 다시 시도하세요.');
  if(LIVE_BUSY)return toast('저장 중입니다. 잠시 기다려 주세요.');
  const docs=(LIVE_DB?.documents||[]).filter(d=>docState.sel.has(d.document_id));
  const todo=docs.filter(d=>d.metadata_status!==target);
  const blocked=target==='CONFIRMED'?todo.filter(d=>!docConfirmable(d.team_id,d.owner_employee_id)):[];
  const ok=todo.filter(d=>!blocked.includes(d)),label=DOC_STATUS[target];
  if(!ok.length)return toast(blocked.length?`담당 팀·직원이 맞지 않아 확정할 수 없는 문서뿐이에요 (${blocked.length}건). 하나씩 수정해 주세요.`:`선택한 문서는 이미 모두 ${label} 상태예요.`);
  const r=await ask({title:`선택 문서 ${label} 처리`,yes:`${ok.length}건 ${label}`,danger:target!=='CONFIRMED',
    text:`선택한 ${docs.length}건 중 <b>${ok.length}건</b>을 <b>${label}</b>(으)로 바꿉니다.`
      +(docs.length-todo.length?`<br>이미 ${label} 상태라 건너뜀: ${docs.length-todo.length}건`:'')
      +(blocked.length?`<br>⚠️ 담당 팀·직원이 맞지 않아 건너뜀: ${blocked.slice(0,5).map(d=>esc(d.document_id)).join(', ')}${blocked.length>5?' 외':''} (${blocked.length}건)`:'')
      +(target==='CONFIRMED'?'<br>확정한 문서는 바로 챗봇 검색 대상이 됩니다.':'<br>검토 필요로 바꾼 문서는 <b>챗봇 검색에서 빠집니다.</b>')
      +'<br>문서마다 따로 저장하고 활동 기록을 남깁니다.',
    fields:reasonField(target==='CONFIRMED'?'예: 담당 배정표 검토 완료':'예: 담당 팀 재확인 필요'),
    check:v=>(v.reason||'').length<3?'사유를 3자 이상 적어 주세요. 활동 기록에 남아요.':''});
  if(!r)return;
  LIVE_BUSY=true;let done=0;
  try{
    for(const d of ok){await liveApi('/api/admin/save',{kind:'documents',key:d.document_id,values:{metadata_status:target},reason:r.reason,version:d.updated_at});done++}
    toast(`${done}건을 ${label}(으)로 저장했습니다.`);
  }catch(error){toast(`${done}건 저장 후 멈췄어요: ${error.message}`)}
  finally{LIVE_BUSY=false;docState.sel.clear();try{await liveRefresh()}catch(error){toast(error.message)}}
}
// ── 운영 상세 점검: DB에서 불러온 목록만으로 화면에서 계산합니다 (서버 수정 없음) ─────────────
// 검색 횟수·오류·보안 경고·백업·LLM 상태처럼 서버가 따로 모아야 하는 값은 여기서 만들지 않습니다.
function dataChecks(){
  const D=LIVE_DB||{},emps=D.employees||[],docs=D.documents||[],teams=D.teams||[];
  const conf=emps.filter(e=>e.verification_status==='CONFIRMED');
  const isActiveTeam=id=>!!teams.find(t=>t.team_id===id)?.is_active;
  const empOf=m=>emps.find(e=>e.employee_id===m.employee_id);
  const noPolicy=conf.filter(e=>e.team_id&&isActiveTeam(e.team_id)&&!livePolicy(e.team_id,e.position_code));
  const blocked=MEMBERS.filter(m=>m.db.status==='ACTIVE'&&m.role!=='ADMIN'&&(e=>scopeFor(e?.team_id,e?.position_code,e?.verification_status)==='NONE')(empOf(m)));
  const mismatch=docs.filter(docMismatch),inactiveOwner=docs.filter(docInactiveOwner);
  const pending=docs.filter(d=>d.metadata_status==='PENDING'),review=docs.filter(d=>d.metadata_status==='REVIEW_REQUIRED');
  const unconf=emps.filter(e=>e.verification_status!=='CONFIRMED');
  const noAccount=conf.filter(e=>!MEMBERS.some(m=>m.employee_id===e.employee_id));
  const emptyTeams=teams.filter(t=>t.is_active&&!conf.some(e=>e.team_id===t.team_id));
  const deadTeams=teams.filter(t=>!t.is_active&&emps.some(e=>e.team_id===t.team_id));
  const ids=(list,key)=>list.map(x=>typeof key==='function'?key(x):x[key]);
  return [
    {level:'bad',title:'담당 불일치 확정 문서',n:mismatch.length,ids:ids(mismatch,'document_id'),btn:'문서 보기',go:()=>openDocuments({status:'MISMATCH'}),
      desc:'담당 직원이 다른 팀으로 옮겼거나, 확정 직원이 아니거나, 팀이 미사용인데 확정된 문서예요. 검색 권한이 의도와 다르게 적용될 수 있어요.'},
    {level:'warn',title:'로그인은 되지만 검색할 수 없는 계정',n:blocked.length,ids:ids(blocked,'login'),btn:'회원 관리',go:()=>{location.hash='#members'},
      desc:'직원이 미확정이거나, 팀이 미사용이거나, 팀·직책 정책이 없거나 검색 불가예요. 챗봇에서 "볼 수 있는 문서가 없어요"가 나와요.'},
    {level:'warn',title:'검색 정책이 없는 확정 직원',n:noPolicy.length,ids:ids(noPolicy,e=>`${e.employee_id}(${TEAMS[e.team_id]||e.team_id} ${e.position_code||'직책 없음'})`),btn:'팀 관리',go:()=>{location.hash='#teams'},
      desc:'팀·직책 조합의 정책이 없어 검색할 수 없어요. 팀 목록 → 권한 설정에서 직책을 추가하세요.'},
    {level:'warn',title:'퇴사·정지 계정 직원이 담당한 확정 문서',n:inactiveOwner.length,ids:ids(inactiveOwner,'document_id'),btn:'문서 보기',go:()=>openDocuments({status:'INACTIVE_OWNER'}),
      desc:'같은 팀 직원은 계속 검색할 수 있어요. 담당자 교체가 필요한지 확인하세요.'},
    {level:'warn',title:'미사용 팀에 남아 있는 직원',n:deadTeams.reduce((s,t)=>s+emps.filter(e=>e.team_id===t.team_id).length,0),ids:ids(deadTeams,'team_name'),btn:'팀 관리',go:()=>{location.hash='#teams'},
      desc:'미사용 팀 소속 직원은 검색할 수 없어요. 다른 팀으로 옮기거나 팀을 다시 사용하세요.'},
    {level:'info',title:'미확정 문서 (검색 안 됨)',n:pending.length,ids:ids(pending,'document_id'),btn:'문서 보기',go:()=>openDocuments({status:'PENDING'}),
      desc:'담당 팀·직원을 정하고 확정해야 챗봇에서 검색돼요.'},
    {level:'info',title:'검토 필요 문서 (검색 안 됨)',n:review.length,ids:ids(review,'document_id'),btn:'문서 보기',go:()=>openDocuments({status:'REVIEW_REQUIRED'}),
      desc:'검토가 끝나면 확정으로 바꿔 주세요.'},
    {level:'info',title:'확인 전·검토 필요 직원',n:unconf.length,ids:ids(unconf,'employee_id'),btn:'팀 관리',go:()=>{location.hash='#teams'},
      desc:'직원 확정 전에는 검색할 수 없고, 문서 담당자로 지정할 수도 없어요.'},
    {level:'info',title:'계정이 없는 확정 직원',n:noAccount.length,ids:ids(noAccount,'employee_id'),btn:'회원 초대',go:()=>{location.hash='#members'},
      desc:'회원 초대로 계정을 발급해야 챗봇을 쓸 수 있어요.'},
    {level:'info',title:'확정 직원이 없는 사용 중 팀',n:emptyTeams.length,ids:ids(emptyTeams,'team_name'),btn:'팀 관리',go:()=>{location.hash='#teams'},
      desc:'이 팀의 문서는 검색할 사람이 없어요.'},
  ];
}
function teamStatusRows(){
  const D=LIVE_DB||{},emps=D.employees||[],docs=D.documents||[];
  return (D.teams||[]).map(t=>{
    const staff=emps.filter(e=>e.team_id===t.team_id&&e.verification_status==='CONFIRMED');
    const accts=MEMBERS.filter(m=>m.db.status==='ACTIVE'&&staff.some(e=>e.employee_id===m.employee_id));
    const can=accts.filter(m=>{const e=staff.find(x=>x.employee_id===m.employee_id);return scopeFor(e.team_id,e.position_code,e.verification_status)!=='NONE'});
    const td=docs.filter(d=>d.team_id===t.team_id);
    const pol=(D.policies||[]).filter(p=>p.team_id===t.team_id).sort((a,b)=>posRank(a.position_code)-posRank(b.position_code));
    return {t,staff:staff.length,accts:accts.length,can:can.length,docs:td.length,conf:td.filter(d=>d.metadata_status==='CONFIRMED').length,pol};
  });
}
function drawDataChecks(){
  const host=$('view').querySelector('.hero');if(!host||!LIVE_DB)return;
  $('dataCheckPanel')?.remove();
  const checks=dataChecks(),need=checks.filter(c=>c.level!=='info'&&c.n).length;
  const mark=c=>!c.n?'✅':c.level==='bad'?'⛔':c.level==='warn'?'⚠️':'ℹ️';
  const preview=c=>c.n&&c.ids.length?`<span class="ids">${c.ids.slice(0,6).map(esc).join(', ')}${c.ids.length>6?` 외 ${c.ids.length-6}`:''}</span>`:'';
  const scopeChip=p=>`<span class="pchip ${p.search_scope==='NONE'?'':'scope'}">${esc(p.position_code)} ${esc(SCOPE_TEXT[p.search_scope]||p.search_scope)}</span>`;
  const section=document.createElement('section');section.id='dataCheckPanel';
  section.innerHTML=`<div class="sbar"><h2>권한·데이터 점검 ${need?`<span class="hbadge h-warn">확인 필요 ${need}</span>`:'<span class="hbadge h-ok">이상 없음</span>'}</h2>
      <button type="button" class="ghostBtn" id="dcRefresh">↻ DB 다시 불러오기</button></div>
    <p class="dnote">DB에서 불러온 직원·계정·정책·문서 목록으로 화면에서 계산합니다. 검색 횟수·오류·보안 경고·백업·LLM 상태는 서버 집계가 필요해 아직 미연결입니다.</p>
    <div class="listPanel"><ul class="dchecks">${checks.map((c,i)=>`<li class="lv-${c.n?c.level:'ok'}"><span class="lv" aria-hidden="true">${mark(c)}</span>
      <div><b class="t">${esc(c.title)}</b><span class="cnt">${c.n}</span><p>${esc(c.desc)}</p>${preview(c)}</div>
      ${c.n?`<button type="button" class="ghostBtn" data-check="${i}">${esc(c.btn)} ›</button>`:'<span></span>'}</li>`).join('')}</ul></div>
    <h2 class="sh2">팀별 현황</h2>
    <div class="etable"><div class="tableWrap"><table><thead><tr><th>팀</th><th>확정 직원</th><th>사용 중 계정</th><th>검색 가능 계정</th><th>확정 문서 / 전체</th><th>직책별 검색 범위</th></tr></thead>
      <tbody>${teamStatusRows().map(r=>`<tr><td><b>${esc(r.t.team_name)}</b> <small style="color:var(--ink3)">${esc(r.t.team_id)}${r.t.is_active?'':' · 미사용'}</small></td>
        <td>${r.staff}명</td><td>${r.accts}명</td><td>${r.can?`${r.can}명`:'<span style="color:var(--ink3)">0명</span>'}</td>
        <td>${r.conf} / ${r.docs}건</td><td style="white-space:normal">${r.pol.map(scopeChip).join('')||'<span class="pchip">정책 없음</span>'}</td></tr>`).join('')}</tbody></table></div></div>`;
  host.after(section);
  section.querySelectorAll('[data-check]').forEach(b=>b.onclick=()=>checks[b.dataset.check].go());
  $('dcRefresh').onclick=()=>liveRefresh().then(()=>toast('DB 목록을 새로 불러왔습니다.')).catch(e=>toast(e.message));
}
const originalBindSystem=bindSystem;
bindSystem=function(){originalBindSystem();drawDataChecks()};

// ── 팀원 문서 분류 결과(엑셀 → CSV) 반영 ────────────────────────────────────
// 팀원 작업 2의 '문서분류_날짜.xlsx'를 엑셀에서 'CSV UTF-8'로 저장해 올립니다. (일반 CSV(한글 깨짐 위험)도 자동으로 읽어 봄)
// 열: 문서 번호 · 담당 팀(이름 또는 T03) · 담당 직원 사번 · 확신(확실/애매함). 확실 → 확정, 애매함 → 검토 필요, 빈칸 → 상태 유지.
// 저장은 문서 하나씩 기존 저장 API로 하므로 확정 조건·동시 수정 충돌·이력 기록이 그대로 적용됩니다.
function parseCsv(text){
  const rows=[];let row=[],cell='',q=false;
  text=text.replace(/^﻿/,'');
  for(let i=0;i<text.length;i++){
    const ch=text[i];
    if(q){if(ch==='"'){if(text[i+1]==='"'){cell+='"';i++}else q=false}else cell+=ch;continue}
    if(ch==='"')q=true;else if(ch===',')(row.push(cell),cell='');else if(ch==='\n'||ch==='\r'){if(ch==='\r'&&text[i+1]==='\n')i++;row.push(cell);rows.push(row);row=[];cell=''}else cell+=ch;
  }
  if(cell!==''||row.length){row.push(cell);rows.push(row)}
  return rows.filter(r=>r.some(v=>v.trim()!==''));
}
async function readCsvFile(file){
  const buf=await file.arrayBuffer();
  const utf=new TextDecoder('utf-8').decode(buf);
  return utf.includes('�')?new TextDecoder('euc-kr').decode(buf):utf;   // 엑셀 기본 CSV(CP949)도 읽기
}
function resolveTeam(v){
  v=String(v||'').trim();if(!v)return null;
  const code=(v.match(/\b(T\d+)\b/)||[])[1];
  return (LIVE_DB?.teams||[]).find(t=>t.team_id===(code||v)||t.team_name===v.replace(/\s*\(.*\)\s*$/,''))||undefined;
}
function planDocumentImport(rows){
  const head=rows[0].map(h=>h.trim()),col=names=>head.findIndex(h=>names.includes(h));
  const ci={id:col(['문서 번호','문서 ID','document_id']),team:col(['담당 팀','team_name','담당 팀 코드','team_id']),
            owner:col(['담당 직원 사번','담당 직원','owner_employee_id']),sure:col(['확신'])};
  if(ci.id<0||ci.team<0||ci.owner<0)throw Error('CSV 첫 줄에 "문서 번호 · 담당 팀 · 담당 직원 사번" 열이 있어야 해요. 팀원 작업용 문서분류 엑셀을 그대로 CSV로 저장해 주세요.');
  const docs=new Map((LIVE_DB?.documents||[]).map(d=>[d.document_id,d])),seen=new Set();
  const plan={changes:[],same:0,errors:[],review:0,confirm:0,reassign:0};
  for(const [n,r] of rows.slice(1).entries()){
    const line=n+2,id=(r[ci.id]||'').trim();if(!id)continue;
    const bad=msg=>plan.errors.push(`${line}줄 ${id}: ${msg}`);
    const d=docs.get(id);if(!d){bad('DB에 없는 문서 번호');continue}
    if(seen.has(id)){bad('같은 문서가 두 번 나옴');continue}seen.add(id);
    const team=resolveTeam(r[ci.team]);if(team===undefined){bad(`모르는 팀 "${r[ci.team]}"`);continue}
    const owner=(r[ci.owner]||'').trim()||null,sure=ci.sure<0?'':(r[ci.sure]||'').trim();
    if(owner&&!liveEmp(owner)){bad(`모르는 사번 "${owner}"`);continue}
    const status=sure==='확실'?'CONFIRMED':sure==='애매함'?'REVIEW_REQUIRED':d.metadata_status;
    const v={team_id:team?.team_id||null,owner_employee_id:owner,metadata_status:status};
    if(status==='CONFIRMED'&&!docConfirmable(v.team_id,v.owner_employee_id)){bad('확정하려면 담당 직원이 그 팀의 확정 직원이어야 함 (확신을 "애매함"으로 하거나 담당자를 고쳐 주세요)');continue}
    const ch=Object.fromEntries(Object.entries(v).filter(([k,val])=>(d[k]??null)!==val));
    if(!Object.keys(ch).length){plan.same++;continue}
    if('team_id' in ch||'owner_employee_id' in ch)plan.reassign++;
    if(ch.metadata_status==='CONFIRMED')plan.confirm++;if(ch.metadata_status==='REVIEW_REQUIRED')plan.review++;
    plan.changes.push({d,ch});
  }
  return plan;
}
async function importDocumentCsv(file){
  if(!LIVE_READY)return toast('DB 목록을 불러온 뒤 다시 시도하세요.');
  if(LIVE_BUSY)return toast('저장 중입니다. 잠시 기다려 주세요.');
  let plan;
  try{const rows=parseCsv(await readCsvFile(file));if(rows.length<2)throw Error('CSV에 내용이 없어요.');plan=planDocumentImport(rows)}
  catch(e){return toast(e.message)}
  const errs=plan.errors.length?`<br>⚠️ <b>반영하지 않는 줄 ${plan.errors.length}개</b><ul style="margin:4px 0 0;padding-left:18px;font-size:13px">${plan.errors.slice(0,8).map(x=>`<li>${esc(x)}</li>`).join('')}${plan.errors.length>8?`<li>… 외 ${plan.errors.length-8}개</li>`:''}</ul>`:'';
  if(!plan.changes.length)return ask({title:'분류 결과 반영',yes:'닫기',cancel:false,text:`<b>${esc(file.name)}</b><br>바뀔 내용이 없어요. (변화 없음 ${plan.same}건)${errs}`});
  const r=await ask({title:'분류 결과 반영 · 미리 보기',yes:`${plan.changes.length}건 반영`,
    text:`<b>${esc(file.name)}</b><br>바뀌는 문서 <b>${plan.changes.length}건</b> · 변화 없음 ${plan.same}건`
      +`<br>· 담당 팀·직원 변경 ${plan.reassign}건 · 확정으로 ${plan.confirm}건 · 검토 필요로 ${plan.review}건 <small>(애매함 → 검토 필요 = 챗봇 검색에서 빠짐)</small>`
      +`<br>예: ${plan.changes.slice(0,5).map(x=>esc(x.d.document_id)).join(', ')}${plan.changes.length>5?' …':''}${errs}`
      +'<br>문서마다 따로 저장하고 활동 기록을 남깁니다.',
    fields:reasonField('예: 팀원 문서 분류 결과(문서분류_1124) 반영'),check:v=>(v.reason||'').length<3?'사유를 3자 이상 적어 주세요. 활동 기록에 남아요.':''});
  if(!r)return;
  LIVE_BUSY=true;let done=0;const failed=[];
  try{
    for(const {d,ch} of plan.changes){
      try{await liveApi('/api/admin/save',{kind:'documents',key:d.document_id,values:ch,reason:r.reason,version:d.updated_at});done++}
      catch(e){failed.push(`${d.document_id}: ${e.message}`)}
    }
  }finally{LIVE_BUSY=false;try{await liveRefresh()}catch(e){toast(e.message)}}
  await ask({title:'분류 결과 반영 완료',yes:'닫기',cancel:false,
    text:`저장 ${done}건${failed.length?` · 실패 ${failed.length}건<ul style="margin:4px 0 0;padding-left:18px;font-size:13px">${failed.slice(0,8).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}${plan.errors.length?`<br>처음부터 반영하지 않은 줄 ${plan.errors.length}개는 CSV를 고쳐 다시 올려 주세요.`:''}`});
}

function exportDocuments(){liveCsv([['문서 ID','문서 유형','담당 팀 코드','담당 팀','담당 직원','직책','문서 상태','마지막 수정'],
  ...filteredDocs().map(d=>[d.document_id,d.document_type,d.team_id,liveTeam(d.team_id)?.team_name||'',d.owner_employee_id,liveEmp(d.owner_employee_id)?.position_code||'',DOC_STATUS[d.metadata_status]||d.metadata_status,d.updated_at||''])],'문서목록.csv')}

// CSV는 실제 조회된 값만 내보내며 수식으로 실행될 수 있는 셀을 일반 텍스트로 만듭니다.
function liveCsv(rows,name){const text=rows.map(r=>r.map(v=>{let s=String(v??'');if(/^[\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"'}).join(',')).join('\r\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\ufeff'+text],{type:'text/csv;charset=utf-8'}));a.download=name;a.click();URL.revokeObjectURL(a.href)}
exportPerms=function(){liveCsv([['아이디','역할','팀','직책','검색 범위','복원 설정','계정 DB 상태'],...MEMBERS.map(m=>[m.login,m.role,teamName(m.team),m.position,m.scope,m.restore?'허용(기능 미연결)':'거부',m.db.status])],'회원권한.csv')};
exportEmployees=function(){liveCsv([['사번','팀','직책','DB 확인 상태','연결 계정'],...EMPLOYEES.map(e=>[e.id,teamName(e.team_id),e.position,e.db.verification_status,linkedMember(e)?.login||''])],'직원목록.csv')};
exportAudit=function(){if(aState.tab==='restore')return unlinked('실제 복원 이력 수집 기능은 미연결입니다.');liveCsv([['기록번호','일시','작업자','작업','대상','변경 전','변경 후','사유'],...filteredAudit().map(r=>[r.id,r.at.toISOString(),r.actor,r.action,r.target,r.before,r.after,r.reason])],'활동기록.csv')};
exportRestore=function(){unlinked('실제 복원 이력 수집 기능은 미연결입니다.')};

// ───────── 인물 검토 (지정된 권한자 전용) ─────────
// 값은 모두 textContent로 넣어 HTML로 해석되지 않게 합니다.
let prQueue='person',prItemsData=[],prCurrent=null,prBusy=false;
const PR_REASON={NO_CANDIDATE:'후보 없음',NAME_PHONE_MATCH:'이름+전화 일치',RRN_MATCH:'이름+주민번호 일치',NAME_ONLY:'이름만 일치',PHONE_DIFFERENT:'이름 같고 전화 다름',MULTIPLE_MATCHES:'후보 여러 개',RRN_CONFLICT:'주민번호 충돌',IDENTITY_CONFLICT:'근거가 다른 PID를 가리킴',OTHER_SHARED_VALUE:'이름 외 정보만 겹침',LOCAL_IDENTITY_AMBIGUOUS:'문서 안 동명이인 의심',OWNER_AMBIGUOUS:'주인 특정 불가',TOKEN_MAPPING_MISSING:'토큰 대응 없음',TOKEN_MAPPING_CONFLICT:'토큰 대응 충돌',POLICY_INSUFFICIENT:'연결 근거 부족',ROLE_CONFLICT:'역할 다름',NAME_REQUIRED:'이름 없음',ALREADY_LINKED:'기존 연결',MANUAL_LINK:'수동 · PID 연결',MANUAL_CREATE:'수동 · 새 PID',MANUAL_ASSIGN:'수동 · 정보 귀속',MANUAL_SHARED:'수동 · 공유 값',MANUAL_DISMISS:'수동 · 제외',MANUAL_UNDONE:'수동 처리 되돌림'};
const PR_STATUS={held:['보류','held'],linked:['자동 연결','done'],created:['자동 생성','done'],manual_linked:['수동 연결','done'],manual_created:['수동 생성','done'],shared:['공유 값',''],dismissed:['제외','']};
const prEl=(tag,cls,value)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(value!==undefined)n.textContent=value;return n};
function prAllowed(){return !!(LIVE_ME&&LIVE_ME.can_review_persons)}
async function prLoad(){
  $('prErr').textContent='';
  try{
    const [s,list]=await Promise.all([liveApi('/api/admin/person-review/summary'),liveApi('/api/admin/person-review/items?queue='+prQueue)]);
    if(!document.contains($('prItems')))return;
    $('prHeld').textContent=num(s.person_held);$('prFeature').textContent=num(s.feature_held);$('prManual').textContent=num(s.manual);
    prItemsData=list.items;prDrawList();
  }catch(e){if($('prErr'))$('prErr').textContent=e.message}
}
function prDrawList(){
  const q=$('prQ').value.trim().toLowerCase(),body=$('prItems');body.replaceChildren();
  const rows=prItemsData.filter(i=>!q||[i.doc_id,i.name||'',i.summary,PR_REASON[i.reason]||i.reason,i.role||''].join(' ').toLowerCase().includes(q));
  for(const i of rows){
    const tr=prEl('tr','prRow'+(prCurrent&&prCurrent.mention.mention_id===i.mention_id?' on':''));tr.tabIndex=0;
    tr.append(prEl('td','',i.doc_id),prEl('td','',i.kind==='feature'?i.summary:(i.name||'(이름 없음)')+(i.summary?' · '+i.summary:'')),prEl('td','',i.role||'—'),prEl('td','',PR_REASON[i.reason]||i.reason));
    tr.onclick=()=>prOpen(i.mention_id);tr.onkeydown=e=>{if(e.key==='Enter')prOpen(i.mention_id)};body.append(tr);
  }
  if(!rows.length){const tr=prEl('tr'),td=prEl('td','',prItemsData.length?'검색 결과가 없습니다.':'처리할 항목이 없습니다.');td.colSpan=4;tr.append(td);body.append(tr)}
}
async function prOpen(id){
  $('prErr').textContent='';
  try{prCurrent=await liveApi('/api/admin/person-review/item/'+id);prDrawDetail();prDrawList()}catch(e){$('prErr').textContent=e.message}
}
function prDrawDetail(){
  const d=prCurrent,m=d.mention,held=m.status==='held',manual=m.decided_by==='manual';
  $('prEmpty').hidden=true;$('prBody').hidden=false;$('prResult').textContent='';$('prConfirm').checked=false;$('prReason').value='';
  const name=(m.values.find(v=>v.column==='data2')||{}).value;
  $('prTitle').textContent=m.kind==='feature'?'주인 미상 정보':(name||'(이름 없음)')+' · '+(m.role||'역할 미확인');
  $('prMeta').textContent='문서 '+m.doc_id+' · 사유: '+(PR_REASON[m.reason]||m.reason)+(m.pid?' · 현재 PID '+m.pid:'')+(m.review_note?' · 메모: '+m.review_note:'');
  const st=PR_STATUS[m.status]||[m.status,''];$('prStatus').className='prBadge '+st[1];$('prStatus').textContent=st[0];
  const mine=new Set(m.tokens),doc=$('prDoc');doc.replaceChildren();
  for(const p of d.document){if(p.token){const s=prEl('span',mine.has(p.token)?'pii hit':'pii',p.text);s.title=p.label;doc.append(s)}else doc.append(document.createTextNode(p.text))}
  const vals=$('prVals');vals.replaceChildren();for(const v of m.values){const li=prEl('li');li.append(prEl('span','',v.label),document.createTextNode(v.value));vals.append(li)}
  $('prShared').textContent=m.shared_tokens.length?'공유 값으로 표시된 정보가 있어요. 연결·생성할 때 이 값은 개인에게 저장하지 않아요.':'';
  $('prCandWrap').hidden=m.kind!=='person';$('prTargetWrap').hidden=m.kind!=='feature';
  const cands=$('prCands');cands.replaceChildren();
  for(const c of d.candidates){
    const card=prEl('article','prCard'+(c.is_current?' cur':''));
    card.append(prEl('h4','','PID '+c.pid+(c.is_current?' (현재 연결)':'')),prEl('p','','연결 문서 '+c.documents.length+'건 · 역할 '+(c.roles.join(', ')||'기록 없음')));
    const t=prEl('table'),h=prEl('tr');h.append(prEl('th','','정보'),prEl('th','','이 항목'),prEl('th','','PID '+c.pid),prEl('th','','비교'));t.append(h);
    for(const r of c.compare){const tr=prEl('tr');tr.append(prEl('td','',r.label),prEl('td','',r.mine.join(', ')||'—'),prEl('td','',r.theirs.join(', ')||'—'),prEl('td',r.state,r.state==='same'?'같음':r.state==='different'?'다름':'한쪽만'));t.append(tr)}
    card.append(t);
    if(c.documents.length)card.append(prEl('p','','문서: '+c.documents.slice(0,12).join(', ')+(c.documents.length>12?' 외 '+(c.documents.length-12)+'건':'')));
    if(held){const b=prEl('button','ghostBtn','이 PID와 같은 사람 → 연결');b.type='button';b.onclick=()=>prDecide('link',{pid:c.pid},'PID '+c.pid+' 연결');card.append(b)}
    cands.append(card);
  }
  if(m.kind==='person'&&!d.candidates.length)cands.append(prEl('p','prEmpty','겹치는 기존 PID가 없어요.'));
  const targets=$('prTargets');targets.replaceChildren();
  for(const t of d.targets){
    const card=prEl('article','prCard');card.append(prEl('h4','',(t.name||'(이름 없음)')+' · '+(t.role||'역할 미확인')),prEl('p','',t.pid?'PID '+t.pid:'PID 없음 · 먼저 인물 보류에서 이 사람을 처리하세요'));
    if(held){const b=prEl('button','ghostBtn','이 사람의 정보로 귀속');b.type='button';b.disabled=!t.pid;b.onclick=()=>prDecide('assign',{target_mention_id:t.mention_id},(t.name||'선택한 인물')+'에게 귀속');card.append(b)}
    targets.append(card);
  }
  if(m.kind==='feature'&&!d.targets.length)targets.append(prEl('p','prEmpty','같은 문서에서 찾은 인물이 없어요.'));
  const acts=$('prActs');acts.replaceChildren();
  const add=(label,action,cls,text)=>{const b=prEl('button',cls,label);b.type='button';b.onclick=()=>prDecide(action,{},text);acts.append(b)};
  if(held&&m.kind==='person'){add('다른 사람 → 새 PID 만들기','create','primaryBtn','새 PID 생성');add('사람 아님 → 제외','dismiss','ghostBtn','제외')}
  if(held&&m.kind==='feature'){add('공유 값으로 표시 (대표번호 등)','shared','ghostBtn','공유 값 표시');add('무시','dismiss','ghostBtn','무시')}
  if(manual)add('수동 처리 되돌리기','undo','ghostBtn','되돌리기');
  if(!held&&!manual)acts.append(prEl('p','prResult','자동으로 연결된 항목이라 수동 처리 대상이 아니에요.'));
}
async function prDecide(action,extra,text){
  if(prBusy||!prCurrent)return;
  const reason=$('prReason').value.trim();
  if(!$('prConfirm').checked){$('prResult').textContent='원문과 후보를 확인했다는 표시를 먼저 선택하세요.';return}
  if(reason.length<3){$('prResult').textContent='처리 사유를 3자 이상 적어 주세요.';return}
  prBusy=true;$('prBody').querySelectorAll('button').forEach(b=>b.disabled=true);$('prResult').textContent=text+' 처리 중…';
  const m=prCurrent.mention;
  try{
    const r=await liveApi('/api/admin/person-review/decide',{mention_id:m.mention_id,action,expected_updated_at:m.updated_at,reason,...extra});
    prBusy=false;await prLoad();await prOpen(m.mention_id);
    $('prResult').textContent=text+' 완료'+(action==='create'&&r.pid?' · PID '+r.pid:'')+(r.person_deleted?' · 남은 정보가 없어 PID를 삭제했어요.':'');toast(text+' 완료');
  }catch(e){prBusy=false;prDrawDetail();$('prResult').textContent=e.message}
}
function bindPersons(){
  if(!prAllowed()){$('view').innerHTML='<div class="soon"><h2>인물 검토</h2><p>지정된 인물 검토 권한자만 볼 수 있는 메뉴예요. 다른 관리자에게 권한 지정을 요청하세요.</p><p><a href="#dashboard">대시보드로 돌아가기</a></p></div>';return}
  prCurrent=null;
  for(const b of $('prTabs').querySelectorAll('[data-prq]'))b.onclick=()=>{prQueue=b.dataset.prq;for(const o of $('prTabs').querySelectorAll('[data-prq]'))o.setAttribute('aria-pressed',o===b);prCurrent=null;$('prBody').hidden=true;$('prEmpty').hidden=false;prLoad()};
  for(const o of $('prTabs').querySelectorAll('[data-prq]'))o.setAttribute('aria-pressed',o.dataset.prq===prQueue);
  $('prRefresh').onclick=prLoad;$('prQ').oninput=prDrawList;prLoad();
}
// 권한 수정 창에 '인물 검토 권한' 지정 칸을 덧붙입니다. 본인 권한은 바꿀 수 없습니다.
const prOriginalPermEdit=openPermEdit;
openPermEdit=function(m){
  prOriginalPermEdit(m);const form=$('pForm');if(!form)return;
  const own=m.login===LIVE_ME.login_id,current=!!m.db.can_review_persons,firstGrant=own&&!current&&!MEMBERS.some(x=>x.db.can_review_persons&&x.db.status==='ACTIVE');
  const box=prEl('fieldset');box.innerHTML='<legend>인물 검토 권한</legend>';
  const label=prEl('label','prCheck'),input=prEl('input');input.type='checkbox';input.name='review';input.checked=current;input.disabled=own&&!firstGrant&&!current;
  label.append(input,document.createTextNode(' 원문 개인정보를 보고 동명이인·주인 미상 정보를 처리할 수 있음'));
  box.append(label,prEl('div','fhint',firstGrant?'아직 인물 검토 권한자가 없어 최초 1명은 본인이 지정할 수 있어요. 이후에는 다른 권한자가 지정해요.':own&&!current?'본인의 인물 검토 권한은 다른 관리자가 지정해야 해요.':'관리자 역할이면서 이 권한이 있는 사람에게만 인물 검토 메뉴가 보여요.'));
  form.querySelector('fieldset').after(box);
  const roleSubmit=form.onsubmit;
  form.onsubmit=async e=>{
    e.preventDefault();const reason=form.reason.value.trim(),role=form.querySelector('input[name=role]:checked').value,review=input.checked;
    if(reason.length<3){$('pErr').textContent='변경 사유를 3자 이상 입력하세요.';return}
    const values={};if(role!==m.role)values.role_code=role;if(review!==current)values.can_review_persons=review;
    if(!Object.keys(values).length){$('pErr').textContent='변경 내용이 없습니다.';return}
    if(own){
      // 본인 계정 정보가 바뀌면 세션이 끝나므로, 저장 결과를 먼저 알린 뒤 다시 로그인 화면으로 보냅니다.
      try{await liveApi('/api/admin/save',{kind:'users',key:String(m.id),values,reason,version:m.db.updated_at});
        $('pErr').textContent='';closeDetail();toast('권한을 저장했어요. 바뀐 권한을 적용하려면 3초 뒤 다시 로그인 화면으로 이동해요.');
        setTimeout(()=>location.replace('/login?relogin=1'),3000)}
      catch(error){if($('pErr'))$('pErr').textContent=error.message;else toast(error.message)}
      return}
    try{await liveSave('users',m.id,values,reason,m.db.updated_at);closeDetail();toast('권한을 DB에 저장했습니다.')}catch(error){if($('pErr'))$('pErr').textContent=error.message;else toast(error.message)}
  };
  void roleSubmit;
};

async function bootLiveAdmin(){
  try{
    LIVE_ME=await liveApi('/api/me');if(LIVE_ME.must_change_password){location.replace('/login');return}
    const personsMenu=$('menuPersons');if(personsMenu)personsMenu.hidden=!LIVE_ME.can_review_persons;
    await liveRefresh();LIVE_READY=true;
    document.querySelectorAll('.mock').forEach(n=>n.textContent='실제 DB · 일부 연결 보류');
    const me=$('meBtn');if(me){me.title=LIVE_ME.login_id;const labels=me.querySelectorAll('b,strong,.name');for(const label of labels)label.textContent=LIVE_ME.login_id}
    const meLabel=$('meBtn')?.querySelector('span:nth-of-type(2)');if(meLabel)meLabel.textContent=LIVE_ME.login_id;
    const chatLink=$('drop')?.querySelector('a');if(chatLink)chatLink.href='/chat';
    document.querySelectorAll('[data-logout]').forEach(b=>b.onclick=async()=>{try{await liveApi('/api/logout',{});location.replace('/login')}catch(e){toast(e.message)}});
  }catch(e){$('view').textContent='DB 연결을 확인하지 못했습니다: '+e.message}
}
window.addEventListener('hashchange',()=>{if(LIVE_READY){closeDetail();const page=(location.hash||'#dashboard').slice(1);if(['activity','dashboard'].includes(page))liveRefresh().catch(e=>toast(e.message));else route();document.querySelectorAll('.mock').forEach(n=>n.textContent='실제 DB · 일부 연결 보류')}});
bootLiveAdmin();
