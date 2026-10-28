// Review probe: actual client modules; injected adapter only, no HTTP/QC.
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = path.resolve(__dirname, '../..');
const sandbox = {console, AbortController, Promise, setTimeout, clearTimeout};
sandbox.window = sandbox;
vm.createContext(sandbox);
for (const name of ['client/resource-store.js', 'workbench/contracts/experience_contract.js', 'client/science-query.js']) {
  vm.runInContext(fs.readFileSync(path.join(root, 'frontend/js', name), 'utf8'), sandbox, {filename:name});
}
(async () => {
  let finish;
  const calls = [];
  sandbox.ACPClient.science.init({v2: (url, options) => {
    calls.push(JSON.parse(options.body));
    return new Promise(resolve => { finish = resolve; });
  }});
  const before = {workflow:'singlepoint', input:{source:'XYZ-A', charge:0}, method:{}, resources:{nproc:4}};
  const after = {...before, input:{source:'XYZ-B', charge:1}};
  const scope = {projectId:'review-project'};
  const first = sandbox.ACPClient.science.preflight(before, scope, {identity:'manual-singlepoint'});
  const second = sandbox.ACPClient.science.preflight(after, scope, {identity:'manual-singlepoint'});
  finish({executed:false, metering_consumed:false, transfers_attempted:false,
    plan:before, estimate:{feasible:true}, preflight_fingerprint:'fingerprint-A'});
  const values = await Promise.all([first, second]);
  const result = {adapter_calls:calls.length, transmitted_sources:calls.map(x=>x.input.source),
    second_requested_source:after.input.source, second_received_source:values[1].plan.input.source,
    second_received_fingerprint:values[1].preflight_fingerprint,
    caveat:'In-flight coalescing only. Completed requests are reloaded; this is not persistent cache reuse.'};
  fs.writeFileSync(path.join(__dirname,'client-probe.json'), JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify(result));
})().catch(error=>{console.error(error);process.exitCode=1;});
