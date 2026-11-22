/* 기존 화면을 유지하는 DB 연결. 정의가 다른 항목은 저장하지 않습니다. */
'use strict';
let LIVE_DB=null,LIVE_ME=null,LIVE_READY=false,LIVE_BUSY=false;
const unlinked=message=>toast(message||'이 기능은 화면과 DB의 처리 기준을 맞춘 뒤 연결할 예정입니다. 아직 저장되지 않습니다.');
async function liveApi(path,body){
  const r=await fetch(path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:body?{'Content-Type':'application/json','X-CSRF-Token':LIVE_ME.csrf}:{},body:body?JSON.stringify(body):undefined});
  let d;try{d=await r.json()}catch{throw Error('서버 응답을 확인할 수 없습니다.')}
  if(r.status===401){location.replace('/login');throw Error('다시 로그인해 주세요.')}
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
  const actionNames={BOOTSTRAP:'최초 업무 관리자 생성',PASSWORD_RECOVERY:'운영자 비밀번호 복구',PASSWORD_RESET:'초기 비밀번호 재발급',PASSWORD_CHANGED:'본인 비밀번호 변경',LOGIN_FAILED:'로그인 인증 실패',USER_CREATED:'계정 발급',USERS_SAVED:'계정 정보 변경',EMPLOYEES_SAVED:'직원 정보 변경',TEAMS_SAVED:'팀 정보 변경',DOCUMENTS_SAVED:'문서 권한 정보 변경',TEAM_POSITION_POLICY_SAVED:'팀·직책 정책 변경',TEST_DATA_DELETED:'테스트 데이터 삭제'};
  for(const l of d.logs){
    const target=MEMBERS.find(m=>m.id===l.target_user_id);
    const type=l.action==='TEAM_POSITION_POLICY_SAVED'?'TEAM_POLICY':l.action==='TEAMS_SAVED'?'TEAM_CREATE':l.action==='USERS_SAVED'?'PERMISSION':'DB_EVENT';
    const text=describeLog(l,d);   // DB의 변경 전·후 JSON을 사람이 읽는 문장으로 바꿈 (원본은 l.before_value / l.after_value)
    const row={id:l.log_id,at:new Date(l.changed_at),actor:l.actor,type,action:actionNames[l.action]||l.action,target:target?.login||text.target||l.target_role_code||'—',
      before:text.before,after:text.after,reason:l.reason,detail:'',result:''};
    AUDIT.push(row);if(target)target.history.push({at:row.at,text:row.action+' · '+row.reason});
    if(['USERS_SAVED','TEAM_POSITION_POLICY_SAVED'].includes(l.action))PERM_LOG.push({at:row.at,login:row.target,summary:row.action,reason:row.reason});
  }
  AUDIT.unsort=true;
  for(const m of MEMBERS)if(!m.history.length)m.history.push({at:m.joined,text:'계정 생성'});
  MOCK.roles=d.roles.map((r,i)=>({name:r.role_name,code:r.role_code,count:MEMBERS.filter(m=>m.role===r.role_code).length,color:['#2F6BFF','#8B5CF6','#21A56B'][i%3]}));
}
async function liveRefresh(){applyLiveData(await liveApi('/api/admin/data'));route()}
async function liveSave(kind,key,values,reason,version){
  if(LIVE_BUSY)throw Error('저장 중입니다. 잠시 기다려 주세요.');LIVE_BUSY=true;
  try{await liveApi('/api/admin/save',{kind,key:String(key),values,reason,version});await liveRefresh()}
  finally{LIVE_BUSY=false}
}

