// Real Chromium / DOM acceptance of the shipped assets; no Controller or QC.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { buildInjectionRows } from '../src/injection.js';
import { createFocusStore } from '../src/focus-store.js';

export class Cdp {
  static async open(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    return new Cdp(socket);
  }
  constructor(socket) {
    this.socket = socket; this.seq = 0; this.pending = new Map(); this.events = [];
    socket.onmessage = ({ data }) => {
      const msg = JSON.parse(data);
      if (!msg.id) { this.events.push(msg); return; }
      const entry = this.pending.get(msg.id);
      if (!entry) return;
      this.pending.delete(msg.id); clearTimeout(entry.timer);
      if (msg.error) entry.reject(new Error(JSON.stringify(msg.error))); else entry.resolve(msg.result);
    };
  }
  call(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.seq;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 20000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const result = await this.call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  }
  close() { this.socket.close(); }
}

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const chrome = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  assert.ok(fs.existsSync(chrome), 'set CHROME_PATH to an installed Chromium browser');
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'acp-dsh-browser-'));
  const rows = buildInjectionRows({ uiBase: '/acp', apiPrefix: '/acp-api', readOnly: true });
  const scripts = rows.filter((r) => r.kind === 'script-src').map((r) => `<script src="${r.src}"></script>`).join('');
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>body{background:rgb(255,255,255);color:rgb(0,0,0)}</style>
    ${rows.filter((r) => r.kind === 'html').map((r) => r.html).join('')}</head><body>
    <button id="host-sentinel">Host sentinel</button><div class="acp-dsh-panel-root" style="height:740px;width:575px"><div id="panel" class="acp-dsh-mount"></div></div>
    <script>window.__ACP_PANELS_CONFIG__=${JSON.stringify(rows[0].value)};window.hostFetch=window.fetch;window.hostES=window.EventSource;</script>${scripts}</body></html>`;
  const requests = [];
  const focus = createFocusStore();
  let slowHealth = false;
  const server = http.createServer((req, res) => {
    requests.push(req.url);
    if (req.url === '/fixture/slow-health') { slowHealth = true; res.end('ok'); return; }
    if (req.url === '/') { res.setHeader('content-type', 'text/html'); res.end(html); return; }
    if (req.url.startsWith('/acp/')) {
      const rel = req.url.slice(5).split('?')[0];
      const target = path.resolve(ROOT, 'assets', rel);
      if (!target.startsWith(path.resolve(ROOT, 'assets') + path.sep) || !fs.existsSync(target)) { res.writeHead(404); res.end(); return; }
      res.setHeader('content-type', target.endsWith('.css') ? 'text/css' : 'text/javascript'); res.end(fs.readFileSync(target)); return;
    }
    res.setHeader('content-type', 'application/json');
    if (req.url === '/acp-api/health' && slowHealth) {
      slowHealth = false;
      setTimeout(() => res.end(JSON.stringify({ ok: true, upstream: { status: 200 } })), 250);
      return;
    }
    if (req.url.startsWith('/acp-api/ui/focus')) {
      if (req.method === 'GET') {
        const record = focus.get(new URL(req.url, 'http://fixture').searchParams.get('session'));
        res.writeHead(record ? 200 : 404); res.end(JSON.stringify({ focus: record })); return;
      }
      let text = ''; req.on('data', (c) => { text += c; });
      req.on('end', () => {
        const body = JSON.parse(text);
        const result = body.operation === 'acquire' ? focus.acquire(body) : focus.put(body);
        res.writeHead(result.status || 200); res.end(JSON.stringify(result));
      }); return;
    }
    res.end(JSON.stringify(req.url === '/acp-api/health' ? { ok: true, upstream: { code: 'upstream_unreachable' } } : { ok: true, projects: [{project_id:'p-fixture',name:'Native project'}], jobs: [], items: [] }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const browser = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-extensions', 'about:blank'], { stdio: 'ignore', windowsHide: true });
  let cdp;
  try {
    const portFile = path.join(profile, 'DevToolsActivePort');
    for (let i = 0; i < 100 && !fs.existsSync(portFile); i++) await delay(100);
    const port = fs.readFileSync(portFile, 'utf8').split('\n')[0];
    const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    cdp = await Cdp.open(tabs.find((t) => t.type === 'page').webSocketDebuggerUrl);
    await cdp.call('Runtime.enable'); await cdp.call('Page.enable');
    await cdp.call('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
    for (let i = 0; i < 100 && !await cdp.evaluate('!!window.ACPDSH'); i++) await delay(100);
    const mounted = await cdp.evaluate('ACPDSH.mount(document.getElementById("panel"),{sessionId:"fixture-session"})');
    assert.equal(mounted.ok, true, JSON.stringify(mounted)); assert.equal(mounted.degraded, true);
    assert.equal(mounted.bridgeError, null); assert.equal(mounted.generation, 1);
    await delay(600);
    const state = await cdp.evaluate(`({shell:!!document.querySelector('[data-acp-ready="true"]'),
      panels:document.querySelectorAll('.acp-panel').length,
      errors:[...document.querySelectorAll('[data-acp-state="error"]')].map(e=>e.textContent.slice(0,300)),
      placeholders:document.querySelectorAll('.is-placeholder').length,
      globals:!!(window.ACPShell||window.ACPClient||window.ACPAdapters),
      fetchIntact:window.fetch===window.hostFetch,esIntact:window.EventSource===window.hostES,
      hostBackground:getComputedStyle(document.body).backgroundColor,
      contained:document.querySelector('.acp-dsh-host').getBoundingClientRect().bottom<=document.getElementById('panel').getBoundingClientRect().bottom+1})`);
    assert.equal(state.shell, true, JSON.stringify(state)); assert.ok(state.panels > 0);
    assert.equal(state.placeholders, 0, JSON.stringify(state));
    assert.equal(state.globals, false); assert.equal(state.fetchIntact, true); assert.equal(state.esIntact, true);
    assert.equal(state.hostBackground, 'rgb(255, 255, 255)');
    assert.equal(state.contained, true, 'viewport-sized standalone roots must fit the native panel');
    for (const width of [1480, 1100, 850, 575, 390]) {
      await cdp.evaluate(`document.getElementById('panel').parentElement.style.width='${width}px'`);
      await delay(30);
      const geometry = await cdp.evaluate(`(()=>{
        const panel=document.getElementById('panel'),box=panel.getBoundingClientRect();
        const shell=panel.querySelector('#acp-shell').getBoundingClientRect();
        const status=panel.querySelector('.acp-next-statusbar').getBoundingClientRect();
        const rail=panel.querySelector('#acp-next-task-rail'),button=panel.querySelector('#acp-next-task-open');
        return {contained:shell.right<=box.right+1&&shell.bottom<=box.bottom+1&&status.bottom<=box.bottom+1,
          taskVisible:getComputedStyle(rail).display!=='none'||getComputedStyle(button).display!=='none'};
      })()`);
      assert.equal(geometry.contained,true,`${width}px native container bounds`);
      assert.equal(geometry.taskVisible,true,`${width}px task list or summary remains reachable`);
    }
    await cdp.evaluate(`document.getElementById('panel').parentElement.style.width='575px'`);
    await cdp.evaluate(`document.querySelector('#panel #acp-project-btn').click()`);
    await delay(50);
    await cdp.evaluate(`document.querySelector('#panel [data-project-id="p-fixture"]').click()`);
    await delay(50);
    const retained = await cdp.evaluate(`(async()=>{
      const panel=document.getElementById('panel');
      const shell=panel.querySelector('#acp-shell');
      const draft=document.createElement('textarea');draft.value='unsaved native-tab draft';shell.appendChild(draft);
      const parked=ACPDSH.suspend(panel);
      const other=document.createElement('div');other.id='other-native-panel';other.style.cssText='height:740px;width:575px';document.body.appendChild(other);
      const second=await ACPDSH.mount(other,{sessionId:'another-native-session'});
      const ownSkeleton=!!other.querySelector('#acp-shell');
      const suspendedSecond=ACPDSH.suspend(other);
      const resumed=await ACPDSH.resume(panel);
      const sameDOM=panel.querySelector('#acp-shell')===shell && draft.isConnected && draft.value==='unsaved native-tab draft';
      const activeSession=ACPDSH.state().sessionId;
      ACPDSH.unmount(other);other.remove();
      return {parked,second,ownSkeleton,suspendedSecond,resumed,sameDOM,activeSession,stillMounted:ACPDSH.state().mounted};
    })()`);
    assert.equal(retained.parked.retained,true);
    assert.equal(retained.second.ok,true,JSON.stringify(retained));
    assert.equal(retained.ownSkeleton,true,'duplicate ids in inactive tabs must not hide the new skeleton');
    assert.equal(retained.resumed.ok,true,JSON.stringify(retained));
    assert.equal(retained.sameDOM,true,'native tab changes must preserve unsaved DOM and module state');
    assert.equal(retained.activeSession,'fixture-session');
    assert.equal(retained.stillMounted,true,'closing an inactive tab must not close the foreground runtime');
    const competing = await cdp.evaluate(`(async()=>{
      const other=document.createElement('div');document.body.appendChild(other);
      const result=await ACPDSH.mount(other,{sessionId:'competing'});
      ACPDSH.unmount(other);other.remove();return result;
    })()`);
    assert.equal(competing.code, 'already_mounted');
    assert.equal(await cdp.evaluate('ACPDSH.state().mounted'), true, 'failed second panel must not dispose the owner');
    await cdp.evaluate('fetch("/api/host-probe")');
    assert.ok(requests.includes('/api/host-probe')); assert.ok(!requests.includes('/acp-api/host-probe'));
    const errors = cdp.events.filter((e) => e.method === 'Runtime.exceptionThrown');
    assert.equal(errors.length, 0, JSON.stringify(errors));
    await cdp.evaluate('ACPDSH.unmount()');
    assert.equal(await cdp.evaluate('document.querySelectorAll(".acp-dsh-host").length'), 0);
    const reopened = await cdp.evaluate('ACPDSH.mount(document.getElementById("panel"),{sessionId:"fixture-session"})');
    assert.equal(reopened.ok, true, JSON.stringify(reopened));
    assert.equal(reopened.generation, retained.resumed.generation + 1); assert.equal(reopened.bridgeError, null);
    await delay(100);
    assert.equal(await cdp.evaluate(`document.querySelector('#panel #acp-project-name').textContent`),'Native project','closing/reopening restores references through the existing focus store');
    assert.equal(await cdp.evaluate('document.querySelectorAll(".acp-dsh-host").length'), 1);
    await cdp.evaluate('ACPDSH.unmount()');
    const switched = await cdp.evaluate('ACPDSH.mount(document.getElementById("panel"),{sessionId:"second-session"})');
    assert.equal(switched.ok, true); assert.equal(switched.sessionId, 'second-session');
    assert.equal(switched.generation, 1); await cdp.evaluate('ACPDSH.unmount()');
    await cdp.evaluate('fetch("/fixture/slow-health")');
    const cancelled = await cdp.evaluate(`(async()=>{
      const panel=document.getElementById('panel');
      const old=ACPDSH.mount(panel,{sessionId:'cancelled'});
      await new Promise(r=>setTimeout(r,30));ACPDSH.unmount(panel);
      const live=await ACPDSH.mount(panel,{sessionId:'live-after-cancel'});
      const late=await old;return {live,late,state:ACPDSH.state()};
    })()`);
    assert.equal(cancelled.late.code, 'mount_cancelled');
    assert.equal(cancelled.live.ok, true); assert.equal(cancelled.state.degraded, true);
    assert.equal(cancelled.state.sessionId, 'live-after-cancel');
    await cdp.evaluate('ACPDSH.unmount()');
    /* Window-level pinned workbench: reassociation is an identity handoff, not
     * a rebuild. The shipped runtime must keep its host, shell and DOM. */
    const reassociated = await cdp.evaluate(`(async()=>{
      const panel=document.getElementById('panel');
      const mounted=await ACPDSH.mount(panel,{sessionId:'linked-a'});
      const host=panel.querySelector('.acp-dsh-host'), shell=panel.querySelector('#acp-shell');
      const same=ACPDSH.reassociate('linked-a');
      const moved=await ACPDSH.reassociate('linked-b');
      const after={sessionId:ACPDSH.state().sessionId,
        sameHost:panel.querySelector('.acp-dsh-host')===host,sameShell:panel.querySelector('#acp-shell')===shell,
        hosts:panel.querySelectorAll('.acp-dsh-host').length};
      ACPDSH.unmount();
      return {mountedOk:mounted.ok,same,moved,after};
    })()`);
    assert.equal(reassociated.mountedOk,true,JSON.stringify(reassociated));
    assert.equal(reassociated.same.changed,false);
    assert.equal(reassociated.moved.changed,true);
    assert.equal(reassociated.moved.previousSessionId,'linked-a');
    assert.equal(reassociated.after.sessionId,'linked-b');
    assert.equal(reassociated.after.sameHost,true,'reassociation must not rebuild the runtime host');
    assert.equal(reassociated.after.sameShell,true,'reassociation must not rebuild the shell');
    assert.equal(reassociated.after.hosts,1);
    console.log(JSON.stringify({ status: 'PASS', scope: 'shipped assets in real Chromium, fixture API', widths: [1480,1100,850,575,390], mounted, state, retained, reopened, reassociated, projectRestored: true, uncaughtErrors: errors.length }, null, 2));
  } finally {
    if (cdp) cdp.close(); browser.kill(); server.closeAllConnections(); server.close();
    // Chromium may still hold its isolated profile briefly after shutdown.
    assert.equal(path.dirname(path.resolve(profile)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(profile).startsWith('acp-dsh-browser-'));
    for (let i = 0; i < 20; i++) {
      try { fs.rmSync(profile, { recursive: true, force: true }); break; } catch { await delay(100); }
    }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
