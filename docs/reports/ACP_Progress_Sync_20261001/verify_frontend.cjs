// Execute the actual served-workbench helper and timeline with a minimal DOM.
// No browser state or production task is changed.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = __dirname;
const html = fs.readFileSync(path.resolve(root, '../../../frontend/ACP_Workbench_v2.html'), 'utf8');
function extract(name) {
  const start = html.indexOf('function ' + name + '(');
  const next = html.indexOf('\nfunction ', start + 1);
  if (start < 0 || next < 0) throw new Error('Cannot extract ' + name);
  return html.slice(start, next);
}
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.classList = { add() {} }; this.style = {}; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener() {}
}
const detail = JSON.parse(fs.readFileSync(path.join(root, 'evidence/job_detail.json'), 'utf8'));
const summary = JSON.parse(fs.readFileSync(path.join(root, 'evidence/job_summary.json'), 'utf8'));
const context = {
  document: { createElement: (tag) => new Element(tag) },
  getJobDetail: () => detail,
  t: (key) => key,
  translateStageName: (key) => key,
  requestAnimationFrame: () => {},
};
vm.createContext(context);
vm.runInContext(extract('getStatusClass') + extract('normalizedProgress') + extract('isProgressKnown') + extract('isProgressIndeterminate'), context);
vm.runInContext(extract('renderWorkflowTimeline'), context);
const container = new Element('div');
context.renderWorkflowTimeline(container, summary);
const viewport = container.children[1];
const labels = viewport.children[0].children.filter(el => el.className === 'tl-node').map(el => el.attributes.title.split(' — ')[0]);
const observed = {
  queue_progress_state: summary.progress_state,
  queue_helper_reports_indeterminate: context.isProgressIndeterminate(summary),
  queue_display_percent: context.normalizedProgress(summary),
  right_panel_stage_position_percent: Math.round(summary.live_status.stage_index / summary.live_status.stage_total * 100),
  timeline_labels: labels,
  stage_index: summary.stage_index,
  current_stage: summary.current_stage,
};
if (observed.queue_helper_reports_indeterminate !== false || observed.queue_display_percent !== 0) throw new Error('Progress defect not reproduced');
if (labels.length !== 4 || labels[0] !== '准备') throw new Error('Timeline mismatch not reproduced');
fs.writeFileSync(path.join(root, 'evidence/frontend_reproductions.json'), JSON.stringify(observed, null, 2) + '\n');
process.stdout.write(JSON.stringify(observed, null, 2) + '\n');
