/* PERPERIS QA — Previews: screenshots of the training app's open pull requests (kattens-pt DEC-526).
   Data: GET /api/previews (open PRs + latest main), GET /api/previews/<sha> (manifest),
   images from GET /api/previews/<sha>/<file>. Before = latest main, after = the PR. */
'use strict';
const qaPreviews = (() => {
  const el = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let listing = null, loadedAt = 0, selected = null, theme = 'light', compare = true;
  const manifests = new Map();

  async function get(url) {
    const res = await fetch(url, {credentials: 'same-origin'});
    if (!res.ok) {
      let msg = `Request failed (${res.status})`;
      try { msg = (await res.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    return res.json();
  }

  async function load(force = false) {
    if (!force && listing && Date.now() - loadedAt < 60000) return listing;
    listing = await get('/api/previews');
    loadedAt = Date.now();
    const count = listing.pulls.filter(p => p.ready).length;
    const badge = el('nav-previews');
    if (badge) { badge.textContent = String(count); badge.classList.toggle('hidden', !count); }
    return listing;
  }

  async function manifest(sha) {
    if (!manifests.has(sha)) manifests.set(sha, get(`/api/previews/${sha}`).catch(e => { manifests.delete(sha); throw e; }));
    return manifests.get(sha);
  }

  function notice(text, title = '', problem = false) {
    const box = el('previews-status');
    box.classList.toggle('hidden', !text);
    box.classList.toggle('is-problem', problem);
    box.innerHTML = text ? `${title ? `<strong>${esc(title)}</strong>` : ''}${esc(text)}` : '';
  }

  function renderPulls() {
    const mount = el('preview-pulls');
    mount.replaceChildren();
    if (!listing.enabled) { notice('Previews are not switched on for this QA instance.', 'Not available'); return; }
    if (!listing.pulls.length) { notice('Nothing is waiting to go live. Changes appear here while they are being reviewed.', 'No open changes'); return; }
    notice(listing.main ? '' : 'They appear once the next change has gone live.', listing.main ? '' : 'No "before" pictures yet');
    for (const p of listing.pulls) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'preview-pull' + (p.ready ? '' : ' is-pending');
      b.setAttribute('aria-pressed', String(selected === p.number));
      b.innerHTML = `<strong>#${esc(p.number)} ${esc(p.title)}</strong><em>${p.ready ? 'Preview ready' : (p.draft ? 'Draft' : 'Being built…')}</em>` +
        `<small>${p.issue ? `For ticket #${esc(p.issue)} · ` : ''}${esc(p.branch)}</small>`;
      if (p.ready) b.addEventListener('click', () => select(p.number));
      mount.append(b);
    }
  }

  function shot(sha, screen, caption, cls) {
    const fig = document.createElement('figure');
    fig.className = 'preview-shot ' + cls;
    if (!screen) {
      fig.innerHTML = `<figcaption>${esc(caption)}</figcaption><div class="preview-missing">Not available</div>`;
      return fig;
    }
    const src = `/api/previews/${sha}/${encodeURIComponent(screen.file)}`;
    fig.innerHTML = `<figcaption>${esc(caption)}</figcaption><a href="${src}" target="_blank" rel="noopener"><img loading="lazy" alt="${esc(screen.label)} (${esc(caption)}, ${esc(screen.theme)})" src="${src}"></a>`;
    return fig;
  }

  async function renderDetail() {
    const pull = listing.pulls.find(p => p.number === selected);
    const detail = el('preview-detail');
    if (!pull) { detail.classList.add('hidden'); return; }
    detail.classList.remove('hidden');
    el('preview-heading').textContent = `#${pull.number} ${pull.title}`;
    document.querySelectorAll('[data-preview-theme]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.previewTheme === theme)));
    const cmp = document.querySelector('[data-preview-compare]');
    cmp.setAttribute('aria-pressed', String(compare));
    cmp.disabled = !listing.main;
    const mount = el('preview-screens');
    mount.replaceChildren();
    el('preview-notes').textContent = 'Loading pictures…';
    try {
      const after = await manifest(pull.sha);
      const before = compare && listing.main ? await manifest(listing.main.sha).catch(() => null) : null;
      if (selected !== pull.number) return;
      const screens = after.screens.filter(s => s.theme === theme);
      const keys = [...new Set([...screens.map(s => s.key), ...(before ? before.screens.filter(s => s.theme === theme).map(s => s.key) : [])])];
      for (const key of keys) {
        const a = screens.find(s => s.key === key);
        const b = before?.screens.find(s => s.key === key && s.theme === theme);
        const block = document.createElement('section');
        block.className = 'preview-screen';
        const h = document.createElement('h3');
        h.textContent = (a || b).label || key;
        const pair = document.createElement('div');
        pair.className = 'preview-pair';
        pair.style.setProperty('--cols', before ? '2' : '1');
        if (before) pair.append(shot(listing.main.sha, b, 'Before (live)', 'is-before'));
        pair.append(shot(pull.sha, a, before ? 'After (this change)' : 'This change', 'is-after'));
        block.append(h, pair);
        mount.append(block);
      }
      const notes = after.notes || [];
      el('preview-notes').textContent = notes.length ? `Not captured: ${notes.join('; ')}` : `Sample data · phone size · pictures from ${new Date(after.created_at || Date.now()).toLocaleString('en-GB')}`;
    } catch (e) {
      el('preview-notes').textContent = e.message;
    }
  }

  function select(number) {
    selected = number;
    renderPulls();
    renderDetail();
  }

  document.querySelectorAll('[data-preview-theme]').forEach(b => b.addEventListener('click', () => { theme = b.dataset.previewTheme; renderDetail(); }));
  document.querySelector('[data-preview-compare]')?.addEventListener('click', () => { compare = !compare; renderDetail(); });

  return {
    async show() {
      notice('Loading open changes…');
      try {
        await load();
        const wanted = Number(new URLSearchParams(location.hash.split('?')[1] || '').get('pr')) || selected;
        if (wanted && listing.pulls.some(p => p.number === wanted && p.ready)) selected = wanted;
        renderPulls();
        renderDetail();
      } catch (e) {
        notice(e.message, 'Previews unavailable', true);
      }
    },
    /* Ticket detail: "Preview ready" link when a change for this ticket has pictures. */
    async annotate(issue) {
      const slot = el('ticket-preview');
      if (!slot) return;
      slot.classList.add('hidden');
      try {
        const data = await load();
        const pull = data.pulls.find(p => p.issue === issue);
        if (!pull) return;
        slot.innerHTML = pull.ready
          ? `Preview ready for change #${esc(pull.number)}: <a href="#previews" data-open-preview="${esc(pull.number)}">see how it looks</a>`
          : `Change #${esc(pull.number)} is being built; pictures follow when its checks finish.`;
        slot.classList.remove('hidden');
        slot.querySelector('[data-open-preview]')?.addEventListener('click', () => {
          selected = pull.number;
          document.getElementById('modal-close')?.click();
        });
      } catch { /* previews are optional on a ticket */ }
    },
  };
})();
