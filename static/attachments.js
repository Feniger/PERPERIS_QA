/* One private screenshot queue for paste, drop and file-picker selection. */
'use strict';
window.qaScreenshots = {
  queue: [], urls: [], ticketId:null,
  enabled() {return !!qaIdentity.config?.attachments_enabled;},
  reset(ticketId=null) {
    this.ticketId=ticketId;
    this.queue=[];this.urls.forEach(url=>URL.revokeObjectURL(url));this.urls=[];
    document.getElementById('screenshot-queue').replaceChildren();
    document.getElementById('screenshot-input').value='';
    document.getElementById('screenshot-existing').replaceChildren();
    document.getElementById('screenshot-controls').classList.toggle('hidden',!this.enabled());
    if(ticketId&&this.enabled())this.load(ticketId).catch(err=>{document.getElementById('screenshot-status').textContent=err.message;});
    document.getElementById('screenshot-status').textContent='';
  },
  add(files) {
    for(const file of files){
      if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>10*1024*1024||this.queue.length>=10){document.getElementById('screenshot-status').textContent='Choose at most 10 PNG, JPEG or WebP screenshots, up to 10 MiB each.';continue;}
      this.queue.push(file);
    }
    this.render();
  },
  render() {
    this.urls.forEach(url=>URL.revokeObjectURL(url));this.urls=[];
    const mount=document.getElementById('screenshot-queue');mount.replaceChildren();
    this.queue.forEach((file,index)=>{
      const row=document.createElement('div');row.className='screenshot-item';
      const img=document.createElement('img');const url=URL.createObjectURL(file);this.urls.push(url);img.src=url;img.alt='Queued screenshot '+(index+1);row.append(img);
      const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='Remove queued screenshot';remove.addEventListener('click',()=>{this.queue.splice(index,1);this.render();});row.append(remove);mount.append(row);
    });
  },
  async upload(ticketId) {
    while(this.queue.length){
      const res=await fetch(`/api/tickets/${ticketId}/attachments`,{method:'POST',credentials:'same-origin',headers:{'X-QA-Request':'1','Content-Type':'application/octet-stream'},body:this.queue[0]});
      if(!res.ok){let data;try{data=await res.json();}catch{data={};}throw new Error(data.detail||'Screenshot upload failed.');}
      this.queue.shift();this.render();
    }
  },
  async load(ticketId) {
    const res=await fetch(`/api/tickets/${ticketId}/attachments`,{credentials:'same-origin'});
    if(!res.ok)throw new Error('Unable to load screenshots.');
    const data=await res.json();if(this.ticketId!==ticketId)return;const mount=document.getElementById('screenshot-existing');mount.replaceChildren();
    for(const item of data.attachments){
      const row=document.createElement('div');row.className='screenshot-item';
      const link=document.createElement('a');link.href='/api/attachments/'+encodeURIComponent(item.id);link.target='_blank';link.rel='noopener noreferrer';
      const img=document.createElement('img');img.src=link.href;img.alt='Private ticket screenshot';img.loading='lazy';link.append(img);row.append(link);
      const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='Delete screenshot';
      remove.addEventListener('click',async()=>{remove.disabled=true;try{const result=await fetch(link.href,{method:'DELETE',credentials:'same-origin',headers:{'X-QA-Request':'1'}});if(!result.ok){const body=await result.json();throw new Error(body.detail||'Deletion failed.');}row.remove();}catch(err){document.getElementById('screenshot-status').textContent=err.message;}finally{remove.disabled=false;}});
      row.append(remove);mount.append(row);
    }
  }
};
const screenshotInput=document.getElementById('screenshot-input');
screenshotInput.addEventListener('change',()=>{qaScreenshots.add(screenshotInput.files);screenshotInput.value='';});
const screenshotDrop=document.getElementById('screenshot-drop');
for(const type of ['dragover','drop'])screenshotDrop.addEventListener(type,event=>{event.preventDefault();if(type==='drop')qaScreenshots.add(event.dataTransfer.files);});
screenshotDrop.addEventListener('paste',event=>{const files=event.clipboardData?.files;if(files?.length){event.preventDefault();qaScreenshots.add(files);}});
