// Review of the isolated layout proposal only; no ACP service or QC requests.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { Cdp } from '../../../../../frontend/dsh-plugin/test/browser-smoke.mjs';

const dir = path.dirname(fileURLToPath(import.meta.url));
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'acp-layout-proposal-'));
const chrome = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
assert.ok(fs.existsSync(chrome));
const browser = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-extensions',
  '--disable-component-update', 'about:blank'], { stdio: 'ignore', windowsHide: true });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let cdp;
const checks = [];
async function check(name, expression) {
  const passed = await cdp.evaluate(expression);
  if (!passed) console.log(JSON.stringify(await cdp.evaluate(`({width:innerWidth,scroll:document.documentElement.scrollWidth,overflow:[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,12).map(e=>({tag:e.tagName,cls:e.className.baseVal??e.className,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right}))})`)));
  assert.equal(passed, true, name);
  checks.push(name);
}
async function click(selector) { await cdp.evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`); }
async function shot(name) {
  await cdp.evaluate('document.querySelector("#toast").hidden=true');
  const result = await cdp.call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(path.join(dir, name), Buffer.from(result.data, 'base64'));
}
try {
  const portFile = path.join(profile, 'DevToolsActivePort');
  for (let i = 0; i < 100 && !fs.existsSync(portFile); i++) await delay(100);
  const port = fs.readFileSync(portFile, 'utf8').split('\n')[0];
  const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  cdp = await Cdp.open(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
  await cdp.call('Runtime.enable'); await cdp.call('Page.enable'); await cdp.call('Network.enable');
  await cdp.call('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cdp.call('Page.navigate', { url: pathToFileURL(path.join(dir, 'index.html')).href });
  for (let i = 0; i < 50 && !await cdp.evaluate('!!document.getElementById("task-items")?.children.length'); i++) await delay(100);
  for (const view of ['A', 'B', 'C', 'E', 'F']) {
    await click(`.nav [data-view="${view}"]`);
    await check(`desktop ${view}: one central view and visible task rail`, `
      [...document.querySelectorAll('[data-page]')].filter(e=>!e.hidden).length===1 &&
      !document.querySelector('[data-page="${view}"]').hidden &&
      document.querySelector('.tasks').getBoundingClientRect().width>0 &&
      document.querySelector('.tasks').getBoundingClientRect().right<=innerWidth+1`);
  }
  await click('.nav [data-view="A"]'); await shot('A-network-1600.png');
  await cdp.evaluate(`document.querySelector('svg [data-mol="m1"]').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
  await check('network object opens C and related tasks', `!document.querySelector('[data-page="C"]').hidden && document.querySelector('#task-count').textContent==='4'`);
  await shot('C-structure-1600.png');
  await click('[data-filter="all"]'); await check('all project tasks', `document.querySelector('#task-count').textContent==='6'`);
  await click('[data-filter="related"]'); await click('#pin');
  await click('.nav [data-view="A"]');
  await cdp.evaluate(`document.querySelector('svg [data-mol="m2"]').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
  await check('pin preserves task scope while central object changes', `document.querySelector('#task-molecule').textContent.includes('底物 A') && document.querySelector('#context-mol').textContent.includes('底物 B') && document.querySelector('#task-count').textContent==='4'`);
  await click('#pin'); await check('unpin follows new object', `document.querySelector('#task-count').textContent==='1'`);
  await click('.nav [data-view="A"]');
  await cdp.evaluate(`document.querySelector('svg [data-mol="m1"]').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
  await click('.nav [data-view="E"]');
  await cdp.evaluate(`document.querySelector('#config-form input[type="number"]').value='2'; document.querySelector('#config-form input[type="number"]').dispatchEvent(new Event('input',{bubbles:true}))`);
  await click('.nav [data-view="A"]'); await click('.nav [data-view="E"]');
  await check('view switching retains configuration input', `document.querySelector('#config-form input[type="number"]').value==='2'`);
  await click('.nav [data-view="A"]');
  await cdp.evaluate(`document.querySelector('svg [data-mol="m2"]').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
  await click('[data-task="t5"]'); await click('.nav [data-view="E"]');
  await check('browsing another task preserves draft source', `document.querySelector('#config-source').textContent.includes('底物 A') && document.querySelector('#context-mol').textContent.includes('底物 B')`);
  await check('browsing another task preserves draft values', `document.querySelector('#config-form input[type="number"]').value==='2'`);
  await click('.nav [data-view="A"]');
  await cdp.evaluate(`document.querySelector('svg [data-mol="m1"]').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
  await click('.nav [data-view="E"]');
  await click('#preflight');
  await check('example preflight gates example receipt', `!document.querySelector('#submit').disabled && !document.querySelector('#config-detail').hidden`);
  await cdp.evaluate(`document.querySelector('#config-form input[type="number"]').value='0'; document.querySelector('#config-form input[type="number"]').dispatchEvent(new Event('input',{bubbles:true}))`);
  await check('input changes invalidate example preflight', `document.querySelector('#submit').disabled`);
  await click('.mini-tabs [data-config-tab="workflow"]'); await shot('E-config-1600.png');
  await click('[data-task="t2"]');
  await check('task click opens F for same attempt', `!document.querySelector('[data-page="F"]').hidden && document.querySelector('#result-title').textContent==='PES 路径探索' && document.querySelector('#result-attempt').textContent==='attempt 1'`);
  await cdp.evaluate(`document.querySelector('[data-page="F"] circle[data-frame="2"]').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
  await check('curve point updates selected frame', `document.querySelector('#result-frame').textContent==='03'`);
  await shot('F-results-1600.png');
  for (const tab of ['logs', 'files', 'terminal']) {
    await click(`[data-dock="${tab}"]`);
    await check(`G ${tab}: opens without hiding task rail`, `!document.querySelector('#dock-body').hidden && document.querySelector('.tasks').getBoundingClientRect().width>0`);
  }
  await click('[data-action="dock-close"]');
  const sizes = [];
  for (const [width,height] of [[1920,1080],[1440,900],[1280,900],[1024,768],[850,900],[768,1024],[390,844],[375,812]]) {
    await cdp.call('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    for (const view of ['A', 'B', 'C', 'E', 'F']) {
      await click(`.nav [data-view="${view}"]`);
      await check(`${width}px ${view}: no horizontal page overflow`, `document.documentElement.scrollWidth<=innerWidth+1`);
      await check(`${width}px ${view}: current task remains discoverable`, `document.querySelector('.tasks').getBoundingClientRect().width>0 || (document.querySelector('#tasks-toggle').getBoundingClientRect().width>0 && document.querySelector('#status-task').textContent.length>0)`);
    }
    sizes.push({ width, height });
  }
  await click('#tasks-toggle');
  await check('narrow task drawer opens', `document.querySelector('.tasks').classList.contains('mobile-open') && document.querySelector('#tasks-toggle').getAttribute('aria-expanded')==='true'`);
  await click('.close-tasks');
  await click('.nav [data-view="A"]'); await shot('A-network-375.png');
  await cdp.call('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cdp.evaluate(`document.querySelector('svg [data-mol="m1"]').dispatchEvent(new MouseEvent('click',{bubbles:true}))`);
  await click('[data-task="t2"]'); await click('.nav [data-view="A"]');
  await cdp.evaluate('document.activeElement?.blur()');
  await shot('A-network-1600.png');
  const errors = cdp.events.filter(e => e.method === 'Runtime.exceptionThrown');
  assert.equal(errors.length, 0, JSON.stringify(errors));
  const appNetwork = cdp.events.filter(e => e.method === 'Network.requestWillBeSent' && /^https?:/.test(e.params.request.url));
  assert.equal(appNetwork.length, 0, 'proposal must not call a service');
  const result = { status: 'PASS', scope: 'isolated proposal, local Chromium, all scientific content illustrative', checks: checks.length, sizes, uncaughtErrors: errors.length, httpRequests: appNetwork.length, verified: checks };
  fs.writeFileSync(path.join(dir, 'validation.json'), JSON.stringify(result, null, 2)+'\n');
  console.log(JSON.stringify({ status: result.status, checks: result.checks, uncaughtErrors: result.uncaughtErrors, httpRequests: result.httpRequests }));
} finally {
  cdp?.close(); browser.kill();
  // The isolated profile remains in the system temporary directory; no recursive deletion.
}
