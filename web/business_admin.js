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
  const actionNames={BOOTSTRAP:'최초 업무 관리자 생성',PASSWORD_RECOVERY:'운영자 비밀번호 복구',PASSWORD_RESET:'초기 비밀번호 재발급',PASSWORD_CHANGED:'본인 비밀번호 변경',LOGIN_FAILED:'로그인 인증 실패',USER_CREATED:'계정 발급',USERS_SAVED:'계정 정보 변경',EMPLOYEES_SAVED:'직원 정보 변경',TEAMS_SAVED:'팀 정보 변경',DOCUMENTS_SAVED:'문서 권한 정보 변경',TEAM_POSITION_POLICY_SAVED:'팀·직책 정책 변경'};
  for(const l of d.logs){
    const target=MEMBERS.find(m=>m.id===l.target_user_id),entity=l.after_value||l.before_value||{};
    const type=l.action==='TEAM_POSITION_POLICY_SAVED'?'TEAM_POLICY':l.action==='TEAMS_SAVED'?'TEAM_CREATE':l.action==='USERS_SAVED'?'PERMISSION':'DB_EVENT';
    const row={id:l.log_id,at:new Date(l.changed_at),actor:l.actor,type,action:actionNames[l.action]||l.action,target:target?.login||[entity.entity,entity.key||entity.team_id,entity.position_code].filter(Boolean).join(' · ')||l.target_role_code||'—',
      before:l.before_value?JSON.stringify(l.before_value):'',after:l.after_value?JSON.stringify(l.after_value):'',reason:l.reason,detail:'DB 저장 이력',result:''};
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
invite=function(){unlinked('현재 화면은 초대·가입 승인 방식이지만 DB는 관리자가 사번에 계정을 발급하는 방식입니다. 발급 흐름을 맞춘 뒤 연결합니다.')};
const originalBindMembers=bindMembers;
bindMembers=function(){originalBindMembers();$('mRefresh').onclick=()=>liveRefresh().then(()=>toast('DB 목록을 새로 불러왔습니다.')).catch(e=>toast(e.message));$('mInvite').title='계정 발급 흐름 협의 후 연결';const tiles=$('mTiles').children;for(const i of [2,3]){tiles[i].querySelector('.n').textContent='미연결';tiles[i].querySelector('.d').textContent='가입 승인·인사 대조 흐름 협의 필요'}const notice=$('mListView').querySelector('.notice span');if(notice)notice.textContent='현재는 DB의 계정 상태와 직원 확인 상태를 각각 조회합니다. 가입 승인·인사 시스템 대조는 연결 보류입니다.'};
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
bindTeams=function(){originalBindTeams();$('tSource').textContent=`직원 DB ${EMPLOYEES.length}명 · 문서 인물과 별도로 관리합니다. 인사 시스템 대조는 미연결입니다.`};
const originalDrawEmpCard=drawEmpCard;
drawEmpCard=function(){originalDrawEmpCard();const card=$('tCard');if(!card)return;for(const li of card.querySelectorAll('li')){const key=li.firstElementChild?.textContent||'';if(/문서|최초|최근|근거/.test(key)&&li.lastElementChild)li.lastElementChild.textContent='미연결'}};
const originalDrawTeamList=drawTeamList;
drawTeamList=function(){originalDrawTeamList();for(const tr of $('tTeamBody').querySelectorAll('tr')){const id=tr.children[1]?.textContent.trim(),team=LIVE_DB.teams.find(t=>t.team_id===id);if(tr.children[5])tr.children[5].textContent=team?ymd(new Date(team.created_at)):'—'}};
policyChips=function(p){const positions=p.positions||[];return `<span class="pchip scope">직책 정책 ${positions.length}개</span><span class="pchip">금융 미연결</span><span class="pchip">복원 미연결</span>`};
policyText=function(p){return `직책별 정책 ${(p.positions||[]).length}개 · 금융 문서·개별 복원 항목은 미연결`};
editTeamPolicy=function(){unlinked('기존 화면은 팀 단위 설정이고 DB는 팀·직책 조합입니다. 직책 선택 방식과 금융 문서·복원 항목을 맞춘 뒤 연결합니다.')};
openTeamSheet=function(id){
  sheetReturn=document.activeElement;const team=LIVE_DB.teams.find(t=>t.team_id===id);if(!team)return;
  const rows=LIVE_DB.policies.filter(p=>p.team_id===id);
  $('sheetBody').innerHTML=`<h3 id="sheetTitle">🏢 ${esc(team.team_name)}<button class="x" type="button" data-sheet-close aria-label="닫기">×</button></h3><dl class="dl"><dt>팀 코드</dt><dd>${esc(id)}</dd><dt>사용 여부</dt><dd>${team.is_active?'사용':'미사용'}</dd><dt>직원 수</dt><dd>${EMPLOYEES.filter(e=>e.team_id===id).length}명</dd></dl><div><h4>저장된 직책별 정책</h4><table class="roleGrid"><thead><tr><th>직책</th><th>검색 범위</th><th>복원 설정</th></tr></thead><tbody>${rows.map(p=>`<tr><td>${esc(p.position_code)}</td><td>${esc(p.search_scope)}</td><td>${p.can_restore?'허용(기능 미연결)':'거부'}</td></tr>`).join('')||'<tr><td colspan="3">등록된 정책 없음 · 검색 차단</td></tr>'}</tbody></table></div><p>팀 단위 화면과 직책별 정책의 적용 방식을 확인한 뒤 수정 기능을 연결합니다.</p><div class="sheetActs"><button class="ghostBtn" data-sheet-close>닫기</button><button class="primaryBtn" id="sheetPolicy">이 팀 권한 설정</button></div>`;
  $('sheetPolicy').onclick=editTeamPolicy;$('sheet').classList.add('open');$('sheet').setAttribute('aria-hidden','false');$('sheetBody').querySelector('.x').focus();
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
exportAudit=function(){if(aState.tab==='restore')return unlinked('실제 복원 이력 수집 기능은 미연결입니다.');liveCsv([['기록번호','일시','작업자','작업','대상','사유'],...filteredAudit().map(r=>[r.id,r.at.toISOString(),r.actor,r.action,r.target,r.reason])],'활동기록.csv')};
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
