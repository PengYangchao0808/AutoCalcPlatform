"""Capture a second read-only snapshot without replacing initial evidence."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parent
base = "http://127.0.0.1:8765/api/v1/jobs/20260930_110815_003_BatchOptimize"


def read(path):
    with urlopen(base + path, timeout=30) as response:
        return response.read()


data = {"captured_at": datetime.now(timezone(timedelta(hours=8))).isoformat()}
for key, path in [
    ("state", "/files/state.json"), ("summary", "/summary"),
    ("raw", ""), ("work_files", "/files?path=WORK"),
]:
    data[key] = json.loads(read(path))
lines = read("/files/WORK/03_OPT/ts_opt.out").decode("utf-8", errors="replace").splitlines()
markers = []
for index, line in enumerate(lines):
    if re.search(r"HFTyp|Hartree.Fock type|GuessMix|ORCA TERMINATED NORMALLY|ORCA NUMERICAL FREQUENCIES|THE OPTIMIZATION HAS CONVERGED", line, re.I):
        markers.append({"line": index + 1, "text": line})
data["output_markers"] = markers
data["output_tail"] = lines[-18:]
(root / "evidence/live_followup.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps({
    "captured_at": data["captured_at"],
    "current_stage": data["state"]["current_stage"],
    "state_updated_at": data["state"]["updated_at"],
    "summary_updated_at": data["summary"]["updated_at"],
    "db_updated_at": data["raw"]["updated_at"],
    "output_markers": markers[:6] + markers[-3:],
    "output_tail": lines[-8:],
}, ensure_ascii=False, indent=2))
