/* PERPERIS QA — standalone ticketing and deterministic QA analytics.
   Shell + Today dashboard. All figures come from GET /api/analytics and GET /api/tickets. */
'use strict';
const $ = id => document.getElementById(id);
const STATUSES = ['reported', 'triaged', 'in_progress', 'fixed', 'deployed', 'verified', 'closed', 'reopened'];
const DONE = ['verified', 'closed'];
const SEVERITIES = ['critical', 'high', 'medium', 'low'];
const SEV_RANK = {critical: 0, high: 1, medium: 2, low: 3};
const LABELS = {reported:'Reported',triaged:'Triaged',in_progress:'In progress',fixed:'Fixed',deployed:'Deployed',verified:'Verified',closed:'Closed',reopened:'Reopened',ui_ux:'UI / UX',coach_ai:'Coach / AI',unset:'No severity',bug:'Bug',task:'Task'};
let tickets = [], analytics = null, editing = null, lastFocus = null, selectedRound = null, loaded = false, session = {role:'Admin'};
/* Statuses each role may set. Mirrors the server's role matrix (the server enforces it). */
const ROLE_STATUSES = {Developer:['triaged','in_progress','fixed','deployed'], Tester:['verified','closed','reopened']};
const allowedStatuses = () => STATUSES.filter(s => s!=='reported' && (session.role==='Admin' || (ROLE_STATUSES[session.role]||[]).includes(s)));
const managed = () => qaIdentity.config?.mode === 'identity';
/* Tickets are GitHub issues; locally an in-memory stand-in (see docs/QA-GITHUB-FRONTEND.md). */
const githubMode = () => qaIdentity.config?.tickets_source === 'github';

const label = value => LABELS[value] || String(value || '—').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const toNum = x => Number.isFinite(+x) ? +x : 0;
const ticketNo = id => `#${id}`;
const formatDate = value => value ? new Date(value).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'}) : '—';
function ago(value){
  const t = Date.parse(value); if(!Number.isFinite(t)) return '—';
  const m = Math.max(0, Math.round((Date.now()-t)/60000));
  if(m < 1) return 'Just now'; if(m < 60) return `${m}m ago`;
  const h = Math.round(m/60); if(h < 24) return `${h}h ago`;
  const d = Math.round(h/24); if(d < 14) return `${d}d ago`;
  return formatDate(value);
}
function safeUrl(url){try {const u=new URL(url);return u.protocol==='https:'?u.href:'#';}catch{return '#';}}
/* Nothing is kept in browser storage. In managed sign-in the reporter is the signed-in member. */
const defaultReporter = () => managed() ? (session.name||'') : 'Product Owner';

async function api(url,{method='GET',body}={}) {
  const res = await fetch(url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-QA-Request':'1'},body:body ? JSON.stringify(body) : undefined});
  let data; try {data=await res.json()}catch {data={detail:'Unexpected response from QA server.'}}
  if(!res.ok) {const message = typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||'Request failed'); const e=new Error(message);e.status=res.status;throw e;}
  return data;
}
function toggleSession(authed){$('workspace').classList.toggle('hidden',!authed);$('login').classList.toggle('hidden',authed);}
async function initial(){
  try {
    qaIdentity.config=await api('/api/auth/config');
    $('email-label').classList.toggle('hidden',!managed());$('identity-actions').classList.toggle('hidden',!managed());$('email').required=managed();
    session=await api('/api/me');toggleSession(true);
    $('who').textContent=managed()?`${session.name} · ${session.role}`:'';
    applyIdentityToQuick();applyTicketSource();route();await refresh();
  }
  catch(e){toggleSession(false);if(e.status===503) $('login-error').textContent=e.message;}
}
async function refresh(){
  try {
    const [a,t]=await Promise.all([api('/api/analytics'),api('/api/tickets')]);
    analytics=a;tickets=t.tickets;loaded=true;renderAll();
    if(a.data_stale){$('load-error-text').textContent=`GitHub can't be reached right now. Showing data from ${new Date(a.data_as_of).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'})}.`;$('load-error').classList.remove('hidden');}
    else $('load-error').classList.add('hidden');
  } catch(e){
    if(e.status===401){toggleSession(false);return;}
    console.error(e);
    $('load-error-text').textContent=`Unable to load QA data: ${e.message}`;
    $('load-error').classList.remove('hidden');
    if(!loaded){$('hero-title').textContent='No data loaded.';$('hero-sub').textContent='The numbers on this screen come only from the QA server. Retry when it is reachable.';}
  }
}

