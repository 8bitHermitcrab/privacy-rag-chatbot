"use strict";
const $ = id => document.getElementById(id);
let csrf = "";
let progressPollTimer=null, progressPollPending=false, controlRequestPending=false;
const events = {dv_build_start:"데이터 버전 · 만들기 시작",dv_build_create:"데이터 버전 · 스키마 생성",dv_build:"데이터 버전 · 만들기 완료",dv_switch:"데이터 버전 · 서버 전환",dv_promote:"데이터 버전 · 기본값 저장",dv_retire:"데이터 버전 · 사용 종료",dv_drop:"데이터 버전 · 데이터 삭제",manager_start:"관리 프로그램 시작",drain_timeout_change:"중지·재시작 대기 시간 변경",account_setup:"관리 계정 설정",monitor_setup:"DB 조회 계정 준비",login:"관리자 로그인",logout:"관리자 로그아웃",password_change:"비밀번호 변경"};
const text = (id,value) => { $(id).textContent = value; };
function signedOut(message="") {pidReset();stopProgressPolling();csrf="";$("dashboard").hidden=true;$("login-view").hidden=false;$("password-dialog").close();$("action-dialog").close();$("password-form").reset();text("login-error",message);}
async function api(path,options={}) {
  const response = await fetch(path,{credentials:"same-origin",cache:"no-store",...options,headers:{"Content-Type":"application/json","X-Admin-Request":"1",...(csrf?{"X-CSRF-Token":csrf}:{}),...(options.headers||{})}});
  let data;
  try {data=await response.json();} catch {throw new Error("서버 응답을 확인하지 못했습니다.");}
  if(!response.ok){if(response.status===401 && path!=="/api/login")signedOut("세션이 만료되었습니다. 다시 로그인하세요.");throw new Error(typeof data.detail==="string"?data.detail:"요청을 처리하지 못했습니다.");}
  return data;
}
function badge(id,kind,label){$(id).className="badge "+kind;text(id,label);}
function gigabytes(value){return (value/1024**3).toLocaleString("ko-KR",{maximumFractionDigits:1})+" GB";}
function timeFormat(value){return new Date(value).toLocaleString("ko-KR",{hour12:false});}
function render(data){
  const app=data.application,db=data.database,system=data.system;
  text("app-state",app.state==="ok"?"정상 응답":"응답 없음");
  text("app-detail",app.state==="ok"?`${app.latency_ms} ms · 검색 ${app.search_mode}`:"서비스 중지 또는 연결 상태 확인 필요");
  badge("app-badge",app.state==="ok"?"ok":"warn",app.state==="ok"?"연결됨":"확인 필요");
  text("db-state",db.state==="ok"?"연결 정상":db.state==="unconfigured"?"설정 필요":"연결 확인 필요");
  text("db-detail",db.state==="unconfigured"?"모니터링 계정 설정 필요":"버전 정보만 읽는 전용 계정");
  badge("db-badge",db.state==="ok"?"ok":"warn",db.state==="ok"?"연결됨":"확인 필요");
  text("schema-version",db.state==="ok"?db.version:"—");text("system-os",system.os);text("python-version",system.python);text("cpu-count",system.cpu_count?system.cpu_count+"개":"확인 불가");
  const seconds=system.uptime_seconds;text("uptime",Math.floor(seconds/3600)+"시간 "+Math.floor(seconds%3600/60)+"분");text("commit",data.commit);
  const used=system.disk_total-system.disk_free;
  text("disk-free",gigabytes(system.disk_free));text("disk-used","사용 "+gigabytes(used));text("disk-total","전체 "+gigabytes(system.disk_total));
  $("disk-meter").className="disk-fill level-"+Math.min(10,Math.max(0,Math.round(used/system.disk_total*10)));
  $("events").replaceChildren();
  for(const item of data.events){const row=document.createElement("tr");for(const value of [timeFormat(item.time),eventLabel(item.event),item.result==="success"?"완료":"실패"]){const cell=document.createElement("td");cell.textContent=value;row.append(cell);}$("events").append(row);}
  if(!data.events.length){const row=document.createElement("tr"),cell=document.createElement("td");cell.colSpan=3;cell.className="empty";cell.textContent="기록이 없습니다.";row.append(cell);$("events").append(row);}
  text("last-update","마지막 확인 · "+timeFormat(data.checked_at));
}
async function refresh(){ $("refresh").disabled=true;text("dashboard-error","");try{const [overview,operations]=await Promise.all([api("/api/overview"),api("/api/operations")]);render(overview);renderOperations(operations);}catch(error){text("dashboard-error",error.message);}finally{$("refresh").disabled=false;} if(typeof monRefresh==="function")monRefresh(currentView==="monitor"?undefined:15); }
async function enter(){const session=await api("/api/session");csrf=session.csrf;pidReset();try{const options=await api("/api/person-dictionary/options");$("pid-sensitivity").value=options.sensitivity;}catch(error){text("pid-error",error.message);}text("account-name",session.username);$("login-view").hidden=true;$("dashboard").hidden=false;startProgressPolling();await pollProgress();await refresh();if(currentView==="versions")dvRefresh();monSchedule();}
$("login-form").addEventListener("submit",async event=>{event.preventDefault();$("login-button").disabled=true;text("login-error","");const password=$("password").value;$("password").value="";try{await api("/api/login",{method:"POST",body:JSON.stringify({username:$("username").value,password})});await enter();}catch(error){text("login-error",error.message);}finally{$("login-button").disabled=false;}});
$("refresh").addEventListener("click",refresh);
$("logout").addEventListener("click",async()=>{try{await api("/api/logout",{method:"POST",body:"{}"});signedOut();}catch(error){text("dashboard-error",error.message);}});
$("password-open").addEventListener("click",()=>{$("password-form").reset();text("password-error","");$("password-dialog").showModal();});
$("password-close").addEventListener("click",()=>{$("password-dialog").close();$("password-form").reset();});
$("password-form").addEventListener("submit",async event=>{event.preventDefault();text("password-error","");if($("new-password").value!==$("confirm-password").value){text("password-error","새 비밀번호가 일치하지 않습니다.");return;}$("password-save").disabled=true;try{await api("/api/password",{method:"POST",body:JSON.stringify({current:$("current-password").value,new:$("new-password").value})});signedOut("비밀번호를 변경했습니다. 다시 로그인하세요.");}catch(error){text("password-error",error.message);}finally{$("password-save").disabled=false;}});
enter().catch(()=>signedOut());

