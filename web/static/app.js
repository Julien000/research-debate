const $ = (id) => document.getElementById(id);
let selected = location.hash.slice(1), tab = 'activity', fileName = '', fileKey = '', current = null, stages = [], polling = false;
const labels = {queued:'准备中',running:'研究中',needs_input:'等待补充',completed:'已完成',failed:'未完成',cancelled:'已停止',interrupted:'已中断'};
const date = (value) => new Date(value).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
function notice(message) { $('notice').textContent = message; $('notice').hidden = false; }
async function api(path, data) {
  const response = await fetch('/api' + path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401) { $('workspace').hidden = true; $('login').hidden = false; }
    throw new Error(result.error || '请求失败，请稍后再试。');
  }
  return result;
}
function element(tag, text, cls) { const e = document.createElement(tag); if(text !== undefined) e.textContent = text; if(cls) e.className = cls; return e; }
function selectJob(id) { selected = id; location.hash = id; tab = 'activity'; fileKey=''; fileName=''; current=null; $('notice').hidden=true; refresh().catch(e=>notice(e.message)); }
function fileUrl(name) { return '/api/jobs/' + selected + '/files/' + name.split('/').map(encodeURIComponent).join('/'); }
async function showContent() {
  document.querySelectorAll('[data-tab]').forEach(button=>button.setAttribute('aria-selected', String(button.dataset.tab === tab)));
  $('activity').hidden = tab !== 'activity'; $('preview').hidden = tab === 'activity';
  if (tab === 'activity' || !current) return;
  const name = tab === 'debate' ? 'debate.md' : tab === 'report' ? 'report.md' : fileName;
  const item = current.files.find(f=>f.name === name);
  $('file-name').textContent = name || '选择右侧材料';
  $('download').hidden = !item;
  if (!item) { $('file-content').textContent = name ? '这份材料尚未生成。运行完成相应阶段后会自动显示。' : '点击研究材料，查看每一步的原始内容。'; fileKey=''; return; }
  $('download').href = fileUrl(name) + '?download=1';
  const key = selected + ':' + name + ':' + item.size;
  if (key === fileKey) return;
  const jobAtRequest = selected;
  const tabAtRequest = tab;
  const response = await fetch(fileUrl(name));
  if (!response.ok) throw new Error('无法读取材料，请刷新或重新登录。');
  const text = await response.text();
  if (selected !== jobAtRequest || tab !== tabAtRequest || (tab === 'file' && fileName !== name)) return;
  $('file-content').textContent = text;
  fileKey=key;
}
async function refresh() {
  if (polling) return;
  polling=true;
  try {
    const result = await api('/jobs'); stages=result.stages;
    $('login').hidden=true; $('workspace').hidden=false;
    $('job-count').textContent=result.jobs.length;
    $('jobs').replaceChildren();
    for (const job of result.jobs) {
      const button=element('button', undefined, 'job-item'+(job.id===selected?' active':''));
      button.append(element('strong',job.topic),element('small',(labels[job.status]||job.status)+' · '+date(job.created_at)));
      button.onclick=()=>selectJob(job.id); $('jobs').append(button);
    }
    $('compose').hidden=Boolean(selected); $('detail').hidden=!selected;
    $('breadcrumb').textContent=selected?'研究记录':'新的研究';
    if (!selected) return;
    const jobAtRequest=selected;
    const data=await api('/jobs/'+selected);
    if(jobAtRequest!==selected)return;
    current=data;
    const job=data.job;
    $('job-title').textContent=job.topic;
    $('job-meta').textContent=date(job.created_at)+' 创建 · '+(labels[job.status]||job.status);
    $('cancel').hidden=!['running','queued','needs_input'].includes(job.status);
    $('job-error').hidden=!job.error; $('job-error').textContent=job.error||'';
    $('stage-list').replaceChildren();
    const index=stages.findIndex(s=>s[0]===job.stage);
    stages.forEach(([key,label],i)=>$('stage-list').append(element('span',(i<index||job.status==='completed'?'✓ ':String(i+1)+' ')+label,
      'stage'+(job.status==='completed'||i<index?' done':i===index?' current':''))));
    $('clarify').hidden=job.status!=='needs_input';
    $('questions').replaceChildren(...(job.questions||[]).map(q=>element('li',q)));
    const events=$('events'); const atBottom=events.scrollHeight-events.scrollTop-events.clientHeight<40;
    events.replaceChildren(...data.events.map(event=>{const li=element('li');li.append(element('time',new Date(event.time).toLocaleTimeString('zh-CN')),element('span',event.message));return li;}));
    if(atBottom)events.scrollTop=events.scrollHeight;
    $('file-count').textContent=data.files.length;
    $('files').replaceChildren();
    for(const item of data.files){
      const button=element('button',undefined,'file-item'+(item.name===fileName?' active':''));
      button.append(element('span',item.name),element('small',(item.size/1024).toFixed(1)+' KB'));
      button.onclick=()=>{fileName=item.name;tab='file';showContent().catch(e=>notice(e.message));};
      $('files').append(button);
    }
    await showContent();
  } finally {polling=false;}
}
async function submit(form, action) {
  const button=form.querySelector('button[type="submit"],button'); button.disabled=true; $('notice').hidden=true;
  try { await action(); } catch(e) {notice(e.message);} finally {button.disabled=false;}
}
$('login-form').onsubmit=e=>{e.preventDefault();submit(e.currentTarget,async()=>{await api('/login',{token:$('token').value});$('token').value='';await refresh();});};
$('new-form').onsubmit=e=>{e.preventDefault();submit(e.currentTarget,async()=>{const job=await api('/jobs',{topic:$('topic').value,context:$('context').value});selectJob(job.id);});};
$('answer-form').onsubmit=e=>{e.preventDefault();submit(e.currentTarget,async()=>{await api('/jobs/'+selected+'/answer',{answer:$('answer').value});$('answer').value='';await refresh();});};
$('new-button').onclick=()=>selectJob('');
$('cancel').onclick=async()=>{try{await api('/jobs/'+selected+'/cancel',{});notice('已请求停止；正在检索时最多需等待当前请求结束。');await refresh();}catch(e){notice(e.message);}};
$('logout').onclick=async()=>{try{await api('/logout',{});$('workspace').hidden=true;$('login').hidden=false;}catch(e){notice(e.message);}};
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{tab=button.dataset.tab;showContent().catch(e=>notice(e.message));});
refresh().catch(()=>{});
setInterval(()=>{if(!$('workspace').hidden)refresh().catch(e=>notice(e.message));},2000);