/* ---------- routing ---------- */
const VIEWS = ['today','tickets'];
function currentView(){const v=location.hash.slice(1);return VIEWS.includes(v)?v:'today';}
function route(){
  const view=currentView();
  VIEWS.forEach(v=>$(`view-${v}`).classList.toggle('hidden',v!==view));
  document.querySelectorAll('[data-view]').forEach(a=>{if(a.dataset.view===view)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  document.title=`PERPERIS / QA — ${view==='today'?'Today':'Tickets'}`;
  window.scrollTo(0,0);
}
window.addEventListener('hashchange',route);

/* ---------- derived, deterministic figures ---------- */
const criticalOpen = () => tickets.filter(t=>t.severity==='critical' && !DONE.includes(t.status));
function latestRound(){return analytics?.current_round || analytics?.rounds?.at(-1)?.round || null;}
function activeRound(){
  const rounds=(analytics?.rounds||[]).map(r=>r.round);
  return rounds.includes(selectedRound)?selectedRound:latestRound();
}

function renderAll(){
  renderHeader();renderHero();renderMetrics();renderRoundPanel();renderChart();
  renderSeverity();renderFlow();renderAttention();renderActivity();
  renderFilters();renderTable();prefillQuick();
}
function renderHeader(){
  $('nav-count').textContent=analytics.open;
  const cur=latestRound();
  $('today-labels').innerHTML=cur?`<div><span>Round ${esc(cur)}</span><span>${analytics.total} tickets</span></div>`:'';
  const strip=$('round-strip');const rounds=analytics.rounds||[];const active=activeRound();
  strip.innerHTML=rounds.length?rounds.map(r=>`<button type="button" class="round${r.round===active?' is-selected':''}" data-round="${esc(r.round)}" aria-pressed="${r.round===active}">${esc(r.round)}<small>${toNum(r.found)} found</small></button>`).join(''):'<p class="note">No testing rounds yet. The first ticket creates one.</p>';
  strip.querySelectorAll('[data-round]').forEach(b=>b.addEventListener('click',()=>{selectedRound=b.dataset.round;renderHeader();renderRoundPanel();}));
}
function renderHero(){
  const hero=$('hero'),crit=criticalOpen().length,total=analytics.total,backlog=analytics.backlog;
  let cls='hero-empty',title,sub;
  if(total===0){title='No reports yet.';sub='The timeline starts with the first ticket. No example figures are shown.';}
  else if(crit>0){cls='hero-critical';title=`${crit} critical open.`;sub=`${backlog} of ${total} reports are not verified. ${analytics.fixed} have a recorded first fix.`;}
  else if(backlog>0){cls='hero-attention';title=`${backlog} not verified.`;sub=`${total} reported · ${analytics.fixed} fixed in code · ${analytics.verified} verified. No critical ticket is open.`;}
  else {cls='hero-clear';title=`All ${total} verified.`;sub='Every reported bug has a recorded verification. This says nothing about areas that have not been tested.';}
  hero.className=`hero ${cls}`;$('hero-title').textContent=title;$('hero-sub').textContent=sub;
}
function renderMetrics(){
  const crit=criticalOpen().length;
  /* The k-* ids are kept from the previous UI; tests/browser_smoke.py and identity_browser_smoke.py read them. */
  const items=[
    ['k-total','Total bugs',analytics.total,'Reported, all rounds'],['k-open','Open',analytics.open,'Not verified or closed'],
    ['k-fixed','Fixed',analytics.fixed,'Ever fixed in code'],['k-verified','Verified',analytics.verified,'Ever verified'],
    ['k-critical','Critical open',crit,'Critical, not verified or closed'],['k-backlog','Backlog',analytics.backlog,'Found minus verified']];
  $('metrics').innerHTML=items.map(([id,name,value,def])=>`<div class="metric${id==='k-critical'&&value>0?' is-alert':''}"><dt>${esc(name)}</dt><dd class="num" id="${id}">${toNum(value)}</dd><dd class="def">${esc(def)}</dd></div>`).join('');
}
function renderRoundPanel(){
  const rounds=analytics.rounds||[],active=activeRound(),r=rounds.find(x=>x.round===active);
  if(!r){$('round-panel').innerHTML='<p class="empty-line">No round selected.</p>';return;}
  $('h-round').textContent=`Round ${r.round}${r.round===latestRound()?' · current':''}`;
  $('round-panel').innerHTML=`<div class="round-panel"><div><b>${toNum(r.found)}</b><span>Found</span></div><div><b>${toNum(r.fixed)}</b><span>Fixed</span></div><div><b>${toNum(r.verified)}</b><span>Verified</span></div></div><button class="link-btn" type="button" id="round-tickets">View ${esc(r.round)} tickets</button>`;
  $('round-tickets').addEventListener('click',()=>{renderFilters();$('round-filter').value=r.round;renderTable();location.hash='#tickets';});
}

/* ---------- chart ---------- */
function renderChart(){
  const rounds=analytics.rounds||[],mount=$('chart'),note=$('chart-note');
  $('legend-backlog').classList.toggle('hidden',!$('show-backlog').checked);
  if(!rounds.length){mount.innerHTML='<p class="chart-empty">The timeline starts with the first ticket. No example numbers are shown.</p>';note.textContent='';return;}
  let cf=0,cv=0;const backlog=rounds.map(r=>{cf+=toNum(r.found);cv+=toNum(r.verified);return Math.max(0,cf-cv);});
  const series=[{key:'found',name:'FOUND',vals:rounds.map(r=>toNum(r.found))},{key:'fixed',name:'FIXED',vals:rounds.map(r=>toNum(r.fixed))},{key:'verified',name:'VERIFIED',vals:rounds.map(r=>toNum(r.verified))}];
  if($('show-backlog').checked) series.push({key:'backlog',name:'BACKLOG',vals:backlog});
  const W=Math.max(380,rounds.length*56+140),H=300,p={l:40,r:92,t:34,b:46},iw=W-p.l-p.r,ih=H-p.t-p.b;
  const ceiling=Math.max(4,Math.ceil(Math.max(...series.flatMap(s=>s.vals))/4)*4);
  const xp=i=>p.l+(rounds.length===1?iw/2:i*iw/(rounds.length-1));
  const yp=v=>p.t+ih-(v/ceiling)*ih;
  const grid=Array.from({length:5},(_,i)=>{const v=ceiling*i/4,y=yp(v);return `<line class="grid-line" x1="${p.l}" y1="${y}" x2="${W-p.r}" y2="${y}"/><text class="axis-text" x="${p.l-8}" y="${y+4}" text-anchor="end">${v}</text>`;}).join('');
  const xlabels=rounds.map((r,i)=>`<text class="round-text" x="${xp(i)}" y="${H-p.b+26}" text-anchor="middle">${esc(r.round)}</text>`).join('');
  const marker=(key,x,y)=>key==='found'?`<circle class="m-found" cx="${x}" cy="${y}" r="5">`:key==='fixed'?`<rect class="m-fixed" x="${x-4.5}" y="${y-4.5}" width="9" height="9">`:key==='verified'?`<path class="m-verified" d="M${x} ${y-6.5}L${x+6.5} ${y+5}L${x-6.5} ${y+5}Z">`:`<circle class="m-backlog" cx="${x}" cy="${y}" r="3.5">`;
  const close=key=>key==='found'||key==='backlog'?'</circle>':key==='fixed'?'</rect>':'</path>';
  const lines=series.map(s=>{
    const pts=s.vals.map((v,i)=>`${xp(i)},${yp(v)}`).join(' ');
    const dots=s.vals.map((v,i)=>`${marker(s.key,xp(i),yp(v))}<title>${esc(rounds[i].round)} · ${s.name}: ${v}</title>${close(s.key)}`).join('');
    return `<polyline class="ln-${s.key}" points="${pts}"/>${dots}`;
  }).join('');
  const ends=series.map(s=>({y:yp(s.vals.at(-1)),text:`${s.name} ${s.vals.at(-1)}`})).sort((a,b)=>a.y-b.y);
  for(let i=1;i<ends.length;i++) if(ends[i].y-ends[i-1].y<13) ends[i].y=ends[i-1].y+13;
  const floor=p.t+ih;   /* keep labels above the round labels row; push the cluster upwards instead */
  if(ends.at(-1).y>floor){ends.at(-1).y=floor;for(let i=ends.length-2;i>=0;i--) if(ends[i+1].y-ends[i].y<13) ends[i].y=ends[i+1].y-13;}
  const endLabels=ends.map(e=>`<text class="end-label" x="${W-p.r+10}" y="${e.y+4}">${esc(e.text)}</text>`).join('');
  const ci=rounds.findIndex(r=>r.round===analytics.crossover);
  const cross=ci<0?'':`<line class="cross-line" x1="${xp(ci)}" y1="${p.t}" x2="${xp(ci)}" y2="${p.t+ih}"/><rect class="cross-tag" x="${Math.max(2,Math.min(W-114,xp(ci)-56))}" y="4" width="112" height="20"/><text class="cross-text" x="${Math.max(2,Math.min(W-114,xp(ci)-56))+56}" y="18">FIXES ≥ FOUND</text>`;
  const table=`<table class="sr-only"><caption>Bugs by testing round</caption><thead><tr><th>Round</th>${series.map(s=>`<th>${s.name}</th>`).join('')}</tr></thead><tbody>${rounds.map((r,i)=>`<tr><td>${esc(r.round)}</td>${series.map(s=>`<td>${s.vals[i]}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  mount.innerHTML=`<svg viewBox="0 0 ${W} ${H}" ${W>520?`style="min-width:${W}px" `:''}role="img" aria-label="Bugs found, fixed and verified per testing round">${grid}${cross}${lines}${xlabels}${endLabels}</svg>${table}`;
  const last=rounds.at(-1);
  let text=`${last.round}: ${toNum(last.found)} found, ${toNum(last.fixed)} first fixes, ${toNum(last.verified)} verified. ${analytics.backlog} report${analytics.backlog===1?'':'s'} not verified overall.`;
  if(analytics.crossover) text+=` Fixes first met or passed discoveries in ${analytics.crossover} (per round; this does not mean the backlog is cleared).`;
  note.textContent=text;
}

/* ---------- distributions, attention, activity ---------- */
function barRows(entries,total,cls){
  return entries.map(([key,count])=>`<div class="bar-row"><span>${esc(label(key))}</span><div class="bar-track"><div class="bar-fill${cls?` ${cls}${key}`:''}" style="width:${total?toNum(count)/total*100:0}%"></div></div><b>${toNum(count)}</b></div>`).join('');
}
function renderSeverity(){
  const total=Math.max(1,analytics.total);
  $('severity').innerHTML=analytics.total?barRows(SEVERITIES.map(k=>[k,toNum(analytics.severity[k])]),total,'sev-'):'<p class="empty-line">No tickets yet.</p>';
}
function renderFlow(){
  const counts=Object.fromEntries(STATUSES.map(s=>[s,0]));tickets.forEach(t=>{if(t.status in counts)counts[t.status]++;});
  $('flow').innerHTML=tickets.length?barRows(STATUSES.map(s=>[s,counts[s]]),tickets.length,''):'<p class="empty-line">No tickets yet.</p>';
}
function attentionItems(){
  const out=[];
  tickets.forEach(t=>{
    let why=null;const open=!DONE.includes(t.status);
    if(t.severity==='critical'&&open) why='Critical · open';
    else if(t.status==='reopened') why='Reopened';
    else if(t.severity==='high'&&open) why='High · open';
    else if(t.status==='fixed'||t.status==='deployed') why='Awaiting verification';
    if(why) out.push({t,why});
  });
  const rank=w=>w.startsWith('Critical')?0:w==='Reopened'?1:w.startsWith('High')?2:3;
  return out.sort((a,b)=>rank(a.why)-rank(b.why)||String(b.t.updated_at).localeCompare(String(a.t.updated_at)));
}
function renderAttention(){
  const all=attentionItems(),list=all.slice(0,6);
  $('attention').innerHTML=list.length?list.map(({t,why})=>`<li><button type="button" class="attn" data-id="${t.id}"><span class="act-id">${ticketNo(t.id)}</span><span class="attn-title">${esc(t.title)}</span><span class="act-time">${esc(t.testing_round)}</span><span class="attn-why">${esc(why)}</span></button></li>`).join('')+(all.length>list.length?`<li class="note">+${all.length-list.length} more in Tickets.</li>`:''):'<li class="empty-line">Nothing needs attention by these rules.</li>';
  $('attention').querySelectorAll('[data-id]').forEach(b=>b.addEventListener('click',()=>openEdit(Number(b.dataset.id))));
}
function renderActivity(){
  const list=[...tickets].sort((a,b)=>String(b.updated_at).localeCompare(String(a.updated_at))).slice(0,8);
  $('activity').innerHTML=list.length?list.map(t=>`<li><button type="button" class="act" data-id="${t.id}"><span class="act-id">${ticketNo(t.id)}</span><span class="act-title">${esc(t.title)}</span><span class="act-time">${esc(ago(t.updated_at))}</span><span class="chip ${esc(t.status)}" style="grid-column:2/4">${esc(label(t.status))}</span></button></li>`).join(''):'<li class="empty-line">No activity yet. Report the first bug above.</li>';
  $('activity').querySelectorAll('[data-id]').forEach(b=>b.addEventListener('click',()=>openEdit(Number(b.dataset.id))));
}

/* ---------- ticket register ---------- */
function renderFilters(){
  const currentStatus=$('status-filter').value;const currentRound=$('round-filter').value;
  $('status-filter').innerHTML='<option value="">All statuses</option>'+STATUSES.map(s=>`<option value="${s}">${label(s)}</option>`).join('');
  $('status-filter').value=currentStatus;
  const rounds=[...new Set(tickets.map(t=>t.testing_round))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
  $('round-filter').innerHTML='<option value="">All rounds</option>'+rounds.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
  $('round-filter').value=rounds.includes(currentRound)?currentRound:'';
}
const meter = sev => { const n={critical:4,high:3,medium:2,low:1}[sev]||0; return `<i aria-hidden="true">${[1,2,3,4].map(i=>`<b${i<=n?' class="on"':''}></b>`).join('')}</i>`; };
function renderTable(){
  const search=$('search').value.trim().toLowerCase(),status=$('status-filter').value,sev=$('severity-filter').value,round=$('round-filter').value;
  const list=tickets.filter(t=>(!status||t.status===status)&&(!sev||t.severity===sev)&&(!round||t.testing_round===round)&&(!search||[t.id,ticketNo(t.id),t.title,t.description,t.reporter,t.category,t.area,t.assigned_to,t.deploy_version].some(v=>String(v||'').toLowerCase().includes(search))));
  $('tickets-labels').innerHTML=tickets.length?`<div><span>${list.length} shown</span><span>${tickets.length} total</span></div>`:'';
  $('ticket-rows').innerHTML=list.map(t=>`<tr class="row" data-id="${t.id}" tabindex="0" aria-label="Open ${esc(ticketNo(t.id))} ${esc(t.title)}"><td class="row-id">${ticketNo(t.id)}</td><td class="row-title">${esc(t.title)}<span class="row-area">${esc(t.area||t.environment||'')}</span></td><td class="c-status"><span class="chip ${esc(t.status)}">${esc(label(t.status))}</span></td><td class="c-sev"><span class="sev sev-${esc(t.severity)}">${meter(t.severity)}${esc(label(t.severity))}</span></td><td class="m-assignee">${esc(t.assigned_to||'—')}</td><td class="m-round">${esc(t.testing_round)}</td><td class="m-updated">${esc(ago(t.updated_at))}</td></tr>`).join('');
  $('empty').classList.toggle('hidden',list.length>0);
  $('ticket-rows').querySelectorAll('.row').forEach(row=>{
    row.addEventListener('click',()=>openEdit(Number(row.dataset.id)));
    row.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openEdit(Number(row.dataset.id));}});
  });
}

/* ---------- quick report ---------- */
function applyIdentityToQuick(){
  const r=$('q-reporter');r.readOnly=managed();if(managed()||!r.value) r.value=defaultReporter();
  $('quick-note').textContent=qaScreenshots.enabled()?'Plain form. No AI. To attach screenshots, use Add details.':'Plain form. No AI.';
}
/* GitHub mode: reports become GitHub issues; status changes return in the next milestone (detail is read-only). */
function applyTicketSource(){
  $('quick-note').textContent=githubMode()
    ?`Creates a GitHub issue in ${qaIdentity.config.tickets_repo||'the PERPERIS repository'}, signed "Reported by ${session.name||'you'} via PERPERIS QA". Plain form, no AI.${qaScreenshots.enabled()?' To attach screenshots, use Add details.':''}`
    :'Local test data, not GitHub: reports are kept in memory and disappear when QA restarts.';
}
function prefillQuick(){
  if(!$('q-reporter').value) $('q-reporter').value=defaultReporter();
  if(!$('q-round').value) $('q-round').value=latestRound()||'R01';
}
$('quick-form').addEventListener('submit',async e=>{
  e.preventDefault();const btn=$('quick-submit'),msg=$('quick-error');
  btn.disabled=true;msg.className='error';msg.textContent='';
  try{
    const res=await api('/api/tickets',{method:'POST',body:{title:$('q-title').value,description:$('q-description').value,reporter:$('q-reporter').value,severity:$('q-severity').value,testing_round:$('q-round').value}});
    $('q-title').value='';$('q-description').value='';
    await refresh();msg.className='ok';msg.textContent=`Reported ${ticketNo(res.ticket.id)}.`;$('q-title').focus();
  }catch(err){if(err.status===401){toggleSession(false);return;}msg.textContent=err.message;}
  finally{btn.disabled=false;}
});
$('quick-details').addEventListener('click',()=>newTicket({title:$('q-title').value,description:$('q-description').value,severity:$('q-severity').value,testing_round:$('q-round').value,reporter:$('q-reporter').value}));

/* ---------- dialog ---------- */
function setModal(open){$('modal').classList.toggle('hidden',!open);document.body.style.overflow=open?'hidden':'';if(open){lastFocus=document.activeElement;setTimeout(()=>document.querySelector('#ticket-form input:not([type=hidden]):not(:disabled):not([readonly]), #ticket-form select:not(:disabled)')?.focus(),20);}else{editing=null;lastFocus?.focus?.();}}
/* Only the visible half of the dialog takes part in validation and submit. Required fields inside a
   hidden section otherwise make the browser refuse to submit ("invalid form control is not focusable"). */
function setDialogMode(create){
  document.querySelectorAll('#create-fields :is(input,select,textarea)').forEach(el=>{el.disabled=!create;});
  document.querySelectorAll('#update-fields :is(input,select,textarea)').forEach(el=>{el.disabled=create;});
}
function newTicket(prefill={}){
  editing=null;const form=$('ticket-form');form.reset();setDialogMode(true);
  $('create-fields').classList.remove('hidden');$('update-fields').classList.add('hidden');$('form-submit').classList.remove('hidden');

  $('modal-eyebrow').textContent='Issue intake';$('modal-title').textContent='Report a bug';$('form-submit').textContent='Create report';$('form-error').textContent='';
  qaScreenshots.reset();
  form.elements.reporter.readOnly=managed();
  form.elements.reporter.value=managed()?defaultReporter():(prefill.reporter||defaultReporter());
  form.elements.testing_round.value=prefill.testing_round||latestRound()||'R01';
  if(prefill.title) form.elements.title.value=prefill.title;
  if(prefill.description) form.elements.description.value=prefill.description;
  if(prefill.severity) form.elements.severity.value=prefill.severity;
  setModal(true);
}
function openEdit(id){
  const ticket=tickets.find(t=>t.id===id);if(!ticket)return;editing=ticket;$('ticket-form').reset();setDialogMode(false);
  $('create-fields').classList.add('hidden');$('update-fields').classList.remove('hidden');$('form-submit').classList.remove('hidden');
  $('modal-eyebrow').textContent=`${ticketNo(id)} / Ticket detail`;$('modal-title').textContent=ticket.title;$('form-submit').textContent='Save status';$('form-error').textContent='';
  qaScreenshots.reset(id);
  $('ticket-detail').innerHTML=`<p>${esc(ticket.description||'No detailed steps provided.')}</p><div class="ticket-detail-meta">Reported by ${esc(ticket.reporter)} · ${esc(ticket.testing_round)} · ${esc(label(ticket.category))} · ${esc(label(ticket.severity))}${ticket.issue_url?` · <a href="${esc(safeUrl(ticket.issue_url))}" target="_blank" rel="noopener noreferrer">GitHub ↗</a>`:''}</div>`;
  /* No preselected change: saving without choosing is refused by the browser (required) and the server. */
  $('edit-status').innerHTML='<option value="" disabled selected>Change status…</option>'+allowedStatuses().map(s=>`<option value="${s}">${label(s)}</option>`).join('');
  setModal(true);
}
$('ticket-form').addEventListener('submit',async e=>{
  e.preventDefault();const form=e.currentTarget,btn=$('form-submit');btn.disabled=true;$('form-error').textContent='';
  try{
    let res;
    if(editing){res=await api(`/api/tickets/${editing.id}`,{method:'PATCH',body:{expected_updated_at:editing.updated_at,status:form.elements.status.value,comment:form.elements.comment.value}});}
    else{const fields=['title','description','reporter','severity','category','testing_round','area','environment','deploy_version'];const payload={};fields.forEach(k=>payload[k]=form.elements[k].value);res=await api('/api/tickets',{method:'POST',body:payload});}
    /* New report with screenshots: the issue exists first, then the dialog switches to it and the
       queued files upload. If one fails, the ticket stays saved and "Retry upload" resends the rest. */
    if(!editing&&qaScreenshots.queue.length){
      const pending=[...qaScreenshots.queue];await refresh();openEdit(res.ticket.id);
      qaScreenshots.queue=pending;qaScreenshots.render();
      try{await qaScreenshots.upload(res.ticket.id);}
      catch(err){$('form-error').textContent=`Ticket ${ticketNo(res.ticket.id)} was created. Use Retry upload for the screenshots.`;return;}
    }
    setModal(false);await refresh();
  }catch(err){
    if(err.status===409){$('form-error').textContent='Someone else changed this ticket. Close and reopen it to see the latest version.';await refresh();}
    else $('form-error').textContent=err.message;
  }finally{btn.disabled=false;}
});

/* ---------- wiring ---------- */
document.querySelectorAll('[data-new-ticket]').forEach(b=>b.addEventListener('click',()=>newTicket()));
$('modal-close').addEventListener('click',()=>setModal(false));$('modal-cancel').addEventListener('click',()=>setModal(false));
$('modal').addEventListener('click',e=>{if(e.target===$('modal'))setModal(false)});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('modal').classList.contains('hidden'))setModal(false);});
['search','status-filter','severity-filter','round-filter'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',renderTable));
$('show-backlog').addEventListener('change',()=>analytics&&renderChart());
$('retry').addEventListener('click',refresh);
$('login-form').addEventListener('submit',async e=>{
  e.preventDefault();$('login-error').textContent='';
  const password=$('password').value;$('password').value='';
  try{if(managed())await qaIdentity.signIn($('email').value,password);else await api('/api/login',{method:'POST',body:{password}});await initial();}
  catch(err){$('login-error').textContent=err.message;}
});
for(const [id,action] of [['register','register'],['reset-password','reset']]){
  $(id).addEventListener('click',async()=>{
    $('login-error').textContent='';const password=$('password').value;$('password').value='';
    if(!$('email').value){$('login-error').textContent='Enter your email first.';return;}
    try{$('login-error').textContent=await qaIdentity[action]($('email').value,password);}catch(err){$('login-error').textContent=err.message;}
  });
}

async function signOut(){try{await api('/api/logout',{method:'POST'})}finally{toggleSession(false);}}
$('logout').addEventListener('click',signOut);$('logout-mobile').addEventListener('click',signOut);
initial();