const viewNames={monitor:["모니터링","서버별 요청 모니터링","챗봇 서버 1·2로 들어온 요청 수, 오류, 응답 시간, 데이터 버전을 확인합니다."],versions:["데이터 버전","HMAC 키 교체","새 키로 만든 데이터 버전을 서버별로 전환하고, 예전 버전을 정리합니다."],persons:["인물사전","인물사전 연결 관리","민감도별 예상 결과를 확인하고 vault.person에 반영합니다."],overview:["운영 현황","서버 대시보드","실제 서비스와 DB 연결 상태를 확인합니다."],services:["챗봇 서비스","서비스 실행 관리","등록한 챗봇의 시작·중지·재시작을 실행합니다."],balancing:["로드밸런싱","트래픽 분산 관리","서버별 요청 분산과 점검 제외·복귀를 제어합니다."],activity:["작업 기록","접속·작업 이력","관리자 인증과 서비스 제어의 결과를 확인합니다."]};
let currentView="overview", pendingAction=null, operationBusy=false;
function eventLabel(event){if(events[event])return events[event];const labels={start:"시작",stop:"중지",restart:"재시작",exclude:"트래픽 제외",include:"트래픽 복귀",weight:"가중치 변경"};for(const target of ["node1","node2","proxy"]){for(const action of Object.keys(labels)){if(event===target+"_"+action)return (target==="proxy"?"분산기":"챗봇 "+target.slice(-1))+" · "+labels[action];}}return "관리 작업";}
function switchView(view){currentView=viewNames[view]?view:"overview";const label=viewNames[currentView];text("view-eyebrow",label[0]);text("view-title",label[1]);text("view-description",label[2]);for(const name of Object.keys(viewNames)){const button=$("nav-"+name);button.classList.toggle("active",name===currentView);if(name===currentView)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");}$("overview-status").hidden=currentView!=="overview";$("overview-environment").hidden=currentView!=="overview";$("services-view").hidden=currentView!=="services";$("balancing-view").hidden=currentView!=="balancing";$("activity").hidden=currentView!=="activity";$("persons-view").hidden=currentView!=="persons";$("versions-view").hidden=currentView!=="versions";$("monitor-view").hidden=currentView!=="monitor";monSchedule();if(currentView==="versions"&&!$("dashboard").hidden)dvRefresh();}
for(const name of Object.keys(viewNames))$("nav-"+name).addEventListener("click",()=>{history.replaceState(null,"","#"+name);switchView(name);});
window.addEventListener("hashchange",()=>switchView(location.hash.slice(1)));
switchView(location.hash.slice(1));
function element(tag,className,value){const node=document.createElement(tag);if(className)node.className=className;if(value!==undefined)node.textContent=value;return node;}
function actionButton(target,action,label,disabled=false,weight=null){const button=element("button","secondary action-button",label);button.type="button";button.disabled=disabled||operationBusy;button.addEventListener("click",()=>askAction(target,action,weight));return button;}
function askAction(target,action,weight=null){if(operationBusy)return;pendingAction={target,action,weight};$("action-form").reset();text("action-error","");$("action-progress").hidden=true;text("action-title",eventLabel(target+"_"+action));let message=target==="proxy"?"분산기":"챗봇 "+target.slice(-1);message+=" 대상 작업입니다. ";if(action==="stop"||action==="restart")message+=target==="proxy"?"분산기 자체를 중지하거나 재시작하면 전체 접속이 잠시 끊깁니다.":"새 요청을 제외하고 처리 중인 요청이 끝난 뒤 실행합니다. 다른 정상 서버가 없으면 작업을 막습니다.";else if(action==="exclude")message+="새 요청만 제외하며 처리 중인 요청은 계속 진행합니다.";else if(action==="include")message+="정상 응답을 확인한 뒤 새 요청을 받도록 복귀합니다.";else if(action==="weight")message+="상대 가중치를 "+weight+"로 변경합니다. 0은 새 요청을 받지 않는 설정입니다.";else message+="기동 후 정상 응답까지 확인합니다. 트래픽 제외 상태였다면 복귀는 별도로 실행하세요.";text("action-description",message);$("action-submit").disabled=false;$("action-dialog").showModal();}
function renderOperations(data){if(document.activeElement!==$("drain-timeout"))$("drain-timeout").value=String(data.drain_timeout_seconds||120);const count=data.nodes.filter(node=>node.healthy).length;text("app-state",count+" / 2 정상");text("app-detail","등록된 챗봇 서버 기준");badge("app-badge",count===2?"ok":count?"warn":"fail",count===2?"모두 정상":count?"일부 정상":"응답 없음");text("service-provider","로컬 프로세스");text("mode-title","현재 두 프로세스로 분산을 검증하는 개발 환경입니다.");text("mode-description","현재 PC의 두 프로세스입니다. 클라우드 전용 구성은 별도로 백업해 두었습니다.");$("service-cards").replaceChildren();$("routing-cards").replaceChildren();
for(const node of data.nodes){const card=element("article","panel node-card"),head=element("div","panel-heading"),name=element("h2","",node.name),status=element("span","badge "+(node.healthy?"ok":node.managed?"warn":"neutral"),node.healthy?"정상 응답":node.managed?"응답 확인 필요":"중지");head.append(name,status);card.append(head,element("p","muted small",("등록 포트 :"+node.port+" · ")+(node.managed?"관리 프로그램 제어 대상":node.healthy?"별도로 실행된 프로세스":"실행 전")));const actions=element("div","action-row");actions.append(actionButton(node.id,"start","시작",node.healthy||node.managed),actionButton(node.id,"stop","중지",!node.managed),actionButton(node.id,"restart","재시작",!node.managed));card.append(actions);$("service-cards").append(card);
const route=element("article","panel node-card"),routeHead=element("div","panel-heading");routeHead.append(element("h2","",node.name),element("span","badge "+(node.enabled&&node.weight>0?"ok":"warn"),node.enabled&&node.weight>0?"요청 허용":"점검 제외"));route.append(routeHead);const metrics=element("div","route-metrics");metrics.append(element("span","","처리 중 "+(node.active===null?"—":node.active)+"건"),element("span","","누적 "+(node.requests===null?"—":node.requests)+"건"));route.append(metrics);const weightLabel=element("label","","상대 가중치 (0~100)"),input=element("input","weight-input");input.type="number";input.min="0";input.max="100";input.value=String(node.weight);input.setAttribute("aria-label",node.name+" 가중치");route.append(weightLabel,input);const weightButton=element("button","secondary action-button","가중치 적용");weightButton.disabled=!node.healthy||operationBusy;weightButton.addEventListener("click",()=>{const value=Number(input.value);if(!Number.isInteger(value)||value<0||value>100){input.reportValidity();return;}askAction(node.id,"weight",value);});const routeActions=element("div","action-row");routeActions.append(weightButton,actionButton(node.id,node.enabled?"exclude":"include",node.enabled?"트래픽 제외":"트래픽 복귀",!node.healthy&&!node.enabled));route.append(routeActions,element("p","muted small route-note",!node.healthy?"정상 응답이 없어 실제 요청 대상으로 사용하지 않습니다.":!data.load_balancer.running?"분산기가 중지되어 있습니다. 설정은 기동 시 사용합니다.":"분산기 연결 기준으로 적용됩니다."));$("routing-cards").append(route);}
const lb=data.load_balancer;text("lb-provider","개발용 내장 분산기 · 127.0.0.1:8080");badge("lb-state",lb.running?"ok":"warn",lb.running?"정상 연결":"연결 없음");text("lb-description","가중 라운드로빈과 정상 서버 우회를 실제 적용합니다. GET/HEAD만 연결 오류 시 재시도하고 POST는 중복 실행 방지를 위해 재시도하지 않습니다.");$("lb-actions").replaceChildren();$("lb-actions").append(actionButton("proxy","start","분산기 시작",lb.running),actionButton("proxy","stop","분산기 중지",!lb.managed),actionButton("proxy","restart","분산기 재시작",!lb.managed));}
$("action-close").addEventListener("click",()=>{if(!operationBusy)$("action-dialog").close();});
$("action-dialog").addEventListener("cancel",event=>{if(operationBusy)event.preventDefault();});
$("action-form").addEventListener("submit",async event=>{event.preventDefault();if(operationBusy||!pendingAction||!$("action-confirmed").checked)return;controlRequestPending=true;operationBusy=true;$("action-submit").disabled=true;$("action-close").disabled=true;$("action-progress").hidden=false;text("action-error","");text("action-phase","작업 요청 중");text("action-status","요청을 전달하고 있습니다.");try{const job=pendingAction;const request=api("/api/control",{method:"POST",body:JSON.stringify({...job,confirmation:job.target+":"+job.action})});setTimeout(pollProgress,150);const result=await request;$("action-dialog").close();$("operation-result").hidden=false;text("operation-result",result.message);setTimeout(()=>{$("operation-result").hidden=true;},6000);}catch(error){text("action-error",error.message);}finally{controlRequestPending=false;await pollProgress();$("action-submit").disabled=operationBusy;$("action-close").disabled=operationBusy;await refresh();}});
const phaseLabels={idle:"대기",checking:"상태 확인",excluding:"새 요청 제외",waiting:"기존 요청 완료 대기",stopping:"서비스 중지",starting:"기동·정상 응답 확인",restoring:"트래픽 복귀",completed:"작업 완료",deferred:"작업 보류",failed:"작업 실패"};
function showProgress(data){
  operationBusy=Boolean(data.busy||controlRequestPending);
  $("save-drain-timeout").disabled=operationBusy;
  $("action-submit").disabled=operationBusy;
  $("action-close").disabled=operationBusy;
  if(operationBusy)for(const button of document.querySelectorAll(".action-button"))button.disabled=true;
  if(!data.job_id){$("job-progress").hidden=true;return;}
  const target=data.target==="proxy"?"분산기":"챗봇 "+data.target.slice(-1);
  const phase=phaseLabels[data.phase]||"상태 확인";
  const active=data.active_requests===null?"처리 중 요청 확인 중":"처리 중 "+data.active_requests+"건";
  const timing=data.phase==="waiting"?"대기 "+Math.floor(data.waited_seconds||0)+"초 / 최대 "+data.drain_limit_seconds+"초 · 남은 시간 "+Math.ceil(data.remaining_seconds||0)+"초":"전체 경과 "+Math.floor(data.elapsed_seconds||0)+"초";
  $("job-progress").hidden=false;$("job-progress").className="job-progress phase-"+data.phase;
  text("job-phase",target+" · "+phase);text("job-message",data.message);text("job-active",active);text("job-time",timing);
  if(controlRequestPending){$("action-progress").hidden=false;text("action-phase",phase);text("action-status",data.message+" · "+active);text("action-timing",timing);}
}
async function pollProgress(){if(progressPollPending||$("dashboard").hidden)return;progressPollPending=true;const previous=operationBusy;try{showProgress(await api("/api/control/status"));if(previous&&!operationBusy&&!controlRequestPending)await refresh();}catch(error){if(controlRequestPending)text("action-status","상태"+" 연결을 확인하고 있습니다. 서버 작업은 계속될 수 있습니다.");}finally{progressPollPending=false;}}
function startProgressPolling(){stopProgressPolling();progressPollTimer=setInterval(pollProgress,1000);}
function stopProgressPolling(){if(progressPollTimer!==null){clearInterval(progressPollTimer);progressPollTimer=null;}}
$("wait-settings").addEventListener("submit",async event=>{event.preventDefault();if(operationBusy)return;const seconds=Number($("drain-timeout").value);if(!Number.isInteger(seconds)||seconds<15||seconds>120){text("wait-settings-result","15~120초의 정수로 입력하세요.");return;}$("save-drain-timeout").disabled=true;try{const result=await api("/api/operations/settings",{method:"POST",body:JSON.stringify({drain_timeout_seconds:seconds})});text("wait-settings-result","최대 대기 시간을 "+result.drain_timeout_seconds+"초로 저장했습니다.");}catch(error){text("wait-settings-result",error.message);}finally{$("save-drain-timeout").disabled=operationBusy;}});


let pidPlan=null,pidBusy=false;
const pidActions={create:'신규 생성',merge:'기존 PID 연결',unchanged:'기존 연결 유지',hold:'인물 보류',hold_feature:'정보 보류',hold_document:'문서 보류',manual:'관리자 판정 유지'};
function pidReset(){pidPlan=null;$("pid-confirm").checked=false;$("pid-apply").disabled=true;$("pid-events").replaceChildren();text("pid-summary","");}
$("pid-sensitivity").addEventListener("change",()=>{pidReset();text("pid-status","민감도가 변경되었습니다. 다시 미리보기를 실행하세요.");});
$("pid-confirm").addEventListener("change",()=>{$("pid-apply").disabled=pidBusy||!pidPlan||!$("pid-confirm").checked;});
async function pidRun(apply){
 if(pidBusy||apply&&(!pidPlan||!$("pid-confirm").checked))return;
 const sensitivity=$("pid-sensitivity").value;
 const plan=pidPlan;pidBusy=true;$("pid-preview").disabled=true;$("pid-apply").disabled=true;$("pid-sensitivity").disabled=true;$("pid-confirm").disabled=true;
 text("pid-error","");text("pid-status",apply?"검토한 결과를 반영하고 있습니다…":"원문을 탐지하고 인물별 연결을 분석하고 있습니다. 모델 로딩으로 시간이 걸릴 수 있습니다…");
 try{
  const data=await api('/api/person-dictionary/'+(apply?'apply':'preview'),{method:'POST',body:JSON.stringify({sensitivity,plan,confirmed:apply})});
  pidPlan=apply?null:data.plan;$("pid-confirm").checked=false;
  text("pid-status",apply?"vault.person 반영을 완료했습니다.":"미리보기 완료 · DB는 변경하지 않았습니다. 아래 결과를 검토하세요.");
  const counts=Object.entries(data.actions).map(([key,value])=>(pidActions[key]||key)+' '+value+'건').join(' · ');
  if(data.query_token_alignment_warning)text('pid-error','기존 DB 토큰을 재사용한 정보가 있습니다. 현재 HMAC 설정으로 질문을 가명처리했을 때 같은 토큰이 되는지 별도 확인이 필요합니다.');
  text("pid-summary",data.profile_description+' / 문서 '+data.documents+'건 / PID '+data.before_people+' → '+data.after_people+' / '+counts);
  $("pid-events").replaceChildren();
  for(const row of data.events){const tr=document.createElement('tr');for(const value of [row.doc_id,pidActions[row.action]||row.action,row.pid!==null&&row.pid!==undefined?(row.pid<0?'신규 예정 '+(-row.pid):String(row.pid)):(row.candidates||[]).join(', '),row.message,JSON.stringify(row.values||{token:row.token||''})])tr.append(element('td','',value));$("pid-events").append(tr);}
 }catch(error){pidPlan=null;text("pid-error",error.message);text("pid-status",apply?"반영 여부를 확인한 후 다시 미리보기를 실행하세요.":"미리보기를 완료하지 못했습니다.");}
 finally{pidBusy=false;$("pid-preview").disabled=false;$("pid-sensitivity").disabled=false;$("pid-confirm").disabled=false;$("pid-apply").disabled=true;}
}
$("pid-preview").addEventListener("click",()=>pidRun(false));$("pid-apply").addEventListener("click",()=>pidRun(true));


// ---------------------------------------------------------------- 데이터 버전 (HMAC 키 교체)
const dvStatus={BUILDING:["적재 중","warn"],READY:["준비 완료","ok"],ACTIVE:["사용 중","ok"],RETIRED:["사용 종료","neutral"]};
let dvData=null,dvPending=null,dvBusy=false,dvTimer=null;
function dvNumber(value){return value===null||value===undefined?"—":Number(value).toLocaleString("ko-KR");}
async function dvRefresh(){if($("dashboard").hidden)return;text("dv-error","");try{dvData=await api("/api/data-versions");dvRender();}catch(error){text("dv-error",error.message);}}
function dvButton(label,onClick,disabled=false,kind="secondary"){const button=element("button",kind+" action-button",label);button.type="button";button.disabled=disabled||dvBusy||operationBusy;button.addEventListener("click",onClick);return button;}
function dvRender(){
 const data=dvData;if(!data)return;
 const usable=data.versions.filter(v=>(v.status==="READY"||v.status==="ACTIVE")&&v.key_stored&&v.tables_exist);
 $("dv-nodes").replaceChildren();
 for(const node of data.nodes){
  const card=element("article","panel node-card"),head=element("div","panel-heading");
  head.append(element("h2","",node.name),element("span","badge "+(node.running?"ok":"warn"),node.running?"실행 중 "+node.running:"응답 없음"));
  card.append(head,element("p","muted small",node.assigned?"서버별 지정: "+node.assigned:".env 기본값을 따름 ("+node.effective+")"));
  const select=element("select","");select.setAttribute("aria-label",node.name+" 전환할 버전");
  for(const v of usable){const option=element("option","",v.version+" · "+dvStatus[v.status][0]);option.value=v.version;if(v.version===node.effective)option.disabled=true;select.append(option);}
  const first=[...select.options].find(o=>!o.disabled);if(first)select.value=first.value;
  const actions=element("div","action-row");
  actions.append(select,dvButton("이 버전으로 전환",()=>dvAsk({title:node.name+" · "+select.value+"로 전환",description:node.name+"를 "+select.value+" 데이터와 키로 다시 시작합니다. 새 요청을 다른 서버로 돌리고 처리 중인 요청이 끝난 뒤 재시작합니다. 다른 정상 서버가 없으면 진행하지 않습니다.",path:"/api/data-versions/switch",body:{node:node.id,version:select.value,confirmation:node.id+":"+select.value}}),!first));
  card.append(actions);$("dv-nodes").append(card);
 }
 text("dv-env",".env 기본값 "+data.env.data_version+(data.env.key_matches?" · 키 짝 맞음":" · 키 짝 확인 필요")+(data.env.new_key_present?" · NEW_HMAC_KEY 있음":""));
 const allOn=v=>data.nodes.every(n=>n.running===v&&n.effective===v);
 const usedBy=v=>data.nodes.some(n=>n.running===v||n.effective===v)||data.env.data_version===v;
 $("dv-rows").replaceChildren();
 for(const v of data.versions){
  const tr=document.createElement("tr");const [label,kind]=dvStatus[v.status]||[v.status,"neutral"];
  const badgeCell=element("td","");badgeCell.append(element("span","badge "+kind,label));
  const c=v.counts||{};
  tr.append(element("td","mono",v.version),badgeCell,element("td","small",v.rag_schema+" · "+v.vault_schema),element("td","",dvNumber(c.chunks)),element("td","",dvNumber(c.token_map)),element("td","",dvNumber(c.person)),element("td","mono small",v.key_fingerprint+(v.key_stored?"":" (키 미보관)")));
  const cell=element("td","");const row=element("div","action-row");
  if(allOn(v.version)&&data.env.data_version!==v.version)row.append(dvButton("기본값으로 저장",()=>dvAsk({title:v.version+" · .env 기본값으로 저장",description:".env의 HMAC_KEY와 DATA_VERSION을 "+v.version+"로 바꿉니다. 지금 .env는 비공개 폴더에 백업합니다. 서버별 지정은 비웁니다.",path:"/api/data-versions/promote",body:{version:v.version,confirmation:"promote:"+v.version}}),false,"primary"));
  if((v.status==="ACTIVE"||v.status==="READY")&&!usedBy(v.version))row.append(dvButton("사용 종료",()=>dvAsk({title:v.version+" · 사용 종료",description:v.version+"를 사용 종료로 표시합니다. 데이터는 지우지 않으며, 종료된 버전으로는 서버를 시작할 수 없습니다.",path:"/api/data-versions/retire",body:{version:v.version,confirmation:"retire:"+v.version}})));
  if(v.status==="RETIRED"&&v.tables_exist)row.append(dvButton("데이터 삭제",()=>dvAsk({title:v.version+" · 데이터 삭제",description:v.version+"의 문서·토큰·인물 데이터를 지웁니다. 되돌릴 수 없습니다. 복원 기록에 남은 이 버전의 토큰은 원래 값을 찾을 수 없게 됩니다. 24시간 안의 백업이 있어야 진행됩니다.",typed:true,path:"/api/data-versions/drop",body:{version:v.version,confirmation:"drop:"+v.version}})));
  if(!row.children.length)row.append(element("span","muted small","—"));
  cell.append(row);tr.append(cell);$("dv-rows").append(tr);
 }
 if(!data.versions.length){const tr=document.createElement("tr"),td=element("td","empty","등록된 버전이 없습니다. 챗봇 서버를 한 번 시작하면 v1이 등록됩니다.");td.colSpan=8;tr.append(td);$("dv-rows").append(tr);}
 text("dv-backup",data.backup?"최근 DB 백업 · "+data.backup.name+" ("+data.backup.age_hours+"시간 전)":"DB 백업이 없습니다. 데이터 삭제 전에 DB_백업을 실행하세요.");
 text("dv-next","다음 버전 이름 · "+data.next_version);
 dvShowBuild(data.build);
}
function dvShowBuild(b){
 dvBusy=Boolean(b&&b.busy);$("dv-build").disabled=dvBusy;
 if(!b||!b.steps){$("dv-steps").hidden=true;text("dv-build-status","");return;}
 $("dv-steps").hidden=false;$("dv-steps").replaceChildren();
 b.steps.forEach((name,i)=>{const n=i+1,li=element("li",n<b.step||b.ok?"done":n===b.step&&b.busy?"current":"",name);$("dv-steps").append(li);});
 text("dv-build-status",(b.version?b.version+" · ":"")+b.message+(b.elapsed_seconds!==undefined?" · 경과 "+Math.floor(b.elapsed_seconds)+"초":""));
 if(dvBusy&&dvTimer===null)dvTimer=setInterval(dvPollBuild,2000);
 if(!dvBusy&&dvTimer!==null){clearInterval(dvTimer);dvTimer=null;}
}
async function dvPollBuild(){try{const b=await api("/api/data-versions/build");const wasBusy=dvBusy;dvShowBuild(b);if(wasBusy&&!b.busy)await dvRefresh();}catch(error){}}
function dvAsk(job){if(dvBusy||operationBusy)return;dvPending=job;$("dv-form").reset();text("dv-title",job.title);text("dv-description",job.description);$("dv-typed-wrap").hidden=!job.typed;$("dv-typed").required=Boolean(job.typed);text("dv-dialog-status","");text("dv-dialog-error","");$("dv-submit").disabled=false;$("dv-close").disabled=false;$("dv-dialog").showModal();}
$("dv-close").addEventListener("click",()=>{if(!$("dv-submit").disabled||!dvPending)$("dv-dialog").close();});
$("dv-form").addEventListener("submit",async event=>{event.preventDefault();if(!dvPending||!$("dv-confirmed").checked)return;
 const job=dvPending,body={...job.body};if(job.typed)body.typed=$("dv-typed").value;
 $("dv-submit").disabled=true;$("dv-close").disabled=true;text("dv-dialog-error","");text("dv-dialog-status",job.path.endsWith("switch")?"전환 중입니다. 재시작 진행 상황은 화면 위쪽 작업 상태에 표시됩니다.":"처리 중입니다.");
 try{const request=api(job.path,{method:"POST",body:JSON.stringify(body)});if(job.path.endsWith("switch"))setTimeout(pollProgress,300);await request;$("dv-dialog").close();$("operation-result").hidden=false;text("operation-result","작업을 완료했습니다.");setTimeout(()=>{$("operation-result").hidden=true;},6000);}
 catch(error){text("dv-dialog-error",error.message);text("dv-dialog-status","");}
 finally{$("dv-submit").disabled=false;$("dv-close").disabled=false;dvPending=null;await dvRefresh();await refresh();}
});
for(const radio of document.querySelectorAll('input[name="dv-source"]'))radio.addEventListener("change",()=>{$("dv-key-fields").hidden=document.querySelector('input[name="dv-source"]:checked').value!=="input";});
$("dv-refresh").addEventListener("click",dvRefresh);
$("dv-build").addEventListener("click",async()=>{
 if(dvBusy)return;text("dv-error","");
 const source=document.querySelector('input[name="dv-source"]:checked').value;
 if(source==="input"&&$("dv-key").value!==$("dv-key-confirm").value){text("dv-error","두 번 입력한 키가 서로 다릅니다.");return;}
 const key=$("dv-key").value,confirm=$("dv-key-confirm").value;$("dv-key").value="";$("dv-key-confirm").value="";
 $("dv-build").disabled=true;
 try{const result=await api("/api/data-versions/build",{method:"POST",body:JSON.stringify({source,key:source==="input"?key:undefined,key_confirm:source==="input"?confirm:undefined,confirmation:"build"})});text("dv-build-status",result.version+" 만들기를 시작했습니다.");await dvPollBuild();}
 catch(error){text("dv-error",error.message);$("dv-build").disabled=false;}
});


// ---------------------------------------------------------------- 모니터링
var monTimer=null;   // var: switchView 가 스크립트 앞부분에서 먼저 호출해도 쓸 수 있게
const monKind={chat:"채팅 질문",admin:"업무 관리",api:"기타 API",page:"화면"};
function monSchedule(){if(monTimer!==null){clearInterval(monTimer);monTimer=null;}if(currentView==="monitor"&&!$("dashboard").hidden){monRefresh();monTimer=setInterval(()=>monRefresh(),10000);}}
function monTime(t){return new Date(t*1000).toLocaleTimeString("ko-KR",{hour12:false});}
function monBanner(mixed){
 if(!mixed||!mixed.mixed){$("mixed-banner").hidden=true;return;}
 const parts=Object.entries(mixed.running).map(([node,v])=>"서버 "+node.slice(-1)+" "+(v||"응답 없음"));
 const since=mixed.since?" · "+Math.max(1,Math.round((Date.now()/1000-mixed.since)/60))+"분째 ("+monTime(mixed.since)+"부터)":"";
 text("mixed-title","두 서버의 데이터 버전이 다릅니다 · "+parts.join(" / ")+since);
 text("mixed-description","전환 중에만 잠깐 허용되는 상태입니다. 길어지면 인물 검토 판정과 적재가 버전별로 나뉘어 쌓일 수 있으니 전환을 끝내거나 되돌리세요.");
 $("mixed-banner").hidden=false;
}
async function monRefresh(minutes){
 if($("dashboard").hidden)return;
 const value=minutes||Number($("mon-window").value);
 try{const data=await api("/api/monitoring?minutes="+value);monBanner(data.mixed);if(minutes&&currentView!=="monitor")return;monRender(data);text("mon-error","");}
 catch(error){if(currentView==="monitor")text("mon-error",error.message);}
}
function monCard(title,stats,cls){
 const card=element("article","status-card mon-card"),top=element("div","card-top");top.append(element("span","",title),element("span","badge "+cls,stats?(stats.errors?"오류 "+stats.errors+"건":"오류 없음"):"요청 없음"));
 card.append(top,element("h2","",(stats?stats.requests:0).toLocaleString("ko-KR")+"건"));
 const split=element("div","split");
 if(stats){split.append(element("span","","채팅 "+stats.chat+"건"),element("span","","평균 "+(stats.avg_ms??"—")+" ms"),element("span","","95% "+(stats.p95_ms??"—")+" ms"));
  const versions=Object.entries(stats.versions||{}).map(([v,n])=>v+" "+n+"건").join(" · ");if(versions)split.append(element("span","","버전 "+versions));}
 card.append(split);return card;
}
function monRender(data){
 $("mon-cards").replaceChildren(monCard("챗봇 서버 1",data.nodes.node1,data.nodes.node1&&data.nodes.node1.errors?"warn":"ok"),monCard("챗봇 서버 2",data.nodes.node2,data.nodes.node2&&data.nodes.node2.errors?"warn":"ok"),monCard("처리할 서버 없음 (503)",data.nodes.none,data.nodes.none?"fail":"neutral"));
 text("mon-bucket",(data.bucket_seconds/60)+"분 단위");text("mon-total","기간 안 전체 "+data.total.toLocaleString("ko-KR")+"건"+(data.versions_seen.length?" · 응답한 버전 "+data.versions_seen.join(", "):""));
 const W=720,H=180,L=34,B=22,n=data.slots.length,bw=(W-L-6)/n;
 const totals=data.slots.map((_,i)=>(data.series.node1?.[i]||0)+(data.series.node2?.[i]||0)+(data.series.none?.[i]||0));
 const max=Math.max(4,...totals),y=v=>(H-B)-(v/max)*(H-B-10);
 const ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 "+W+" "+H);
 const add=(tag,attrs,label)=>{const e=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);if(label!==undefined)e.textContent=label;svg.append(e);return e;};
 data.errors.forEach((e,i)=>{if(e)add("rect",{x:L+i*bw,y:0,width:bw,height:H-B,class:"err-band"});});
 add("line",{x1:L,x2:W,y1:H-B,y2:H-B,class:"axis"});add("line",{x1:L,x2:W,y1:y(max),y2:y(max),class:"axis"});
 add("text",{x:L-6,y:H-B+3,"text-anchor":"end"},"0");add("text",{x:L-6,y:y(max)+3,"text-anchor":"end"},String(max));
 data.slots.forEach((_,i)=>{let base=0;for(const [key,cls] of [["node1","bar-n1"],["node2","bar-n2"],["none","bar-none"]]){const v=data.series[key]?.[i]||0;if(!v)continue;const top=y(base+v),bottom=y(base);add("rect",{x:L+i*bw+bw*0.15,y:top,width:Math.max(1,bw*0.7),height:Math.max(1,bottom-top),class:cls});base+=v;}});
 for(const i of [0,Math.floor(n/2),n-1])add("text",{x:L+i*bw+bw/2,y:H-6,"text-anchor":i===0?"start":i===n-1?"end":"middle"},monTime(data.slots[i]).slice(0,5));
 $("mon-chart").replaceChildren(svg);
 $("mon-recent").replaceChildren();
 for(const r of data.recent){const tr=document.createElement("tr");const ok=r.status&&r.status<500;
  for(const v of [monTime(r.t),r.node?"서버 "+r.node.slice(-1):"없음",r.version||"—",monKind[r.kind]||r.kind,r.status?String(r.status):"연결 실패",r.ms+" ms"]){tr.append(element("td","",v));}
  if(!ok)tr.className="fail-row";$("mon-recent").append(tr);}
 if(!data.recent.length){const tr=document.createElement("tr"),td=element("td","empty","기간 안에 기록된 요청이 없습니다. 분산기(8080)를 거친 요청만 기록됩니다.");td.colSpan=6;tr.append(td);$("mon-recent").append(tr);}
}
$("mon-window").addEventListener("change",()=>monRefresh());
