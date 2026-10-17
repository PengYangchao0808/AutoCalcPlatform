// Execute the actual workbench functions against a small DOM surface.
// H2 decomposition moved these helpers out of the inline HTML into
// js/workbench/{compat/api.js, features/job-detail.js}; extract from the
// living production sources instead of the HTML shell.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const html = fs.readFileSync(process.argv[2], 'utf8');
const jsRoot = path.resolve(path.dirname(process.argv[2]), 'js');
const corpus = [
  'workbench/compat/api.js',
  'workbench/features/job-detail.js',
].map(rel => fs.readFileSync(path.join(jsRoot, rel), 'utf8')).join('\n');
function source(name) {
  const start = corpus.indexOf('function ' + name + '(');
  assert(start >= 0, name);
  const end = corpus.indexOf('\nfunction ', start + 1);
  return corpus.slice(start, end);
}
class Element {
  constructor() { this.children = []; this.attrs = {}; this.style = {}; this.classList = { add() {} }; }
  appendChild(child) { this.children.push(child); }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener() {}
}
const order = ['optimize', 'frequency', 'single_point', 'thermochemistry'];
const context = {
  document: { createElement: () => new Element() },
  requestAnimationFrame() {},
  t: key => key,
  translateStageName: key => key,
  getStatusClass: status => status.toLowerCase(),
  getJobDetail: () => ({ stages: ['prepare', ...order, 'finalize'].map(stage_name => ({ stage_name })) }),
};
vm.createContext(context);
for (const name of ['normalizedProgress', 'isProgressKnown', 'isProgressIndeterminate', 'renderWorkflowTimeline']) {
  vm.runInContext(source(name), context);
}
const job = { id: 'regression', status: 'running', progress: 0.25, progress_state: 'indeterminate', stage_index: 2, stage_total: 4, current_stage: 'frequency', stage_order: order, spec: { workflow: 'BatchOptimize' } };
assert.strictEqual(context.isProgressKnown(job), false);
assert.strictEqual(context.isProgressIndeterminate(job), true);
assert.strictEqual(context.isProgressIndeterminate({ ...job, status: 'paused' }), true);
assert.strictEqual(context.normalizedProgress({ ...job, progress_state: 'determinate' }), 25);
for (const stage_order of [order, []]) {
  const container = new Element();
  context.renderWorkflowTimeline(container, { ...job, stage_order });
  const nodes = container.children[1].children[0].children.filter(child => child.className === 'tl-node');
  assert.strictEqual(nodes.length, 4);
  nodes.forEach((node, index) => assert(node.attrs['aria-label'].startsWith(order[index] + ',')));
  assert.strictEqual(nodes[1].children[1].textContent, 'frequency');
}
console.log('Progress indeterminacy and four-stage timeline verified.');