drawKpis=function(){
  const active=MEMBERS.filter(m=>m.db.status==='ACTIVE').length;
  $('kpis').innerHTML=[['k-blue',ICON.users,'전체 회원',num(MEMBERS.length)+'<small>명</small>','#members'],['k-orange',ICON.wait,'승인 대기','미연결','#members'],['k-green',ICON.shield,'활성 계정',num(active)+'<small>명</small>','#members'],['k-purple',ICON.doc,'검색 요청 수','미집계','#activity']]
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
actionsFor=function(m){const a=[['detail','상세정보 보기'],['perm','권한 수정']];if(m.login!==LIVE_ME.login_id){if(m.db.status==='ACTIVE')a.push(['suspend','계정 정지',true]);if(m.db.status==='LOCKED')a.push(['resume','정지 해제'])}return a};
act=async function(kind,id){
  const m=MEMBERS.find(x=>x.id===id);if(!m)return;
  try{
    if(kind==='detail')return openDetail(m);
    if(kind==='perm'){setMemberView('perm');return openPermEdit(m)}
    if(!['suspend','resume'].includes(kind))return unlinked('가입 승인·신원 대조 절차는 DB와 의미를 맞춘 뒤 연결합니다.');
    const r=await ask({title:kind==='suspend'?'계정을 정지할까요?':'정지를 해제할까요?',yes:'저장',danger:kind==='suspend',text:`<b>${esc(m.login)}</b> 계정의 실제 DB 상태를 변경합니다.`,fields:reasonField('변경 사유'),check:v=>(v.reason||'').trim().length>=3?'':'사유를 3자 이상 적어 주세요.'});
    if(!r)return;await liveSave('users',m.id,{status:kind==='suspend'?'LOCKED':'ACTIVE'},r.reason,m.db.updated_at);closeDetail();toast('DB에 저장했습니다.');
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
    +reasonField(e?'예: 인사팀 확인 후 소속 팀 변경':'예: 신규 입사자 등록');
}
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
  };
  team.onchange=sync;sync();
  const r=await pending;if(!r)return;
  const id=e?e.id:r.employee_id;
  try{
    tState.sel=id;
    await liveSave('employees',id,employeeChanges(r,e),r.reason,e?e.db.updated_at:null);
    toast(e?'직원 정보를 DB에 저장했습니다.':`직원 ${id}을(를) DB에 등록했습니다.`);
  }catch(error){toast(error.message)}
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
unlinkEmployee=function(){unlinked('일반 계정은 직원 연결이 필수인 DB 조건이 있습니다. 연결 해제 시 계정 처리 방식을 정한 뒤 연결합니다.')};
saveFix=dSuggest=dKeep=dEdit=dBulk=function(){unlinked('탐지 수정 결과를 저장·재가명처리하는 기준이 없어 연결을 보류했습니다.')};
const originalBindDetect=bindDetect;
bindDetect=function(){originalBindDetect();if($('dSource'))$('dSource').textContent='실제 탐지 검토 데이터 연결 보류 · 예시 후보를 불러오지 않습니다.';if($('dList')&&!DETECTION_REVIEW.length)$('dList').textContent='검토 데이터와 수정 반영 기준을 맞춘 뒤 연결할 예정입니다.';for(const b of $('dStats').querySelectorAll('b'))b.textContent='미연결';$('dNote').textContent='검토 데이터와 수정 결과 저장·재가명처리·재색인 기준은 연결 보류입니다.';const note=$('view').querySelector('.readonly');if(note)note.textContent='원문 보기와 탐지 수정 기능은 연결하지 않았습니다. 예시 문서나 원문은 불러오지 않습니다.'};
exportFixes=function(){unlinked('탐지 수정 저장 기능을 연결한 뒤 내보내기를 제공합니다.')};
// CSV는 실제 조회된 값만 내보내며 수식으로 실행될 수 있는 셀을 일반 텍스트로 만듭니다.
function liveCsv(rows,name){const text=rows.map(r=>r.map(v=>{let s=String(v??'');if(/^[\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"'}).join(',')).join('\r\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\ufeff'+text],{type:'text/csv;charset=utf-8'}));a.download=name;a.click();URL.revokeObjectURL(a.href)}
exportPerms=function(){liveCsv([['아이디','역할','팀','직책','검색 범위','복원 설정','계정 DB 상태'],...MEMBERS.map(m=>[m.login,m.role,teamName(m.team),m.position,m.scope,m.restore?'허용(기능 미연결)':'거부',m.db.status])],'회원권한.csv')};
exportEmployees=function(){liveCsv([['사번','팀','직책','DB 확인 상태','연결 계정'],...EMPLOYEES.map(e=>[e.id,teamName(e.team_id),e.position,e.db.verification_status,linkedMember(e)?.login||''])],'직원목록.csv')};
exportAudit=function(){if(aState.tab==='restore')return unlinked('실제 복원 이력 수집 기능은 미연결입니다.');liveCsv([['기록번호','일시','작업자','작업','대상','변경 전','변경 후','사유'],...filteredAudit().map(r=>[r.id,r.at.toISOString(),r.actor,r.action,r.target,r.before,r.after,r.reason])],'활동기록.csv')};
exportRestore=function(){unlinked('실제 복원 이력 수집 기능은 미연결입니다.')};

async function bootLiveAdmin(){
  try{
    LIVE_ME=await liveApi('/api/me');if(LIVE_ME.must_change_password){location.replace('/login');return}
    await liveRefresh();LIVE_READY=true;
    document.querySelectorAll('.mock').forEach(n=>n.textContent='실제 DB · 일부 연결 보류');
    const me=$('meBtn');if(me){me.title=LIVE_ME.login_id;const labels=me.querySelectorAll('b,strong,.name');for(const label of labels)label.textContent=LIVE_ME.login_id}
    const meLabel=$('meBtn')?.querySelector('span:nth-of-type(2)');if(meLabel)meLabel.textContent=LIVE_ME.login_id;
    const chatLink=$('drop')?.querySelector('a');if(chatLink)chatLink.href='/chat';
    document.querySelectorAll('[data-logout]').forEach(b=>b.onclick=async()=>{try{await liveApi('/api/logout',{});location.replace('/login')}catch(e){toast(e.message)}});
  }catch(e){$('view').textContent='DB 연결을 확인하지 못했습니다: '+e.message}
}
window.addEventListener('hashchange',()=>{if(LIVE_READY){closeDetail();route();document.querySelectorAll('.mock').forEach(n=>n.textContent='실제 DB · 일부 연결 보류')}});
bootLiveAdmin();
