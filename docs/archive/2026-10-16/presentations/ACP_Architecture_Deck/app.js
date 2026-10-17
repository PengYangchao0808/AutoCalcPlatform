/* ACP Architecture Deck — browser-only presentation controller.
 * No npm, no server, no external dependency. Edit slide text in slides.js;
 * edit palette/layout in styles.css; edit presentation controls here.
 */
(() => {
  'use strict';
  const slides = window.ACP_SLIDES || [];
  const byId = id => document.getElementById(id);
  let slideNode = byId('slide');
  const sizer = byId('stage-sizer');
  const shell = byId('stage-shell');
  const sectionNode = byId('app-section');
  const titleNode = byId('app-title');
  const countNode = byId('page-count');
  const barNode = byId('progress-bar');
  const backdrop = byId('overlay-backdrop');
  const outlinePanel = byId('outline-panel');
  const notesPanel = byId('notes-panel');
  const notesTitle = byId('notes-title');
  const notesContent = byId('notes-content');
  const outlineList = byId('outline-list');
  const outlineSearch = byId('outline-search');
  const detailModal = byId('detail-modal');
  let current = 0;
  let outlineOpen = false;
  let notesOpen = false;
  let toastTimer = null;
  const statusLabel = { vision:'目标愿景 · 非现有功能声明', design:'目标架构 / 待实施', candidate:'开源 / 产品研究判断 · 待验证', baseline:'2026-10-16 静态清点' };
  const pad = n => String(n).padStart(2, '0');
  const escapeHTML = value => String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');

  function buildSlide(s, idx) {
    const classes = [s.theme==='dark'?'dark':'light', s.kind==='cover'?'cover':'', s.kind==='chapter'?'chapter':''].filter(Boolean).join(' ');
    const top = s.kind==='cover' || s.kind==='chapter' ? '' : `
      <div class="s-top"><span class="s-kicker">${escapeHTML(s.section)} / ACP BLUEPRINT</span><span class="s-phase">${escapeHTML(statusLabel[s.status] || '')}</span></div>
      <div class="s-header"><h1>${escapeHTML(s.title)}</h1>${s.subtitle ? `<p class="s-subtitle">${escapeHTML(s.subtitle)}</p>`:''}</div>`;
    const footer = s.kind==='cover' ? '' : `
      <footer class="s-footer"><span>ACP · 科研工作台架构调整方案 / v1.5</span><div class="footer-right"><span>${escapeHTML(s.source||'')}</span><span class="footer-num">${pad(idx+1)} / ${pad(slides.length)}</span></div></footer>`;
    return `<div class="slide ${classes}" data-slide-id="${escapeHTML(s.id)}" data-slide-number="${idx+1}">${top}<div class="${s.kind==='cover'?'cover-body':'s-body'}">${s.html}</div>${footer}</div>`;
  }

  function drawSlide() {
    const s = slides[current];
    if (!s) return;
    slideNode.className = `slide ${s.theme==='dark'?'dark':'light'} ${s.kind||''}`;
    const markup = buildSlide(s,current);
    const temp = document.createElement('div');
    temp.innerHTML = markup;
    const nextSlide = temp.firstElementChild;
    slideNode.replaceWith(nextSlide);
    // Keep a fresh reference because this slide element is replaced on navigation.
    slideNode = nextSlide;
    slideNode.id = 'slide';
    sectionNode.textContent = s.section;
    titleNode.textContent = s.title;
    countNode.innerHTML = `${pad(current+1)} <span>/ ${pad(slides.length)}</span>`;
    barNode.style.width = `${((current+1)/slides.length)*100}%`;
    byId('btn-prev').disabled = current === 0;
    byId('btn-next').disabled = current === slides.length-1;
    notesTitle.textContent = s.title;
    notesContent.textContent = s.notes || '本页暂无额外备注。';
    updateNavHighlight();
    document.title = `${s.title} | ACP · Architecture Deck`;
    if (window.location.hash !== `#${s.id}`) {
      try { history.replaceState(null, '', `#${s.id}`); }
      catch (_e) { /* file URL behavior varies */ }
    }
    resize();
  }

  function goto(target) {
    const next = Math.max(0,Math.min(slides.length-1,target));
    if (next === current) return;
    current = next;
    drawSlide();
  }

  function resize() {
    const w = shell.clientWidth - 2;
    const h = shell.clientHeight - 2;
    const scale = Math.min(w/1366, h/768);
    sizer.style.transform = `scale(${Math.max(0.16, scale)})`;
    sizer.style.marginLeft = '0';
    sizer.style.marginTop = '0';
  }

  function groups() {
    return [...new Set(slides.map(s=>s.section))];
  }

  function buildOutline() {
    const needle = outlineSearch.value.toLowerCase().trim();
    let html = '';
    let lastGroup = '';
    slides.forEach((s,i) => {
      if (needle && !(`${s.title} ${s.subtitle} ${s.section}`.toLowerCase().includes(needle))) return;
      if (s.section !== lastGroup) {
        html += `<div class="outline-group">${escapeHTML(s.section)}</div>`;
        lastGroup = s.section;
      }
      html += `<button class="outline-item ${current===i?'active':''}" data-goto="${i}"><span class="index">${pad(i+1)}</span><span>${escapeHTML(s.title)}</span></button>`;
    });
    outlineList.innerHTML = html || '<p style="font-size:13px;color:#627f8b">没有找到相关幻灯片。</p>';
  }

  function buildChapterDots() {
    const seen = new Set();
    byId('chapter-dots').innerHTML = slides.map((s,i) => {
      if(seen.has(s.section)) return '';
      seen.add(s.section);
      return `<button class="chapter-mini-btn" data-goto="${i}" title="${escapeHTML(s.section)}" aria-label="跳转至${escapeHTML(s.section)}"></button>`;
    }).join('');
  }

  function updateNavHighlight() {
    const buttons = [...byId('chapter-dots').querySelectorAll('button')];
    const sectionIndex = groups().indexOf(slides[current].section);
    buttons.forEach((b,i)=>b.classList.toggle('is-active',i===sectionIndex));
    buildOutline();
  }

  function toggleOutline(force) {
    outlineOpen = typeof force==='boolean'?force:!outlineOpen;
    if (outlineOpen) { toggleNotes(false); }
    outlinePanel.classList.toggle('open',outlineOpen);
    outlinePanel.setAttribute('aria-hidden', String(!outlineOpen));
    backdrop.classList.toggle('show',outlineOpen);
    if(outlineOpen){outlineSearch.focus(); const active=outlineList.querySelector('.active');active?.scrollIntoView({block:'nearest'});}
  }

  function toggleNotes(force) {
    notesOpen = typeof force==='boolean'?force:!notesOpen;
    if(notesOpen){toggleOutline(false);}
    notesPanel.classList.toggle('open',notesOpen);
    notesPanel.setAttribute('aria-hidden',String(!notesOpen));
  }

  function toast(message) {
    const node = byId('toast');
    node.textContent=message;
    node.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer=setTimeout(()=>node.classList.remove('show'),2500);
  }

  function inspect(key) {
    const d=(window.ACP_DETAILS||{})[key];
    if(!d)return;
    byId('detail-title').textContent=d.title;
    byId('detail-copy').textContent=d.copy;
    byId('detail-tags').innerHTML=`<div class="tags">${d.tags.map(t=>`<span class="tag">${escapeHTML(t)}</span>`).join('')}</div>`;
    if(typeof detailModal.showModal==='function')detailModal.showModal();
  }

  function printSlides() {
    toggleOutline(false);
    toggleNotes(false);
    const root=byId('print-root');
    root.innerHTML=slides.map((s,i)=>`<div class="print-slide">${buildSlide(s,i)}</div>`).join('');
    const cleanup=()=>{root.innerHTML='';window.removeEventListener('afterprint',cleanup);};
    window.addEventListener('afterprint',cleanup);
    window.print();
  }

  byId('btn-prev').addEventListener('click',()=>goto(current-1));
  byId('btn-next').addEventListener('click',()=>goto(current+1));
  byId('btn-outline').addEventListener('click',()=>toggleOutline());
  byId('btn-notes').addEventListener('click',()=>toggleNotes());
  byId('btn-print').addEventListener('click',printSlides);
  byId('btn-close-outline').addEventListener('click',()=>toggleOutline(false));
  byId('btn-close-notes').addEventListener('click',()=>toggleNotes(false));
  byId('btn-close-detail').addEventListener('click',()=>detailModal.close());
  byId('overlay-backdrop').addEventListener('click',()=>toggleOutline(false));
  byId('btn-fullscreen').addEventListener('click',()=>{
    if(document.fullscreenElement){document.exitFullscreen().catch(()=>{});}
    else if(document.documentElement.requestFullscreen){document.documentElement.requestFullscreen().catch(()=>toast('此浏览器不支持全屏'));}
  });
  outlineSearch.addEventListener('input',buildOutline);
  document.addEventListener('click',e=>{
    const target=e.target.closest('[data-goto]');
    if(target){goto(Number(target.dataset.goto));toggleOutline(false);return;}
    const inspectTarget=e.target.closest('[data-inspect]');
    if(inspectTarget) inspect(inspectTarget.dataset.inspect);
  });
  document.addEventListener('keydown',e=>{
    if(detailModal.open || (e.target.matches && e.target.matches('input, textarea, [contenteditable]'))){
      if(e.key==='Escape'){toggleOutline(false);toggleNotes(false);}return;
    }
    if(e.altKey||e.ctrlKey||e.metaKey)return;
    const k=e.key.toLowerCase();
    if(['arrowright','arrowdown','pagedown',' '].includes(k)){e.preventDefault();goto(current+1);}
    else if(['arrowleft','arrowup','pageup'].includes(k)){e.preventDefault();goto(current-1);}
    else if(k==='home'){goto(0);}else if(k==='end'){goto(slides.length-1);}
    else if(k==='o'){toggleOutline();}else if(k==='n'){toggleNotes();}
    else if(k==='f'){byId('btn-fullscreen').click();}
    else if(k==='escape'){toggleOutline(false);toggleNotes(false);}
    else if(k==='enter'&&document.activeElement?.dataset?.inspect){inspect(document.activeElement.dataset.inspect);}
  });
  window.addEventListener('resize',resize);
  window.addEventListener('hashchange',()=>{
    const id=decodeURIComponent(window.location.hash.substring(1));
    const index=slides.findIndex(s=>s.id===id);
    if(index!==-1&&index!==current){current=index;drawSlide();}
  });
  let touchX=null;
  shell.addEventListener('touchstart',e=>{touchX=e.touches.length===1?e.touches[0].clientX:null;},{passive:true});
  shell.addEventListener('touchend',e=>{if(touchX===null||!e.changedTouches.length)return;const d=e.changedTouches[0].clientX-touchX;if(Math.abs(d)>60)goto(current+(d<0?1:-1));touchX=null;},{passive:true});

  if(!slides.length){slideNode.innerHTML='<p>未加载幻灯片，请确认 slides.js 文件存在。</p>';return;}
  const initial=slides.findIndex(s=>s.id===decodeURIComponent(window.location.hash.substring(1)));
  if(initial>=0)current=initial;
  buildChapterDots();
  drawSlide();
})();
