const $ = (id) => document.getElementById(id);
const roleOrder = ['planner','implementer','tester','reviewer','fixer'];
const roleMarks = {planner:'PL',implementer:'IM',tester:'TS',reviewer:'RV',fixer:'FX'};
const statusText = {pending:'等待',running:'运行中',success:'完成',failed:'失败',skipped:'未触发'};
let currentTask=null, timer=null, seen=0, selectedNode='planner', latestData=null;

function escapeHtml(value){const div=document.createElement('div');div.textContent=String(value??'');return div.innerHTML}
function formatTime(value){return value?new Date(value).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—'}
function formatDuration(value){if(value===null||value===undefined)return '—';return value<1000?`${value} ms`:`${(value/1000).toFixed(1)} s`}

function renderDag(data){
  const nodes=data?.nodes||{};
  const card=(id)=>{const node=nodes[id]||{id,label:id,status:'pending',summary:''};return `<button type="button" class="dag-node ${escapeHtml(node.status)} ${selectedNode===id?'selected':''}" data-node="${id}"><span class="dag-avatar">${roleMarks[id]||'AG'}</span><span><strong>${escapeHtml(node.label)}</strong><small>${escapeHtml(node.summary)}</small></span><em>${statusText[node.status]||node.status}</em></button>`};
  $('dag').innerHTML=`
    <div class="dag-row single">${card('planner')}</div><div class="dag-arrow">↓</div>
    <div class="dag-row single">${card('implementer')}</div><div class="dag-split"><span>↙</span><b>并行质量检查</b><span>↘</span></div>
    <div class="dag-row parallel">${card('tester')}${card('reviewer')}</div>
    <div class="dag-return">失败反馈 ↘　↙ 汇合结果</div>
    <div class="dag-row single">${card('fixer')}</div>`;
  $('dag').querySelectorAll('[data-node]').forEach(el=>el.addEventListener('click',()=>{selectedNode=el.dataset.node;renderDag(latestData);renderDetail(latestData)}));
  const running=Object.values(nodes).filter(node=>node.status==='running').map(node=>node.label);
  $('dag-caption').textContent=running.length?`${running.join(' + ')} 正在运行`:`状态：${data?.status||'idle'}`;
}

function summarize(event){
  const detail=event.detail||{};
  if(event.event==='agent_message')return `${detail.summary}${detail.payload&&Object.keys(detail.payload).length?'\n'+JSON.stringify(detail.payload,null,2):''}`;
  if(event.event==='state_transition')return `${detail.state} · ${detail.note}`;
  if(event.event==='parallel_stage_started')return `${(detail.roles||[]).join(' + ')} 同时开始工作`;
  return JSON.stringify(detail,null,2);
}

function renderTimeline(events){
  if(!events.length){$('timeline').innerHTML='<div class="empty-state"><div class="orbit"><i></i><i></i><i></i></div><h3>协作过程将在这里展开</h3><p>这里展示的是可审计事件，而不是模型的私有推理。</p></div>';return}
  $('timeline').innerHTML=events.map(event=>{
    const active=event.node_id===selectedNode?'selected':'';
    const icon={agent_message:'↗',state_transition:'ST',parallel_stage_started:'∥'}[event.event]||'·';
    return `<article class="event ${event.event} ${active}" data-node="${escapeHtml(event.node_id||'')}"><div class="event-marker">${icon}</div><div class="event-card"><div class="event-top"><strong>${escapeHtml(event.title)}</strong><time>${formatTime(event.timestamp)}</time></div><div class="event-detail">${escapeHtml(summarize(event))}</div></div></article>`;
  }).join('');
  $('timeline').querySelectorAll('[data-node]').forEach(el=>el.addEventListener('click',()=>{if(el.dataset.node){selectedNode=el.dataset.node;renderDag(latestData);renderDetail(latestData);renderTimeline(latestData.events||[])}}));
}

function renderDetail(data){
  const node=data?.nodes?.[selectedNode];
  if(!node)return;
  const events=(data.events||[]).filter(event=>event.node_id===selectedNode);
  $('detail-status').textContent=statusText[node.status]||node.status;
  $('node-detail').innerHTML=`
    <div class="detail-head"><span class="dag-avatar">${roleMarks[selectedNode]}</span><div><h3>${escapeHtml(node.label)}</h3><p>${escapeHtml(node.summary)}</p></div><b class="status-chip ${escapeHtml(node.status)}">${statusText[node.status]||node.status}</b></div>
    <dl><div><dt>开始</dt><dd>${formatTime(node.started_at)}</dd></div><div><dt>结束</dt><dd>${formatTime(node.finished_at)}</dd></div><div><dt>耗时</dt><dd>${formatDuration(node.duration_ms)}</dd></div><div><dt>尝试</dt><dd>${node.attempt||0}</dd></div></dl>
    <section><h4>允许能力</h4><div class="tag-list">${(node.permissions||[]).map(item=>`<span>${escapeHtml(item)}</span>`).join('')}</div></section>
    <section><h4>最近结果</h4><p class="detail-copy">${escapeHtml(node.last_summary)}</p></section>
    <section><h4>产物</h4><div class="artifact-list">${(node.artifacts||[]).length?node.artifacts.map(item=>`<code>${escapeHtml(item)}</code>`).join(''):'<small>暂无文件产物</small>'}</div></section>
    <section><h4>关联事件</h4><p class="detail-copy">${events.length} 条可审计事件</p></section>`;
}

async function poll(){
  if(!currentTask)return;
  try{
    const response=await fetch(`/api/tasks/${currentTask}`);const data=await response.json();latestData=data;
    seen=(data.events||[]).length;$('event-count').textContent=`${seen} 条事件`;
    renderDag(data);renderTimeline(data.events||[]);renderDetail(data);
    const active=(data.active_roles||[]).filter(Boolean);$('process-caption').textContent=active.length?`${active.join(' + ')} 正在工作`:`状态：${data.status}`;
    if(['completed','failed'].includes(data.status)){
      clearInterval(timer);timer=null;$('submit').disabled=false;$('submit').querySelector('span').textContent='启动新任务';
      $('live-badge').textContent=data.status==='completed'?'DONE':'FAILED';$('live-badge').className=`live-badge ${data.status==='completed'?'done':'failed'}`;showResult(data);
    }
  }catch(error){console.error(error)}
}

function showResult(data){
  const result=$('result');result.hidden=false;
  if(data.status==='completed'){
    $('result-title').textContent='项目已生成并通过验证';$('result-summary').textContent=data.result.summary;
    $('file-list').innerHTML=(data.result.files||[]).map(file=>`<span>${escapeHtml(file)}</span>`).join('');
    $('result-meta').innerHTML=`${escapeHtml(data.result.provider)} / ${escapeHtml(data.result.model)}<br>${data.result.attempts} 次尝试<br>${escapeHtml(data.result.output)}`;
  }else{$('result-title').textContent='任务未能完成';$('result-summary').textContent=data.error||'已达到最大尝试次数';$('file-list').innerHTML='';$('result-meta').textContent='请检查事件反馈后重试'}
}

$('task-form').addEventListener('submit',async(event)=>{
  event.preventDefault();clearInterval(timer);seen=0;selectedNode='planner';latestData=null;$('result').hidden=true;
  $('timeline').innerHTML='<div class="empty-state"><div class="orbit"><i></i><i></i><i></i></div><h3>正在建立安全工作区</h3><p>准备角色、上下文和权限边界。</p></div>';
  $('submit').disabled=true;$('submit').querySelector('span').textContent='协作进行中';$('live-badge').textContent='LIVE';$('live-badge').className='live-badge running';
  try{
    const response=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({requirement:$('requirement').value,name:$('name').value,max_attempts:Number($('attempts').value)})});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'无法启动任务');currentTask=data.id;await poll();timer=setInterval(poll,700);
  }catch(error){$('submit').disabled=false;$('live-badge').textContent='ERROR';$('live-badge').className='live-badge failed';$('timeline').innerHTML=`<div class="empty-state"><h3>无法启动</h3><p>${escapeHtml(error.message)}</p></div>`}
});

latestData={status:'idle',events:[],nodes:Object.fromEntries(roleOrder.map(id=>[id,{id,label:id[0].toUpperCase()+id.slice(1),summary:'等待任务',permissions:[],status:'pending',attempt:0,artifacts:[],last_summary:'等待 Harness 调度'}]))};
renderDag(latestData);renderDetail(latestData);
