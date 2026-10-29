"""Phase C1: one real closed loop — NL → model plan → approval → real ORCA singlepoint
→ native evidence → referenced decision, through the INSTALLED rph product entry.

Lead-run acceptance driver (machine evidence producer). No fabrication: every
fact recorded here comes from an actual HTTP call, process run or artifact read.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path("/tmp/phaseC2/c2-chain")
RUN_ROOT = BASE / "run_root"
RPH = "/tmp/phaseB/host-install/node_modules/.bin/rph"
PORT = 18768
URL = f"http://127.0.0.1:{PORT}"
RESEARCH_ID = "c2-water-xtbopt"
XYZ = "3\nwater\nO   0.0000000    0.0000000    0.1173000\nH   0.0000000    0.7572000   -0.4692000\nH   0.0000000   -0.7572000   -0.4692000\n"

def http(method: str, path: str, body: dict | None = None, timeout: float = 60.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:500]}

def run(argv: list[str], out_name: str, timeout: int = 900, env: dict | None = None):
    log = BASE / out_name
    started = time.perf_counter()
    proc = subprocess.run(argv, cwd=str(BASE), capture_output=True, text=True,
                          timeout=timeout, env=env)
    log.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    return proc, round(time.perf_counter() - started, 1)

def main() -> int:
    BASE.mkdir(parents=True, exist_ok=True)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    key = Path.home().joinpath(".acp/model_key").read_text().strip()
    env = dict(os.environ)
    env.update({
        "ACP2_MODEL_API_KEY": key,
        "ACP2_MODEL_ID": "deepseek-chat",
        "ACP2_MODEL_REQUEST_LIMIT": "6",
        "ACP2_MODEL_MAX_OUTPUT_TOKENS": "2000",
        "ACP_PUBLIC_ORIGIN": "",
        "ACP_RUN_ROOT": str(RUN_ROOT),
        "RPH_INTERPRETER": "python3.11",
        "PATH": os.path.expanduser("~/.local/bin:") + env.get("PATH", ""),
    })

    sha = "sha256:" + hashlib.sha256(XYZ.encode()).hexdigest()
    science = {
        "science_seed": {
            "method": "GFN2-xTB", "mapping": "identity", "charge": 0, "multiplicity": 1,
            "structure_provenance": [{"input_id": "water-confirmed", "sha256": sha,
                                      "format": "xyz", "role": "minimum"}],
            "constraints": [],
        },
        "initial_input": {"input_id": "water-confirmed", "sha256": sha, "format": "xyz", "role": "minimum"},
        "submission": {
            "workflow": "xtb_optimize",
            "input": {"input_id": "water-confirmed", "sha256": sha, "format": "xyz",
                      "role": "minimum", "xyz_text": XYZ},
            "method": {"method": "GFN2-xTB", "basis": None, "charge": 0, "multiplicity": 1},
            "resources": {"ncores": 1, "memory_gb": 2},
            "budget": {"unit": "task_count", "count": 1, "timeout_s": 900},
        },
        "planning_budget": {"model_requests": 4, "input_tokens": 5000, "output_tokens": 5000,
                            "task_refs": 1, "recipe_steps": 1, "qc_invocations": 1,
                            "wall_seconds": 900, "queue_seconds": 60},
    }
    science_path = BASE / "science.json"
    science_path.write_text(json.dumps(science, indent=2), encoding="utf-8")
    recipe_digest = "sha256:" + hashlib.sha256(science_path.read_bytes()).hexdigest()

    # -- controller ------------------------------------------------------------
    controller_log = open(BASE / "controller.log", "w", encoding="utf-8")
    controller = subprocess.Popen(["acp", "run", "serve", "--port", str(PORT)],
                                  env=env, stdout=controller_log, stderr=subprocess.STDOUT)
    try:
        for _ in range(120):
            if controller.poll() is not None:
                print("controller died early; see controller.log", file=sys.stderr)
                return 2
            try:
                code, _ = http("GET", "/api/v1/projects")
                if code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            print("controller did not become ready", file=sys.stderr)
            return 2

        code, created = http("POST", "/api/v1/projects", {"name": "ACP2 dev batch phase C1 " + str(int(time.time()))})
        assert code == 201, (code, created)
        project = created["project_id"]
        print("project:", project)
        config_path = BASE / "cccp.yaml"
        config_path.write_text("# minimal deployment config; built-in defaults are authoritative\n",
                               encoding="utf-8")
        env.update({
            "RPH_WORKING_DIRECTORY": str(BASE),
            "ACP_CONFIG": str(config_path),
            "RPH_PROJECT": project,
            "RPH_PERMISSION_SCOPE": "read,write",
            "RPH_ARTIFACT_EVIDENCE_PATH": str(RUN_ROOT),
            "RPH_RESEARCH_WRITER": "platform-research-cas",
            "RPH_MODEL_REQUESTS": "4",
            "RPH_MODEL_PROVIDER": "controller",
            "RPH_MODEL_ID": "deepseek-chat",
        })

        # -- step 1: rph research (model plan + proposal + approval request) ----
        research_argv = [RPH, "research",
                         "--controller", URL, "--project", project, "--research", RESEARCH_ID,
                         "--question", "Optimize the confirmed water structure at the GFN2-xTB level "
                                       "and report the referenced optimized energy.",
                         "--recipe-digest", recipe_digest,
                         "--science", str(science_path), "--json"]
        proc1, dur1 = run(research_argv, "step1-research.log", env=env)
        print(f"step1 research exit={proc1.returncode} in {dur1}s")
        step1: dict = {}
        try:
            step1 = json.loads(proc1.stdout)
        except json.JSONDecodeError:
            step1 = {"parse_error": proc1.stdout[-800:]}

        # -- step 2: operator approves the pending approval ---------------------
        time.sleep(1.0)
        code, approvals = http("GET", f"/api/v2/research/approvals?project_id={project}")
        rows = approvals.get("approvals") or approvals.get("items") or []
        pending = [r for r in rows if str(r.get("status")) == "pending"]
        assert pending, ("no pending approval", code, approvals)
        row = pending[0]
        approval_ref = row["approval_ref"] if "approval_ref" in row else row["approval_id"]
        expected_revision = row.get("revision", row.get("expected_revision", 1))
        code, decided = http("POST", f"/api/v2/research/approvals/{approval_ref}/approve",
                             {"expected_revision": expected_revision,
                              "reason": "Phase C1 operator approval: plan matches the confirmed "
                                        "r2SCAN-3c water singlepoint seed."})
        print("approval:", approval_ref, "->", code, decided.get("status"))
        assert code == 200, (code, decided)

        # -- step 3: resume drives submit -> ORCA -> evidence -> decision --------
        proposal_ref = step1.get("proposal_ref") or step1.get("resume", {}).get("proposal_ref")
        plan_digest = step1.get("resume", {}).get("plan_digest")
        assert proposal_ref and plan_digest, ("resume handle missing", step1)
        print("proposal:", proposal_ref, "plan_digest:", str(plan_digest)[:20])

        resume_argv = [RPH, "resume", "--controller", URL, "--project", project,
                       "--proposal", str(proposal_ref), "--plan-digest", str(plan_digest), "--json"]
        step3: dict = {}
        dur3 = 0.0
        for attempt in range(10):
            proc3, dur = run(resume_argv, f"step3-resume-{attempt}.log", timeout=1200, env=env)
            dur3 += dur
            try:
                step3 = json.loads(proc3.stdout)
            except json.JSONDecodeError:
                step3 = {"parse_error": proc3.stdout[-800:]}
                break
            result = step3.get("result") or {}
            print(f"  resume[{attempt}] exit={proc3.returncode} status={result.get('status')} "
                  f"stop={result.get('stop_reason')} submits={result.get('submits')}")
            if result.get("status") != "waiting":
                break
            time.sleep(12)
        print(f"step3 resume rounds done in {dur3:.1f}s")

        # -- facts ---------------------------------------------------------------
        facts: dict = {"project_id": project, "approval_ref": approval_ref,
                       "proposal_ref": proposal_ref, "plan_digest": plan_digest,
                       "recipe_digest": recipe_digest,
                       "step1": step1, "step3": step3,
                       "durations": {"research_s": dur1, "resume_s": dur3}}
        code, jobs = http("GET", f"/api/v1/jobs?project_id={project}")
        facts["jobs"] = jobs if code == 200 else {"http": code}
        code, usage = http("GET", f"/api/v2/model/usage?project_id={project}")
        facts["model_usage"] = usage if code == 200 else {"http": code}
        code, record = http("GET", f"/api/v2/research/records/{RESEARCH_ID}?project_id={project}")
        facts["research_record"] = record if code == 200 else {"http": code}
        (BASE / "summary.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
        print("summary written:", BASE / "summary.json")
        return 0
    finally:
        controller.terminate()
        try:
            controller.wait(timeout=15)
        except subprocess.TimeoutExpired:
            controller.kill()
        controller_log.close()

if __name__ == "__main__":
    sys.exit(main())
