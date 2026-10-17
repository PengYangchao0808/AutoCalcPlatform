"""Read-only evidence capture for the ACP progress synchronization audit."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)
BASE = "http://127.0.0.1:8765"
JOB = "20260930_110815_003_BatchOptimize"
JOB_BASE = f"/api/v1/jobs/{JOB}"


def read_url(path: str) -> bytes:
    with urlopen(BASE + path, timeout=30) as response:
        return response.read()


def save_json(name: str, data: object) -> None:
    (EVIDENCE / name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


capture = {
    "captured_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
    "job_id": JOB,
    "base_url": BASE,
    "requests": [],
}
for name, path in [
    ("service_status.json", "/api/status"),
    ("job_raw.json", JOB_BASE),
    ("job_summary.json", JOB_BASE + "/summary"),
    ("job_detail.json", JOB_BASE + "/detail"),
    ("stage_tasks.json", JOB_BASE + "/tasks"),
    ("state.json", JOB_BASE + "/files/state.json"),
    ("runtime_logs.json", JOB_BASE + "/logs?lines=100"),
    ("optimization_energy_graph.json", JOB_BASE + "/energy-graph?view_type=optimization"),
    ("root_files.json", JOB_BASE + "/files"),
    ("work_files.json", JOB_BASE + "/files?path=WORK"),
    ("opt_files.json", JOB_BASE + "/files?path=WORK/03_OPT"),
    ("optimization_trajectory.json", JOB_BASE + "/files/WORK/03_OPT/optimization_trajectory.json"),
]:
    try:
        data = json.loads(read_url(path))
        save_json(name, data)
        capture["requests"].append({"path": path, "file": name, "status": 200})
    except HTTPError as error:
        capture["requests"].append({"path": path, "status": error.code})

for name, path in [
    ("ts_opt.inp", JOB_BASE + "/files/WORK/03_OPT/ts_opt.inp"),
    ("events.jsonl", JOB_BASE + "/files/WORK/00_RUNTIME/events.jsonl"),
]:
    data = read_url(path)
    (EVIDENCE / name).write_bytes(data)

output = read_url(JOB_BASE + "/files/WORK/03_OPT/ts_opt.out")
lines = output.decode("utf-8", errors="replace").splitlines()
patterns = [
    "THE OPTIMIZATION HAS CONVERGED",
    "NUMERICAL FREQUENCIES",
    "NUMERICAL HESSIAN",
    "Displacement",
    "ORCA TERMINATED NORMALLY",
    "VIBRATIONAL FREQUENCIES",
    "Program Version",
]
matches = []
selected: set[int] = set()
for index, line in enumerate(lines):
    if any(pattern.lower() in line.lower() for pattern in patterns):
        matches.append({"line": index + 1, "text": line})
        selected.update(range(max(0, index - 2), min(len(lines), index + 5)))
selected.update(range(max(0, len(lines) - 65), len(lines)))
(EVIDENCE / "ts_opt_out_excerpt.txt").write_text(
    "\n".join(f"{index + 1}: {lines[index]}" for index in sorted(selected)) + "\n",
    encoding="utf-8",
)
save_json("ts_opt_out_markers.json", {
    "bytes": len(output),
    "sha256": hashlib.sha256(output).hexdigest(),
    "line_count": len(lines),
    "matches": matches,
    "last_lines": lines[-15:],
})

served = read_url("/")
local = ROOT.parents[2] / "frontend" / "ACP_Workbench_v2.html"
save_json("frontend_comparison.json", {
    "served_sha256": hashlib.sha256(served).hexdigest(),
    "workspace_sha256": hashlib.sha256(local.read_bytes()).hexdigest(),
    "same_after_newline_normalization": served.decode("utf-8").replace("\r\n", "\n") == local.read_text(encoding="utf-8"),
    "served_progress_helper": re.search(
        r"function isProgressIndeterminate\(job\) \{[\s\S]*?\n\}",
        served.decode("utf-8"),
    ).group(0),
})
save_json("capture.json", capture)
print(json.dumps({
    "captured_at": capture["captured_at"],
    "evidence_directory": str(EVIDENCE),
    "output_lines": len(lines),
    "key_markers": [m for m in matches if "Displacement" not in m["text"]][-20:],
    "output_tail": lines[-12:],
}, ensure_ascii=False, indent=2))
