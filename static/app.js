/* PERPERIS QA — standalone ticketing and deterministic QA analytics. */
'use strict';
const $ = id => document.getElementById(id);
const STATUSES = ['reported', 'triaged', 'in_progress', 'fixed', 'deployed', 'verified', 'closed', 'reopened'];
const LABELS = {reported:'Reported',triaged:'Triaged',in_progress:'In progress',fixed:'Fixed',deployed:'Deployed',verified:'Verified',closed:'Closed',reopened:'Reopened',ui_ux:'UI / UX',coach_ai:'Coach / AI'};
const COLORS = {found:'#d89864',fixed:'#527cb1',verified:'#57a77c'};
let tickets = [], analytics = null, editing = null, lastFocus = null;
const label = value => LABELS[value] || String(value || '—').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const formatDate = value => value ? new Date(value).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'}) : '—';
const toNum = x => Number.isFinite(+x) ? +x : 0;
async function api(url,{method='GET',body}={}) {
  const res = await fetch(url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-QA-Request':'1'},body:body ? JSON.stringify(body) : undefined});
  let data; try {data=await res.json()}catch {data={detail:'Unexpected response from QA server.'}}
  if(!res.ok) {const message = typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||'Request failed'); const e=new Error(message);e.status=res.status;throw e;}
  return data;
}
function toggleSession(authed){$('workspace').classList.toggle('hidden',!authed);$('login').classList.toggle('hidden',authed);}
async function initial(){
  try {await api('/api/me');toggleSession(true);await refresh();}
  catch(e){toggleSession(false);if(e.status===503) $('login-error').textContent=e.message;}
}
async function refresh(){
  try {const [a,t]=await Promise.all([api('/api/analytics'),api('/api/tickets')]); analytics=a;tickets=t.tickets;renderAll();}
  catch(e){if(e.status===401){toggleSession(false);return;}console.error(e);$('trend-title').textContent='Unable to load QA data';$('trend-desc').textContent=e.message;}
}
function renderAll(){
  $('k-total').textContent=analytics.total;
  $('k-open').textContent=analytics.open;
  $('k-fixed').textContent=analytics.fixed;
  $('k-verified').textContent=analytics.verified;
  $('backlog').textContent=analytics.backlog;
  $('total-tag').textContent=`${analytics.total} REPORTS`;
  $('nav-count').textContent=analytics.open;
  $('ticket-count').textContent=`${tickets.length} TICKETS`;
  renderSeverity();renderCategories();renderReporters();renderChart();renderFilters();renderTable();
}
function renderSeverity(){
  const levels=['critical','high','medium','low'];const total=Math.max(1,analytics.total);
  const colors={critical:'#d55e5c',high:'#dc9967',medium:'#6593b0',low:'#70ad91'};
  $('severity-bars').innerHTML=levels.map(key=>`<div class="bar-row"><span>${label(key)}</span><div class="bar-track"><div class="bar-fill" style="width:${toNum(analytics.severity[key])/total*100}%;background:${colors[key]}"></div></div><b>${toNum(analytics.severity[key])}</b></div>`).join('');
}
function renderCounts(id,items,empty){
  const entries=Object.entries(items).sort((a,b)=>b[1]-a[1]);
  $(id).innerHTML=entries.length?entries.map(([key,count])=>`<div class="category-item"><span>${esc(label(key))}</span><b>${count}</b></div>`).join(''):`<span class="muted">${empty}</span>`;
}
function renderCategories(){renderCounts('category-list',analytics.categories,'No issues reported yet.');}
function renderReporters(){renderCounts('reporter-list',analytics.reporters,'No reporters yet.');}
function renderFilters(){
  const currentStatus=$('status-filter').value;const currentRound=$('round-filter').value;
  $('status-filter').innerHTML='<option value="">All statuses</option>'+STATUSES.map(s=>`<option value="${s}">${label(s)}</option>`).join('');
  $('status-filter').value=currentStatus;
  const rounds=[...new Set(tickets.map(t=>t.testing_round))].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
  $('round-filter').innerHTML='<option value="">All rounds</option>'+rounds.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
  $('round-filter').value=rounds.includes(currentRound)?currentRound:'';
}
function renderTable(){
  const search=$('search').value.trim().toLowerCase(),status=$('status-filter').value,sev=$('severity-filter').value,round=$('round-filter').value;
  const list=tickets.filter(t=>(!status||t.status===status)&&(!sev||t.severity===sev)&&(!round||t.testing_round===round)&&(!search||[t.id,t.title,t.description,t.reporter,t.category,t.area,t.assigned_to,t.deploy_version].some(v=>String(v||'').toLowerCase().includes(search))));
  $('ticket-rows').innerHTML=list.map(t=>`<tr data-id="${t.id}" tabindex="0" aria-label="Open ${esc(t.title)}"><td><span class="ticket-id">QA-${String(t.id).padStart(3,'0')}</span><span class="ticket-name">${esc(t.title)}</span><div class="ticket-area">${esc(t.area || t.environment || '')}</div></td><td><span class="chip ${esc(t.status)}">${esc(label(t.status))}</span></td><td><b class="sev-${esc(t.severity)}">${esc(label(t.severity))}</b></td><td>${esc(label(t.category))}</td><td>${esc(t.testing_round)}</td><td>${esc(t.reporter)}</td><td>${formatDate(t.created_at)}</td></tr>`).join('');
  $('empty').classList.toggle('hidden',list.length>0);
  $('ticket-rows').querySelectorAll('tr').forEach(row=>{
    row.addEventListener('click',()=>openEdit(Number(row.dataset.id)));
    row.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openEdit(Number(row.dataset.id));}});
  });
}
function renderChart(){
  const rounds=analytics.rounds||[];const mount=$('chart');
  if(!rounds.length){mount.innerHTML='<div class="chart-empty">Your QA timeline starts with your first ticket.<br><small>No example numbers are presented as real results.</small></div>';setTrend('No rounds recorded','Create the first report to begin measuring quality.');return;}
  const w=760,h=365,p={l:44,r:26,t:27,b:64};const iw=w-p.l-p.r,ih=h-p.t-p.b;
  const max=Math.max(4,...rounds.flatMap(r=>['found','fixed','verified'].map(k=>toNum(r[k]))));
  const ceiling=Math.ceil(max/4)*4;
  const xp=i=>p.l+(rounds.length===1?iw/2:i*iw/(rounds.length-1));
  const yp=value=>p.t+ih-(toNum(value)/ceiling)*ih;
  const grid=Array.from({length:5},(_,i)=>{const v=ceiling*i/4,y=yp(v);return `<line x1="${p.l}" y1="${y}" x2="${w-p.r}" y2="${y}" stroke="#edf1ed" stroke-width="1"/><text x="${p.l-12}" y="${y+4}" text-anchor="end" fill="#9aa79f" font-size="11">${v}</text>`;}).join('');
  const labels=rounds.map((r,i)=>`<text x="${xp(i)}" y="${h-p.b+28}" text-anchor="middle" fill="#7e8c84" font-size="12" font-weight="600">${esc(r.round)}</text>`).join('');
  const lines=Object.entries(COLORS).map(([key,color])=>{
    const points=rounds.map((r,i)=>`${xp(i)},${yp(r[key])}`).join(' ');
    const dots=rounds.map((r,i)=>`<circle cx="${xp(i)}" cy="${yp(r[key])}" r="4.5" fill="${color}" stroke="white" stroke-width="2"><title>${esc(r.round)} · ${label(key)}: ${r[key]}</title></circle>`).join('');
    return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>${dots}`;
  }).join('');
  const crossing=analytics.crossover;
  const crossingIdx=rounds.findIndex(r=>r.round===crossing);
  const mark=crossingIdx<0?'':`<line x1="${xp(crossingIdx)}" y1="${p.t}" x2="${xp(crossingIdx)}" y2="${p.t+ih}" stroke="#a8c2a9" stroke-width="1.5" stroke-dasharray="5 6"/><rect x="${Math.min(w-172,Math.max(49,xp(crossingIdx)-73))}" y="3" width="146" height="20" rx="6" fill="#e8f5e8"/><text x="${Math.min(w-99,Math.max(122,xp(crossingIdx)))}" y="17" text-anchor="middle" font-size="10" font-weight="700" fill="#3a8059">FIXES ≥ FOUND</text>`;
  mount.innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="QA trend showing reports found, fixed and verified by testing round" preserveAspectRatio="xMidYMid meet"><g>${grid}${mark}${lines}${labels}</g><text x="${w/2}" y="${h-8}" text-anchor="middle" fill="#9ca9a1" font-size="10" letter-spacing="2">TESTING ROUNDS</text></svg>`;
  const latest=rounds[rounds.length-1];
  if(latest.fixed>latest.found) setTrend('Fixes outpace discoveries',`${latest.round}: ${latest.fixed} first fixes vs ${latest.found} new reports. ${analytics.backlog} reports are not verified.`);
  else if(latest.fixed===latest.found&&latest.found>0) setTrend('Discovery and fixes are level',`${latest.round}: ${latest.fixed} fixes and ${latest.found} discoveries. Verified: ${latest.verified}.`);
  else setTrend('More discoveries than fixes',`${latest.round}: ${latest.found} new reports vs ${latest.fixed} first fixes. Keep closing the verification loop.`);
}
function setTrend(title,desc){$('trend-title').textContent=title;$('trend-desc').textContent=desc;}
function setModal(open){$('modal').classList.toggle('hidden',!open);document.body.style.overflow=open?'hidden':'';if(open){lastFocus=document.activeElement;setTimeout(()=>document.querySelector('#ticket-form input:not([type=hidden]), #ticket-form select')?.focus(),20);}else{editing=null;lastFocus?.focus?.();}}
function newTicket(){editing=null;$('ticket-form').reset();$('create-fields').classList.remove('hidden');$('update-fields').classList.add('hidden');$('modal-eyebrow').textContent='ISSUE INTAKE';$('modal-title').textContent='Report a bug';$('form-submit').textContent='Create report';$('form-error').textContent='';$('ticket-form').elements.reporter.value='Product Owner';$('ticket-form').elements.testing_round.value=analytics.rounds.at(-1)?.round || 'R01';setModal(true);}
function openEdit(id){const ticket=tickets.find(t=>t.id===id);if(!ticket)return;editing=ticket;$('ticket-form').reset();$('create-fields').classList.add('hidden');$('update-fields').classList.remove('hidden');$('modal-eyebrow').textContent=`QA-${String(id).padStart(3,'0')} / TICKET DETAIL`;$('modal-title').textContent=ticket.title;$('form-submit').textContent='Save ticket';$('form-error').textContent='';$('ticket-detail').innerHTML=`<strong>${esc(ticket.title)}</strong><p>${esc(ticket.description||'No detailed steps provided.')}</p><div class="ticket-detail-meta">Reported by ${esc(ticket.reporter)} · ${esc(ticket.testing_round)} · ${esc(label(ticket.category))} · ${esc(label(ticket.severity))}${ticket.issue_url?` · <a href="${esc(safeUrl(ticket.issue_url))}" target="_blank" rel="noopener noreferrer">GitHub ↗</a>`:''}</div>`;
  $('edit-status').innerHTML=STATUSES.map(s=>`<option value="${s}">${label(s)}</option>`).join('');$('edit-status').value=ticket.status;$('ticket-form').elements.assigned_to.value=ticket.assigned_to||'';$('ticket-form').elements.resolution_notes.value=ticket.resolution_notes||'';$('ticket-form').elements.resolution_round.value=ticket.fixed_round||ticket.verified_round||'';setModal(true);}
function safeUrl(url){try {const u=new URL(url);return u.protocol==='https:'?u.href:'#';}catch{return '#';}}
$('ticket-form').addEventListener('submit',async e=>{
  e.preventDefault();const form=e.currentTarget,btn=$('form-submit');btn.disabled=true;$('form-error').textContent='';
  try{let res;if(editing){res=await api(`/api/tickets/${editing.id}`,{method:'PATCH',body:{status:form.elements.status.value,assigned_to:form.elements.assigned_to.value,resolution_notes:form.elements.resolution_notes.value,resolution_round:form.elements.resolution_round.value}});}else{const fields=['title','description','reporter','severity','category','testing_round','area','environment','deploy_version','issue_url'];const payload={};fields.forEach(k=>payload[k]=form.elements[k].value);res=await api('/api/tickets',{method:'POST',body:payload});}setModal(false);await refresh();}
  catch(err){$('form-error').textContent=err.message;}finally{btn.disabled=false;}
});
$('new-ticket').addEventListener('click',newTicket);
$('modal-close').addEventListener('click',()=>setModal(false));$('modal-cancel').addEventListener('click',()=>setModal(false));
$('modal').addEventListener('click',e=>{if(e.target===$('modal'))setModal(false)});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('modal').classList.contains('hidden'))setModal(false);});
['search','status-filter','severity-filter','round-filter'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',renderTable));
$('login-form').addEventListener('submit',async e=>{e.preventDefault();$('login-error').textContent='';try{await api('/api/login',{method:'POST',body:{password:$('password').value}});$('password').value='';toggleSession(true);await refresh();}catch(err){$('login-error').textContent=err.message;}});
$('logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST'})}finally{toggleSession(false);}});
initial();
