const roles = [
  {id:'planner',name:'Planner',sub:'规划',avatar:'PL'},
  {id:'implementer',name:'Builder',sub:'实现',avatar:'IM'},
  {id:'reviewer',name:'Reviewer',sub:'审查',avatar:'RV'},
  {id:'tester',name:'Tester',sub:'验证',avatar:'TS'},
  {id:'fixer',name:'Fixer',sub:'返工',avatar:'FX'}
];
const $ = (id) => document.getElementById(id);
let currentTask = null, timer = null, seen = 0, roleHistory = new Set();

function renderRoles(active=[]){
  const activeRoles=Array.isArray(active)?active:[active].filter(Boolean);
  $('roles').innerHTML=roles.map(r=>`<div class="role ${activeRoles.includes(r.id)?'active':''} ${roleHistory.has(r.id)?'complete':''}"><div class="avatar">${r.avatar}</div><strong>${r.name}</strong><small>${r.sub}</small></div>`).join('');
}
function escapeHtml(v){const d=document.createElement('div');d.textContent=String(v??'');return d.innerHTML}
function summarize(event){
  const d=event.detail||{};
  if(event.event==='role_assigned') return `${d.name} 接手：${d.objective}`;
  if(event.event==='state_transition') return `${d.state} · ${d.note}`;
  if(event.event==='implementation') return `${d.success?'提交成功':'提交失败'} · ${d.summary}${d.changed_files?.length?'\n变更：'+d.changed_files.join('、'):''}${d.error?'\n'+d.error:''}`;
  if(event.event==='verification') return `${d.passed?'验证通过':'验证未通过'} · ${d.summary}${d.feedback?.length?'\n反馈：'+d.feedback.join('；'):''}`;
  if(event.event==='review') return `${d.passed?'审查通过':'审查未通过'} · ${d.summary}${d.feedback?.length?'\n反馈：'+d.feedback.join('；'):''}`;
  if(event.event==='parallel_stage_started') return `${(d.roles||[]).join(' + ')} 同时开始工作`;
  if(event.event==='result_envelope') return `${d.producer_role} 提交 ${d.result_type} 结果 · task version ${d.task_version}`;
  if(event.event==='task_started') return `目标：${d.objective}`;
  return JSON.stringify(d);
}
function addEvent(event,index){
  if(index===0)$('timeline').innerHTML='';
  const el=document.createElement('article');el.className=`event ${event.event}`;
  const icon={task_started:'IN',role_assigned:'→',state_transition:'ST',implementation:'IM',verification:'TS'}[event.event]||'·';
  const time=event.timestamp?new Date(event.timestamp).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'';
  el.innerHTML=`<div class="event-marker">${icon}</div><div class="event-card"><div class="event-top"><strong>${escapeHtml(event.title)}</strong><time>${time}</time></div><div class="event-detail">${escapeHtml(summarize(event))}</div></div>`;
  $('timeline').appendChild(el);$('timeline').scrollTop=$('timeline').scrollHeight;
}
async function poll(){
  if(!currentTask)return;
  try{
    const res=await fetch(`/api/tasks/${currentTask}`);const data=await res.json();
    (data.events||[]).slice(seen).forEach((e,i)=>{if(e.event==='role_assigned'&&e.detail?.name)roleHistory.add(e.detail.name);addEvent(e,seen+i)});
    seen=(data.events||[]).length;$('event-count').textContent=`${seen} 条事件`;renderRoles(data.active_roles||data.active_role);
    const active=(data.active_roles||[]).filter(Boolean);$('process-caption').textContent=active.length?`${active.join(' + ')} 正在并行工作`:`状态：${data.status}`;
    if(['completed','failed'].includes(data.status)){
      clearInterval(timer);timer=null;$('submit').disabled=false;$('submit').querySelector('span').textContent='启动新任务';
      $('live-badge').textContent=data.status==='completed'?'DONE':'FAILED';$('live-badge').className=`live-badge ${data.status==='completed'?'done':''}`;
      showResult(data);return;
    }
  }catch(e){console.error(e)}
}
function showResult(data){
  const result=$('result');result.hidden=false;
  if(data.status==='completed'){
    $('result-title').textContent='项目已生成并通过验证';$('result-summary').textContent=data.result.summary;
    $('file-list').innerHTML=(data.result.files||[]).map(f=>`<span>${escapeHtml(f)}</span>`).join('');
    $('result-meta').innerHTML=`${escapeHtml(data.result.provider)} / ${escapeHtml(data.result.model)}<br>${data.result.attempts} 次尝试<br>${escapeHtml(data.result.output)}`;
  }else{$('result-title').textContent='任务未能完成';$('result-summary').textContent=data.error||'已达到最大尝试次数';$('file-list').innerHTML='';$('result-meta').textContent='请检查事件反馈后重试';}
  result.scrollIntoView({behavior:'smooth',block:'center'});
}
$('task-form').addEventListener('submit',async(e)=>{
  e.preventDefault();clearInterval(timer);seen=0;roleHistory=new Set();renderRoles();$('result').hidden=true;
  $('timeline').innerHTML='<div class="empty-state"><div class="orbit"><i></i><i></i><i></i></div><h3>正在建立安全工作区</h3><p>准备角色、上下文和权限边界。</p></div>';
  $('submit').disabled=true;$('submit').querySelector('span').textContent='协作进行中';$('live-badge').textContent='LIVE';$('live-badge').className='live-badge running';
  try{
    const res=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({requirement:$('requirement').value,name:$('name').value,max_attempts:Number($('attempts').value)})});
    const data=await res.json();if(!res.ok)throw new Error(data.error||'无法启动任务');currentTask=data.id;await poll();timer=setInterval(poll,700);
  }catch(err){$('submit').disabled=false;$('live-badge').textContent='ERROR';$('live-badge').className='live-badge';$('timeline').innerHTML=`<div class="empty-state"><h3>无法启动</h3><p>${escapeHtml(err.message)}</p></div>`;}
});
renderRoles();
