"""Independent R8 review probes. Temporary data only; no network or QC."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

from acp.results.remote_structure_cache import RemoteStructureCache
from acp.services_data_bootstrap import _ReadRootAdapter
from acp.storage.manifest import ResultManifest
from acp_services.data.research_resources import (
    PROPOSAL_SCHEMA,
    ResearchResourceConflictError,
    ResearchResourceRequest,
    ResearchResourceStore,
)
from acp_services.entrypoints.status_projection import attempt_release_facts

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


def proposed_key(project: str, kind: str, document_id: str) -> str:
    """Literal R8 algorithm, not an implementation claimed to be delivered."""
    raw = f"{project}:{kind}:{document_id}"
    if re.match(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", raw) and len(raw) <= 128:
        return raw
    return "rr_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


with tempfile.TemporaryDirectory(prefix="acp2-r8-review-") as directory:
    tmp = Path(directory)
    cache = RemoteStructureCache(tmp)
    root = cache.job_root("job-review")
    manifest = ResultManifest(task_id="job-review", workflow="singlepoint", status="completed")
    manifest.add_product("attempt-one-product", "Old result", "old.xyz")
    manifest_path = manifest.write(root / "RESULT")
    cache._write_attempt_sidecar(manifest_path, 1)
    runtime = root / "WORK/00_RUNTIME/frozen"
    runtime.mkdir(parents=True)
    (runtime / "envelope.json").write_text(json.dumps({"attempt":1,"release_digest":"a"*64}))
    record = SimpleNamespace(id="job-review", attempt=2, work_dir=str(tmp/"local-task"),
        result={"node":"remote-node", "remote_dir":"/remote/job"},
        spec=SimpleNamespace(workflow="singlepoint"))
    resolved = _ReadRootAdapter(job_lookup=lambda _:record, cache=cache).resolve("job-review",2)
    loaded = ResultManifest.read(Path(resolved.path)/"RESULT")
    remote = {"root":dataclasses.asdict(resolved),
        "r8_historical_only_guard_rejects":bool(resolved.historical and resolved.is_remote and not resolved.catalog_ready),
        "direct_manifest_read_ids":[p.id for p in loaded.products],
        "direct_release_facts":attempt_release_facts(resolved.path),
        "stored_manifest_attempt":1, "requested_attempt":2,
        "note":"The route is not implemented; this proves the planned guard does not reject this actual resolver output."}
    fixture = json.loads((REPO/'docs/reference/next-change/acp2-contracts/experience/fixtures/proposal.valid.json').read_text())
    pairs = [("a", "prop-x:proposal:prop-y"), ("a:proposal:prop-x", "prop-y")]
    keys = [proposed_key(p,"proposal",d) for p,d in pairs]
    store = ResearchResourceStore(tmp/'resources.db')
    collision = None
    try:
        for project, doc_id in pairs:
            doc = {**fixture,"project_id":project,"proposal_id":doc_id}
            store.create(ResearchResourceRequest(resource_id=proposed_key(project,"proposal",doc_id),
                schema=PROPOSAL_SCHEMA,document=doc,principal_id="review",project_id=project))
    except ResearchResourceConflictError as error:
        collision = type(error).__name__ + ': ' + str(error)
    finally:
        store.close()
    result = {"remote_current_attempt":remote,"r8_key_delimiter_collision":{
        "identities":pairs,"keys":keys,"same_key":keys[0]==keys[1],"store_error":collision,
        "scope":"Allowed by the Data contract. Normal ProjectStore.create_project generates UUIDs; this is not a claim of a current UI exploit."}}
    (HERE/'source-probes.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
