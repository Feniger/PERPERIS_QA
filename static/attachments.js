/* Screenshots (decision 4.1): paste, drop or pick; private thumbnails streamed through QA.
   New report: files wait in a queue and upload after the issue exists ("Retry upload" on failure).
   Existing ticket: "Add screenshot" uploads at once. Nothing is kept in browser storage. */
'use strict';
window.qaScreenshots = {
  queue: [], urls: [], ticketId: null, existing: 0,
  enabled() {return !!qaIdentity.config?.screenshots_enabled;},
  limit() {return qaIdentity.config?.screenshot_limit || 10;},
  el(id) {return document.getElementById(id);},
  status(text, ok=false) {const s=this.el('screenshot-status');s.textContent=text;s.className=ok?'ok':'error';},
  /* ticketId null: a new report (queue). A number: an existing ticket (upload immediately). */
  reset(ticketId=null) {
    this.ticketId=ticketId;this.existing=0;
    this.queue=[];this.revoke();
    this.el('screenshot-queue').replaceChildren();this.el('screenshot-existing').replaceChildren();
    this.el('screenshot-input').value='';this.status('');
    this.el('screenshot-retry').classList.add('hidden');
    this.el('screenshot-controls').classList.toggle('hidden',!this.enabled());
    this.el('screenshot-pick-label').textContent=ticketId?'Add screenshot':'Attach screenshots';
    this.el('screenshot-count').textContent='';
    if(ticketId&&this.enabled())this.load(ticketId).catch(err=>this.status(err.message));
  },
  revoke() {this.urls.forEach(url=>URL.revokeObjectURL(url));this.urls=[];},
  room() {return this.limit()-this.existing-this.queue.length;},
  accept(files) {
    const ok=[];
    for(const file of files){
      if(!['image/png','image/jpeg','image/webp'].includes(file.type)){this.status('Use PNG, JPEG or WebP images.');continue;}
      if(file.size>10*1024*1024){this.status('Each image can be at most 10 MB.');continue;}
      if(ok.length>=this.room()){this.status(`A ticket can have at most ${this.limit()} screenshots.`);break;}
      ok.push(file);
    }
    return ok;
  },
  async add(files) {
    const ok=this.accept([...files]);if(!ok.length)return;
    if(this.ticketId===null){this.queue.push(...ok);this.render();return;}
    this.queue.push(...ok);this.render();
    await this.upload(this.ticketId).catch(()=>{});
  },
  render() {
    this.revoke();
    const mount=this.el('screenshot-queue');mount.replaceChildren();
    this.queue.forEach((file,index)=>{
      const row=document.createElement('div');row.className='screenshot-item queued';
      const img=document.createElement('img');const url=URL.createObjectURL(file);this.urls.push(url);img.src=url;img.alt='Screenshot waiting to upload '+(index+1);row.append(img);
      const note=document.createElement('span');note.className='screenshot-meta';note.textContent=this.ticketId===null?'Uploads when the report is created':'Not uploaded yet';row.append(note);
      const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='Remove';
      remove.addEventListener('click',()=>{this.queue.splice(index,1);this.render();if(!this.queue.length)this.el('screenshot-retry').classList.add('hidden');});
      row.append(remove);mount.append(row);
    });
    this.count();
  },
  count() {
    const n=this.existing;this.el('screenshot-count').textContent=this.ticketId===null&&!n?'':`${n} of ${this.limit()} uploaded`;
  },
  /* Uploads queued files one by one to ticketId. Throws on the first failure; the rest stay queued. */
  async upload(ticketId) {
    this.ticketId=ticketId;this.el('screenshot-retry').classList.add('hidden');
    try{
      while(this.queue.length){
        this.status(`Uploading ${this.queue.length} screenshot${this.queue.length>1?'s':''}…`,true);
        const res=await fetch(`/api/tickets/${ticketId}/screenshots`,{method:'POST',credentials:'same-origin',
          headers:{'X-QA-Request':'1','Content-Type':'application/octet-stream'},body:this.queue[0]});
        if(!res.ok){let data;try{data=await res.json();}catch{data={};}const e=new Error(typeof data.detail==='string'?data.detail:'Screenshot upload failed.');e.status=res.status;throw e;}
        this.queue.shift();this.render();
      }
      this.status('');
    }catch(err){
      this.render();this.status(`${err.message} ${this.queue.length} screenshot${this.queue.length>1?'s':''} not uploaded.`);
      this.el('screenshot-retry').classList.remove('hidden');throw err;
    }finally{
      await this.load(ticketId).catch(()=>{});
      await window.qaAfterScreenshotChange?.(ticketId);
    }
  },
  async load(ticketId) {
    const res=await fetch(`/api/tickets/${ticketId}/screenshots`,{credentials:'same-origin'});
    if(!res.ok)throw new Error('Screenshots could not be loaded.');
    const data=await res.json();if(this.ticketId!==ticketId)return;
    const mount=this.el('screenshot-existing');mount.replaceChildren();
    this.existing=data.screenshots.length;this.count();
    for(const item of data.screenshots){
      const base=`/api/tickets/${ticketId}/screenshots/${encodeURIComponent(item.id)}`;
      const row=document.createElement('div');row.className='screenshot-item';
      const link=document.createElement('a');link.href=base;link.target='_blank';link.rel='noopener noreferrer';link.title='Open full size';
      const img=document.createElement('img');img.src=base+'?thumb=true';img.alt='Screenshot added by '+(item.uploader_name||'a member');img.loading='lazy';link.append(img);row.append(link);
      const meta=document.createElement('span');meta.className='screenshot-meta';
      meta.textContent=`${item.uploader_name||'Member'} · ${new Date(item.uploaded_at*1000).toLocaleDateString()}`;row.append(meta);
      if(item.can_delete){
        const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='Delete';
        remove.addEventListener('click',async()=>{
          remove.disabled=true;
          try{const r=await fetch(base,{method:'DELETE',credentials:'same-origin',headers:{'X-QA-Request':'1'}});
            if(!r.ok){let body;try{body=await r.json();}catch{body={};}throw new Error(body.detail||'Deletion failed.');}
            await this.load(ticketId);await window.qaAfterScreenshotChange?.(ticketId);}
          catch(err){this.status(err.message);remove.disabled=false;}
        });
        row.append(remove);
      }
      mount.append(row);
    }
  }
};
const screenshotInput=document.getElementById('screenshot-input');
screenshotInput.addEventListener('change',()=>{const files=[...screenshotInput.files];screenshotInput.value='';qaScreenshots.add(files);});
const screenshotDrop=document.getElementById('screenshot-drop');
for(const type of ['dragover','drop'])screenshotDrop.addEventListener(type,event=>{event.preventDefault();if(type==='drop')qaScreenshots.add(event.dataTransfer.files);});
/* Paste works anywhere in the open dialog, not only on the drop area. */
document.getElementById('modal').addEventListener('paste',event=>{
  if(!qaScreenshots.enabled()||document.getElementById('screenshot-controls').classList.contains('hidden'))return;
  const files=[...(event.clipboardData?.files||[])].filter(f=>f.type.startsWith('image/'));
  if(files.length){event.preventDefault();qaScreenshots.add(files);}
});
document.getElementById('screenshot-retry').addEventListener('click',()=>{
  if(qaScreenshots.ticketId!==null)qaScreenshots.upload(qaScreenshots.ticketId).catch(()=>{});
});
