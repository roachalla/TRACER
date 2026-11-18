/* TRACER review center. Static page: state lives in localStorage and in a feedback file the reviewer saves (File System Access
   API where the browser has it, a download otherwise). RC = {id, title, prior, prior_comments, designer_email, stage}. */
(() => {
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const KEY = 'tracer-review:' + RC.id;
  const doc = $('#doc'), rail = $('#rail'), page = $('#page'), cardsEl = $('#cards');
  const fresh = !readLocal();
  const state = readLocal() || {v: 1, lecture: RC.id, role: RC.stage, name: '', designer_email: '', decisions: {}, comments: []};
  if (!state.designer_email && RC.designer_email) state.designer_email = RC.designer_email;
  let filter = 'open', active = null, dirty = false, saveT;

  function readLocal() { try { const s = localStorage.getItem(KEY); return s ? JSON.parse(s) : null; } catch (e) { return null; } }
  function persist() {
    dirty = true; clearTimeout(saveT);
    saveT = setTimeout(() => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} }, 200);
    refresh();
  }
  function toast(msg, ms = 3200) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), ms); }
  const isItem = c => !c.classList.contains('cmt') && !c.classList.contains('settled');
  const isSettled = c => c.classList.contains('settled');
  // the professor's version leaves out housekeeping items (unless the designer passed one on as a question)
  const forRole = c => !(state.role === 'professor' && c.dataset.aud === 'designer' && !(RC.prior[c.dataset.id] && RC.prior[c.dataset.id].status === 'ask'));
  const everyCard = () => $$('.card', cardsEl);
  const allCards = () => everyCard().filter(forRole);
  const cardById = id => $(`.card[data-id="${id}"]`, cardsEl);

  // ---------- anchors in the textbook
  function anchorEl(a) {
    if (a === 'top') return $('.doctitle', page);
    let el = page.querySelector(`[data-a="${a}"]`);
    if (!el) { const m = /^[xef](\d+)-/.exec(a) || /^s(\d+)/.exec(a); if (m) el = page.querySelector(`[data-a="s${m[1]}"]`); }
    return el;
  }
  function showEl(a) { // the element that is outlined and gets the marker
    const el = anchorEl(a);
    if (el && el.matches('section.sec')) return $('h2', el) || el;
    return el;
  }
  function rawFor(a) { const el = page.querySelector(`[data-a="${a}"]`); return el ? el.dataset.raw : undefined; }
  function whereFor(a) {
    if (a.startsWith('t-')) return 'Teaching edition';
    const m = /(\d+)/.exec(a); const sec = m && page.querySelector(`[data-a="s${m[1]}"]`);
    return sec ? 'Section ' + ($('.secno', sec)?.textContent || '') : '';
  }

  // ---------- decisions
  const LABEL = {ok: 'Looks right', fix: 'Needs a fix', ask: () => state.role === 'designer' ? 'Ask the professor' : 'Not sure'};
  const lab = s => typeof LABEL[s] === 'function' ? LABEL[s]() : LABEL[s];
  function handled(c) {
    if (!isItem(c)) return true;  // comments and items TRACER settled itself
    const id = c.dataset.id, d = state.decisions[id], p = RC.prior[id];
    if (d) return true;
    return !!(p && state.role === 'professor' && p.status !== 'ask');
  }

  function setupCard(c) {
    const id = c.dataset.id, a = c.dataset.anchor, p = RC.prior[id];
    if (p) {
      const who = p.by ? `${p.by} (${p.role})` : p.role || 'Earlier reviewer';
      const div = document.createElement('div');
      div.className = 'prior' + (p.status === 'ask' ? '' : ' ok');
      div.textContent = p.status === 'ask' ? `${who} asks: ${p.text || '(no note)'}` :
        p.status === 'fix' ? `${who} fixed this: ${p.text || ''}` : `${who}: looks right.${p.text ? ' ' + p.text : ''}`;
      c.querySelector('.d').after(div);
    }
    const act = document.createElement('div');
    act.className = 'act'; act.setAttribute('role', 'group'); act.setAttribute('aria-label', 'Your decision');
    act.innerHTML = ['ok', 'fix', 'ask'].map(s => `<button type=button data-s=${s} aria-pressed=false>${lab(s)}</button>`).join('');
    const sug = $('.suggest', c);
    if (sug) {  // TRACER's re-check proposed a corrected wording: one click makes it the fix (a person still decides)
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'usesug'; b.textContent = 'Use this suggestion';
      b.addEventListener('click', e => {
        e.stopPropagation();
        state.decisions[id] = {status: 'fix', text: sug.dataset.text, by: state.name, role: state.role, at: new Date().toISOString(), suggested: true};
        paintCard(c); persist();
      });
      sug.append(b);
    }
    const ed = document.createElement('div');
    ed.className = 'edit'; ed.hidden = true;
    ed.innerHTML = `<label for="t${id}"></label><textarea id="t${id}"></textarea><div class=pv></div>`;
    c.append(act, ed);
    act.addEventListener('click', e => {
      const b = e.target.closest('button[data-s]'); if (!b) return;
      const s = b.dataset.s, d = state.decisions[id];
      if (d && d.status === s) { delete state.decisions[id]; }
      else {
        const nd = {status: s, text: d ? d.text : '', by: state.name, role: state.role, at: new Date().toISOString()};
        const raw = rawFor(a);
        if (s === 'fix' && raw !== undefined && (!nd.text || (d && d.status !== 'fix'))) nd.text = raw;
        state.decisions[id] = nd;
      }
      paintCard(c); persist();
      if (state.decisions[id]) { const t = $('textarea', c); if (s !== 'ok') t.focus(); }
      if (filter === 'open' && s === 'ok') setTimeout(() => { applyFilter(); nextOpen(c); }, 450);
    });
    const ta = $('textarea', ed);
    ta.addEventListener('input', () => {
      const d = state.decisions[id]; if (!d) return;
      d.text = ta.value; d.at = new Date().toISOString(); preview(c); persist();
    });
    paintCard(c);
  }

  function paintCard(c) {
    const id = c.dataset.id, d = state.decisions[id], a = c.dataset.anchor;
    c.classList.remove('st-ok', 'st-fix', 'st-ask');
    const p = RC.prior[id];
    const st = d ? d.status : (p && state.role === 'professor' && p.status !== 'ask' ? p.status : null);
    if (st) c.classList.add('st-' + st);
    $('.state', c).textContent = d ? lab(d.status) : (st ? 'done earlier' : '');
    $$('.act button', c).forEach(b => { b.setAttribute('aria-pressed', String(!!d && d.status === b.dataset.s)); b.textContent = lab(b.dataset.s); });
    const ed = $('.edit', c), ta = $('textarea', c), label = $('label', ed);
    ed.hidden = !d;
    if (d) {
      const raw = rawFor(a);
      label.textContent = d.status === 'fix' ? (raw !== undefined ? (a.startsWith('e') ? 'Corrected equation (LaTeX)' :
        'Corrected sentence (math between $ signs). This replaces the sentence in every format.') : 'Describe the fix') :
        d.status === 'ask' ? (state.role === 'designer' ? 'Your question for the professor' : 'What are you unsure about?') : 'Note (optional)';
      if (ta.value !== d.text) ta.value = d.text || '';
      preview(c);
    }
  }

  let pvT;
  function preview(c) {
    const d = state.decisions[c.dataset.id], pv = $('.pv', c), a = c.dataset.anchor;
    if (!d || d.status !== 'fix' || rawFor(a) === undefined || !d.text || d.text === rawFor(a)) { pv.textContent = ''; return; }
    pv.textContent = a.startsWith('e') ? '$$' + d.text + '$$' : d.text;
    clearTimeout(pvT);
    pvT = setTimeout(() => { if (window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise([pv]).catch(() => {}); }, 300);
  }

  // ---------- comments
  // ids and anchors can come from a feedback file someone else saved: allow only plain characters before they reach the page
  const safeId = v => String(v || '').replace(/[^A-Za-z0-9_-]/g, '').slice(0, 40) || 'c' + Math.random().toString(36).slice(2, 10);
  const safeAnchor = v => /^[A-Za-z0-9-]{1,40}$/.test(String(v || '')) ? String(v) : 'top';
  function commentCard(cm, readonly) {
    cm.id = safeId(cm.id); cm.anchor = safeAnchor(cm.anchor);
    const c = document.createElement('article');
    c.className = 'card cmt'; c.tabIndex = 0;
    c.dataset.id = cm.id; c.dataset.anchor = cm.anchor; c.dataset.group = 'comments'; c.dataset.kind = 'comment';
    c.innerHTML = `<header><span class=num></span><span class=chip>comment</span><span class=where></span><span class=state></span></header>
      <div class=q></div>` + (readonly ? `<div class=d></div>` :
      `<div class=edit><label for="t${cm.id}">Your comment</label><textarea id="t${cm.id}"></textarea></div>
       <button type=button class=linkish>Delete comment</button>`);
    $('.where', c).textContent = whereFor(cm.anchor);
    $('.q', c).textContent = cm.quote || '';
    if (readonly) {
      $('.state', c).textContent = (cm.by || cm.role || 'reviewer');
      $('.d', c).textContent = cm.text;
      c.classList.add('readonly');
    } else {
      $('.state', c).textContent = cm.by || '';
      const ta = $('textarea', c); ta.value = cm.text || '';
      ta.addEventListener('input', () => { cm.text = ta.value; cm.at = new Date().toISOString(); persist(); });
      $('.linkish', c).addEventListener('click', e => {
        e.stopPropagation();
        if (cm.text && !confirm('Delete this comment?')) return;
        state.comments = state.comments.filter(x => x.id !== cm.id); c.remove(); active = null; persist(); rebuild();
      });
    }
    cardsEl.append(c);
    return c;
  }
  function addComment(anchor, quote) {
    const cm = {id: 'c' + Date.now().toString(36), anchor, quote: (quote || '').slice(0, 300), text: '', by: state.name, role: state.role,
                at: new Date().toISOString()};
    state.comments.push(cm);
    const c = commentCard(cm);
    if (filter !== 'open' && filter !== 'all' && filter !== 'comments') setFilter('comments');
    persist(); rebuild(); activate(c, true);
    setTimeout(() => $('textarea', c).focus({preventScroll: true}), 350);
  }

  // ---------- markers, order, numbering
  function rebuild() {
    $$('.badge, .blkbadges', page).forEach(x => x.remove());
    $$('.flag, .flagblk, .cmt, .done', page).forEach(x => x.classList.remove('flag', 'flagblk', 'cmt', 'done'));
    everyCard().forEach(c => { c.hidden = !forRole(c); });
    const all = allCards(), cs = all.filter(c => !isSettled(c)), st = all.filter(isSettled);
    const pos = new Map(all.map(c => [c, showEl(c.dataset.anchor)]));
    const byPos = (a, b) => {
      const x = pos.get(a), y = pos.get(b);
      if (!x || !y) return x ? -1 : y ? 1 : 0;
      if (x === y) return (isItem(a) ? 0 : 1) - (isItem(b) ? 0 : 1);
      return x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    };
    cs.sort(byPos); st.sort(byPos);
    cs.forEach((c, i) => { cardsEl.append(c); $('.num', c).textContent = i + 1; });
    st.forEach(c => { cardsEl.append(c); $('.num', c).textContent = '✓'; });  // settled by TRACER: listed last, no marker in the text
    everyCard().filter(c => !forRole(c)).forEach(c => cardsEl.append(c));
    const by = new Map();
    cs.forEach(c => { const el = pos.get(c); if (el) { if (!by.has(el)) by.set(el, []); by.get(el).push(c); } });
    for (const [el, list] of by) {
      const inline = el.classList.contains('s');
      const items = list.filter(isItem);
      if (items.length) el.classList.add(inline ? 'flag' : 'flagblk');
      if (items.length && items.every(handled)) el.classList.add('done');
      if (list.some(c => !isItem(c))) el.classList.add(inline ? 'cmt' : 'flagblk');
      const badges = list.map(c => {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'badge' + (isItem(c) ? (handled(c) ? ' done' : '') : ' c');
        b.textContent = $('.num', c).textContent; b.dataset.card = c.dataset.id;
        b.setAttribute('aria-label', `Review item ${b.textContent}`);
        return b;
      });
      if (inline) badges.forEach(b => el.before(b));
      else { const w = document.createElement('div'); w.className = 'blkbadges'; badges.forEach(b => w.append(b)); el.prepend(w); }
    }
    applyFilter(); refresh(); drawLink();
  }

  function applyFilter() {
    let shown = 0;
    const isCmt = c => c.classList.contains('cmt');
    const match = (c, f) => f === 'all' ? !isSettled(c) : f === 'open' ? (isItem(c) ? !handled(c) : isCmt(c)) :
      f === 'comments' ? isCmt(c) : f === 'settled' ? isSettled(c) : c.dataset.group === f && isItem(c);
    everyCard().forEach(c => {
      c.hidden = !forRole(c) || (!match(c, filter) && c !== active); shown += !c.hidden;
    });
    $('#empty').hidden = shown > 0;
    $$('.filters button').forEach(b => {
      const f = b.dataset.f, cs = allCards();
      const n = f === 'open' ? cs.filter(c => isItem(c) && !handled(c)).length : cs.filter(c => match(c, f)).length;
      b.innerHTML = b.textContent.replace(/\s*\d+$/, '') + ` <span class=n>${n}</span>`;
      b.hidden = n === 0 && !['open', 'all'].includes(f);
    });
  }
  function setFilter(f) {
    filter = f; $$('.filters button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.f === f))); applyFilter(); drawLink();
  }

  function refresh() {
    const items = allCards().filter(isItem), done = items.filter(handled).length;
    const serious = items.filter(c => !handled(c) && $('.chip.r', c)).length;  // red = a fact-check or reviewer found a real error
    const hb = $('#highbar');
    hb.hidden = !serious;
    hb.textContent = serious ? `${serious} serious issue${serious > 1 ? 's' : ''} still open (red). Student pages say "Draft" until the ` +
      `professor's review is applied.` : '';
    $('#progtxt').textContent = `${done} of ${items.length} checked` + (state.comments.length ? ` · ${state.comments.length} comment(s)` : '');
    $('#progbar').style.width = (items.length ? 100 * done / items.length : 100) + '%';
    // marker colours follow decisions without a full rebuild
    $$('.badge', page).forEach(b => { const c = cardById(b.dataset.card); if (c && isItem(c)) b.classList.toggle('done', handled(c)); });
    $$('.flag, .flagblk', page).forEach(el => {
      const ids = $$('.badge', el.classList.contains('s') ? el.parentElement : el).map(b => b.dataset.card);
      const cs = ids.map(cardById).filter(c => c && isItem(c) && showEl(c.dataset.anchor) === el);
      el.classList.toggle('done', cs.length > 0 && cs.every(handled));
    });
  }

  // ---------- linking
  function activate(c, fromCard) {
    $$('.card.on').forEach(x => x.classList.remove('on')); $$('.on', page).forEach(x => x.classList.remove('on'));
    active = c; if (!c) { drawLink(); return; }
    c.classList.add('on'); c.hidden = false;
    const el = showEl(c.dataset.anchor);
    if (el) {
      el.classList.add('on');
      if (fromCard) { el.scrollIntoView({block: 'center', behavior: 'smooth'}); el.classList.remove('hot'); void el.offsetWidth; el.classList.add('hot'); }
    }
    if (!fromCard) c.scrollIntoView({block: 'center', behavior: 'smooth'});
    drawLink(); setTimeout(drawLink, 450); setTimeout(drawLink, 900);
  }
  function nextOpen(c) {
    const cs = allCards().filter(x => !x.hidden && isItem(x) && !handled(x));
    const nxt = cs.find(x => c.compareDocumentPosition(x) & Node.DOCUMENT_POSITION_FOLLOWING) || cs[0];
    if (nxt) activate(nxt, true);
  }
  let raf;
  function drawLink() {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      const path = $('#link path');
      if (!active || active.hidden) { path.setAttribute('d', ''); return; }
      const el = showEl(active.dataset.anchor);
      if (!el || !el.getClientRects().length) { path.setAttribute('d', ''); return; }
      const r = el.getClientRects()[el.classList.contains('s') ? el.getClientRects().length - 1 : 0], c = active.getBoundingClientRect();
      const dr = doc.getBoundingClientRect(), rr = rail.getBoundingClientRect();
      if (r.bottom < dr.top + 40 || r.top > dr.bottom || c.bottom < rr.top + 40 || c.top > rr.bottom || dr.width < 50) { path.setAttribute('d', ''); return; }
      const x1 = Math.min(r.right + 6, dr.right - 6), y1 = r.top + Math.min(r.height / 2, 14), x2 = c.left - 2, y2 = Math.max(rr.top + 44, c.top + 18);
      const k = Math.max(40, (x2 - x1) / 2);
      path.setAttribute('d', `M${x1},${y1} C${x1 + k},${y1} ${x2 - k},${y2} ${x2},${y2}`);
    });
  }

  cardsEl.addEventListener('click', e => {
    if (e.target.closest('button, textarea, summary, a, details[open] img')) return;
    const c = e.target.closest('.card'); if (c) activate(c, true);
  });
  cardsEl.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.classList.contains('card')) activate(e.target, true);
  });
  page.addEventListener('click', e => {
    if (!getSelection().isCollapsed) return;
    const b = e.target.closest('.badge');
    let c = b ? cardById(b.dataset.card) : null;
    if (!c) {
      const el = e.target.closest('.flag, .flagblk, .cmt');
      if (el) c = allCards().find(x => showEl(x.dataset.anchor) === el && !x.hidden) || allCards().find(x => showEl(x.dataset.anchor) === el);
    }
    if (c) { e.preventDefault(); activate(c, false); }
  });
  doc.addEventListener('scroll', () => { drawLink(); hideCbtn(); }, {passive: true});
  rail.addEventListener('scroll', drawLink, {passive: true});
  addEventListener('resize', drawLink);

  // ---------- comment button: on a text selection, or hovering an equation / figure / box
  const cbtn = $('#cbtn'); let pending = null;
  function hideCbtn() { cbtn.hidden = true; pending = null; }
  function anchorOf(node) {
    const el = node.nodeType === 1 ? node : node.parentElement;
    if (!el || !page.contains(el)) return null;
    const a = el.closest('.s[data-a]') || el.closest('[data-a]');
    return a ? a.dataset.a : 'top';
  }
  function placeCbtn(x, y) {
    cbtn.hidden = false;
    cbtn.style.left = Math.min(x, innerWidth - cbtn.offsetWidth - 8) + 'px';
    cbtn.style.top = Math.min(y, innerHeight - 40) + 'px';
  }
  page.addEventListener('mouseup', () => setTimeout(() => {
    const sel = getSelection();
    if (sel.isCollapsed || !sel.rangeCount) return;
    const text = sel.toString().trim(); const a = anchorOf(sel.anchorNode);
    if (text.length < 2 || !a) return;
    const r = sel.getRangeAt(0).getBoundingClientRect();
    pending = {anchor: a, quote: text}; placeCbtn(r.right - 40, r.bottom + 8);
  }, 0));
  page.addEventListener('mouseover', e => {
    if (!getSelection().isCollapsed) return;
    const el = e.target.closest('.eq[data-a], figure[data-a], .textbox[data-a]');
    if (!el) { if (pending && pending.blk && !e.target.closest('#cbtn')) hideCbtn(); return; }
    const label = el.matches('.eq') ? 'Equation ' + ($('.eqn', el)?.textContent || '') :
      el.matches('figure') ? ($('figcaption', el)?.textContent || 'Figure').slice(0, 140) : ($('.textbox__title', el)?.textContent || '');
    const r = el.getBoundingClientRect();
    pending = {anchor: el.dataset.a, quote: label.trim(), blk: true}; placeCbtn(r.right - 110, Math.max(r.top + 6, doc.getBoundingClientRect().top + 50));
  });
  cbtn.addEventListener('mousedown', e => e.preventDefault());
  cbtn.addEventListener('click', () => { if (pending) { const p = pending; hideCbtn(); getSelection().removeAllRanges(); addComment(p.anchor, p.quote); } });
  document.addEventListener('mousedown', e => { if (!e.target.closest('#cbtn') && !page.contains(e.target)) hideCbtn(); });

  // ---------- save / open / email
  function exportData() {
    const decisions = {};
    for (const [id, d] of Object.entries(state.decisions)) {
      const c = cardById(id), a = c ? c.dataset.anchor : '';
      decisions[id] = Object.assign({}, d, {anchor: a, kind: c ? c.dataset.kind : '', where: c ? $('.where', c).textContent : '',
        question: c ? ($('.d', c)?.textContent || '').trim().slice(0, 500) : '', quote: c ? ($('.q', c)?.textContent || '').trim().slice(0, 300) : '',
        original: rawFor(a)});
    }
    const comments = state.comments.map(cm => Object.assign({}, cm, {where: whereFor(cm.anchor), original: rawFor(cm.anchor)}));
    return {tracer_feedback: 1, lecture: RC.id, title: RC.title, role: state.role, name: state.name, designer_email: state.designer_email,
            saved_at: new Date().toISOString(), decisions, comments};
  }
  const fname = () => `TRACER feedback - ${state.role} - ${RC.id}.json`;
  function idb(mode, fn) {
    return new Promise((res, rej) => {
      const rq = indexedDB.open('tracer-review', 1);
      rq.onupgradeneeded = () => rq.result.createObjectStore('h');
      rq.onerror = () => rej(rq.error);
      rq.onsuccess = () => { const tx = rq.result.transaction('h', mode); const q = fn(tx.objectStore('h')); tx.oncomplete = () => res(q && q.result); tx.onerror = () => rej(tx.error); };
    });
  }
  const hkey = () => KEY + ':' + state.role;
  async function save() {
    const data = JSON.stringify(exportData(), null, 1);
    if (window.showSaveFilePicker) {
      try {
        let h = await idb('readonly', s => s.get(hkey())).catch(() => null);
        if (h && (await h.queryPermission({mode: 'readwrite'})) !== 'granted' && (await h.requestPermission({mode: 'readwrite'})) !== 'granted') h = null;
        if (!h) {
          toast('Choose the shared OneDrive folder this page is in, so your designer receives the file.', 6000);
          h = await showSaveFilePicker({suggestedName: fname(), types: [{description: 'TRACER feedback', accept: {'application/json': ['.json']}}]});
          await idb('readwrite', s => s.put(h, hkey())).catch(() => {});
        }
        const w = await h.createWritable(); await w.write(data); await w.close();
        dirty = false; state.saved_to = h.name; persist(); dirty = false;
        toast(`Saved to ${h.name}. OneDrive will sync it.`); return true;
      } catch (e) { if (e.name === 'AbortError') return false; console.warn('save picker failed, downloading instead', e); }
    }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([data], {type: 'application/json'})); a.download = fname(); a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    dirty = false; toast('Downloaded ' + fname() + '. Move it into the shared OneDrive folder.', 6000); return true;
  }
  function loadData(obj) {
    if (!obj || !obj.tracer_feedback) { toast('That file is not a TRACER feedback file.'); return; }
    if (obj.lecture && obj.lecture !== RC.id && !confirm(`This file is for a different lecture (${obj.lecture}). Load it anyway?`)) return;
    if (obj.role && obj.role !== state.role && !confirm(`This file was saved by the ${obj.role}. You are reviewing as the ${state.role}. Load it as your own work?`)) return;
    for (const [id, d] of Object.entries(obj.decisions || {})) {
      if (!/^[0-9a-f]{10}$/.test(id) || !['ok', 'fix', 'ask'].includes(d.status)) continue;  // only this page's item ids and known decisions
      state.decisions[id] = {status: d.status, text: String(d.text || ''), by: String(d.by || ''), role: String(d.role || ''), at: String(d.at || '')};
    }
    const have = new Set(state.comments.map(c => c.id));
    for (const cm of obj.comments || []) if (!have.has(cm.id)) { const c = {id: cm.id, anchor: cm.anchor, quote: cm.quote, text: cm.text, by: cm.by, role: cm.role, at: cm.at}; state.comments.push(c); commentCard(c); }
    if (!state.name && obj.name) state.name = obj.name;
    if (!state.designer_email && obj.designer_email) state.designer_email = obj.designer_email;
    syncWho(); allCards().filter(isItem).forEach(paintCard); persist(); rebuild();
    toast(`Loaded ${Object.keys(obj.decisions || {}).length} decision(s) and ${(obj.comments || []).length} comment(s).`);
  }
  $('#save').addEventListener('click', save);
  $('#load').addEventListener('click', () => $('#loadfile').click());
  $('#loadfile').addEventListener('change', async e => {
    const f = e.target.files[0]; if (!f) return;
    try { loadData(JSON.parse(await f.text())); } catch (err) { toast('Could not read that file.'); }
    e.target.value = '';
  });
  $('#mail').addEventListener('click', async () => {
    const ex = exportData(), ds = Object.values(ex.decisions);
    const cnt = s => ds.filter(d => d.status === s).length;
    const lines = [`Review of "${RC.title}" by ${state.name || 'the ' + state.role} (${state.role}).`, '',
      `${cnt('ok')} looked right, ${cnt('fix')} need a fix, ${cnt('ask')} questions, ${ex.comments.length} comment(s).`, ''];
    const list = [...ds.filter(d => d.status !== 'ok'), ...ds.filter(d => d.status === 'ok' && d.text)];
    for (const d of list) lines.push(`- ${d.where.split('·')[0].trim()} | ${lab(d.status)}${d.text ? ': ' + d.text : ''}${d.quote ? '  [about: "' + d.quote.slice(0, 80) + '"]' : ''}`);
    for (const c of ex.comments) lines.push(`- ${c.where || 'Comment'} | Comment: ${c.text || '(empty)'}  [on: "${(c.quote || '').slice(0, 80)}"]`);
    let body = lines.join('\n');
    const tail = `\n\nThe full feedback file (${state.saved_to || fname()}) is in the shared OneDrive folder.`;
    if (body.length > 1500) body = body.slice(0, 1500) + '\n... (the rest is in the feedback file)';
    if (dirty) { if (confirm('Save your feedback file first, so the full details reach your designer?')) await save(); }
    const to = state.role === 'professor' ? (state.designer_email || '') : '';
    location.href = `mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent('TRACER review: ' + RC.title)}&body=${encodeURIComponent(body + tail)}`;
  });

  // ---------- who / options / help / zoom
  function syncWho() {
    $('#role').value = state.role; $('#name').value = state.name || ''; $('#demail').value = state.designer_email || '';
    $('#demail').placeholder = state.role === 'designer' ? 'Your email (for the professor)' : "Designer's email";
  }
  $('#role').addEventListener('change', e => { state.role = e.target.value; syncWho(); everyCard().filter(isItem).forEach(paintCard); persist(); rebuild(); });
  $('#name').addEventListener('input', e => { state.name = e.target.value; persist(); });
  $('#demail').addEventListener('input', e => { state.designer_email = e.target.value.trim(); persist(); });
  $('.filters').addEventListener('click', e => { const b = e.target.closest('button[data-f]'); if (b) setFilter(b.dataset.f); });
  $('#teachtog').addEventListener('change', e => { document.body.classList.toggle('noteach', !e.target.checked); drawLink(); });
  const help = $('#helpdlg');
  $('#helpbtn').addEventListener('click', () => help.showModal());
  $('button', help).addEventListener('click', () => help.close());
  const zd = $('#zoomdlg');
  document.addEventListener('click', e => {
    const b = e.target.closest('.zoom'); if (!b || !zd.showModal) return;
    const i = $('img', b); $('img', zd).src = i.src; $('img', zd).alt = i.alt; $('p', zd).textContent = b.dataset.cap || ''; zd.showModal();
  });
  $('button', zd).addEventListener('click', () => zd.close());
  zd.addEventListener('click', e => { if (e.target === zd) zd.close(); });
  addEventListener('beforeunload', e => { if (dirty && (Object.keys(state.decisions).length || state.comments.length)) { e.preventDefault(); e.returnValue = ''; } });

  // ---------- start
  syncWho();
  everyCard().filter(c => !isSettled(c)).forEach(setupCard);
  for (const cm of RC.prior_comments || []) if (!state.comments.some(x => x.id === cm.id)) commentCard(cm, true);
  for (const cm of state.comments) commentCard(cm);
  rebuild();
  setFilter(allCards().some(c => isItem(c) && !handled(c)) ? 'open' : 'all');
  dirty = false;
  if (fresh && help.showModal) help.showModal();
})();
