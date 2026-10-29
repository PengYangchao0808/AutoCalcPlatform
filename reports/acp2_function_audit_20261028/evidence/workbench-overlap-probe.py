import json,os,socket,tempfile,threading,time
from pathlib import Path
OUT=Path('/ACPServer/AutoCalcPlatform/reports/acp2_function_audit_20261028/evidence')
os.environ['ACP_RUN_ROOT']=tempfile.mkdtemp(prefix='acp2-audit-ui-')
import uvicorn
from acp.api.server import create_app
from playwright.sync_api import sync_playwright
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
server=uvicorn.Server(uvicorn.Config(create_app(run_root=os.environ['ACP_RUN_ROOT']),host='127.0.0.1',port=port,log_level='error'))
t=threading.Thread(target=server.run,daemon=True);t.start()
for _ in range(100):
 if server.started:break
 time.sleep(.1)
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True);p=browser.new_page(viewport={'width':1280,'height':800})
  p.route('**/api/v2/task-view?**',lambda r:r.fulfill(json={'groups':[],'facets':{},'total':0,'counts':{'completed':0},'offset':0,'limit':200,'next_offset':None}))
  p.route('**/js/vendor/3Dmol-*',lambda r:r.abort())
  p.goto(f'http://127.0.0.1:{port}/',wait_until='domcontentloaded');p.wait_for_timeout(1500)
  facts=p.evaluate('''() => {
    const ids=['btn-new-project','queue-counts','science-question-panel','capability-form-panel','queue-list'];
    return {viewport:{w:innerWidth,h:innerHeight},elements:ids.map(id=>{const e=document.getElementById(id);if(!e)return {id,missing:true};const r=e.getBoundingClientRect();const top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {id,rect:{x:r.x,y:r.y,w:r.width,h:r.height},text:e.innerText.slice(0,100),top:top?{id:top.id,cls:top.className,tag:top.tagName}:null}})};
  }''')
  try:p.locator('#btn-new-project').click(trial=True,timeout=1800);facts['new_project_click']='actionable'
  except Exception as e:facts['new_project_click']='blocked';facts['click_error']=str(e)
  p.screenshot(path=str(OUT/'workbench-overlap.png'),full_page=False)
  (OUT/'workbench-overlap.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n')
  print(json.dumps(facts,ensure_ascii=False));browser.close()
finally:server.should_exit=True;t.join(5)
