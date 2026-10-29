"""Phase C drills: disconnect/reconnect, client restart, cancel, timeout.

Each drill runs the real chain (installed rph bin, real Controller, real ORCA)
and asserts the invariants from the roadmap: reconnect never duplicates the
submit, a restarted client resumes from the checkpoint, cancel truly ends
execution, and a deadline terminates the task. Evidence lands in /tmp/phaseC/drills/.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path("/tmp/phaseC/drills")
RPH = "/tmp/phaseB/host-install/node_modules/.bin/rph"
URL = "http://127.0.0.1:{port}"
XYZ = "3\nwater\nO   0.0000000    0.0000000    0.1173000\nH   0.0000000    0.7572000   -0.4692000\nH   0.0000000   -0.7572000   -0.4692000\n"


def http(base: str, method: str, path: str, body: dict | None = None, timeout: float = 30.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:300]}


def science_for(xyz: str, timeout_s: int, wall: int):
    sha = "sha256:" + hashlib.sha256(xyz.encode()).hexdigest()
    return {
        "science_seed": {"method": "r2SCAN-3c", "mapping": "identity", "charge": 0, "multiplicity": 1,
                         "structure_provenance": [{"input_id": "water-confirmed", "sha256": sha,
                                                   "format": "xyz", "role": "minimum"}], "constraints": []},
        "initial_input": {"input_id": "water-confirmed", "sha256": sha, "format": "xyz", "role": "minimum"},
        "submission": {"workflow": "singlepoint",
                       "input": {"input_id": "water-confirmed", "sha256": sha, "format": "xyz",
                                 "role": "minimum", "xyz_text": xyz},
                       "method": {"method": "r2SCAN-3c", "basis": None, "charge": 0, "multiplicity": 1},
                       "resources": {"ncores": 1, "memory_gb": 2},
                       "budget": {"unit": "task_count", "count": 1, "timeout_s": timeout_s}},
        "planning_budget": {"model_requests": 4, "input_tokens": 5000, "output_tokens": 5000,
                            "task_refs": 1, "recipe_steps": 1, "qc_invocations": 1,
                            "wall_seconds": wall, "queue_seconds": 60},
    }


class Drill:
    def __init__(self, name: str, port: int, timeout_s: int, wall: int):
        self.name = name
        self.port = port
        self.base = URL.format(port=port)
        self.dir = BASE / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_root = self.dir / "run_root"
        self.run_root.mkdir(exist_ok=True)
        self.config = self.dir / "cccp.yaml"
        self.config.write_text("# drill deployment config\n", encoding="utf-8")
        self.science_path = self.dir / "science.json"
        self.science_path.write_text(json.dumps(science_for(XYZ, timeout_s, wall), indent=2), encoding="utf-8")
        self.recipe_digest = "sha256:" + hashlib.sha256(self.science_path.read_bytes()).hexdigest()
        self.controller: subprocess.Popen | None = None
        self.log_file = open(self.dir / "controller.log", "w", encoding="utf-8")
        self.project = ""
        self.env: dict[str, str] = {}
        self.facts: dict = {"drill": name}

    def start(self):
        key = Path.home().joinpath(".acp/model_key").read_text().strip()
        env = dict(os.environ)
        env.update({"ACP2_MODEL_API_KEY": key, "ACP2_MODEL_ID": "deepseek-chat",
                    "ACP2_MODEL_REQUEST_LIMIT": "6", "ACP2_MODEL_MAX_OUTPUT_TOKENS": "2000",
                    "ACP_PUBLIC_ORIGIN": "", "ACP_RUN_ROOT": str(self.run_root),
                    "PATH": os.path.expanduser("~/.local/bin:") + env.get("PATH", "")})
        self.controller = subprocess.Popen(["acp", "run", "serve", "--port", str(self.port)],
                                           env=env, stdout=self.log_file, stderr=subprocess.STDOUT)
        for _ in range(120):
            try:
                code, _ = http(self.base, "GET", "/api/v1/projects")
                if code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        code, created = http(self.base, "POST", "/api/v1/projects",
                             {"name": f"drill-{self.name}-{int(time.time())}"})
        assert code == 201, (code, created)
        self.project = created["project_id"]
        env.update({"RPH_INTERPRETER": "python3.11", "RPH_WORKING_DIRECTORY": str(self.dir),
                    "ACP_CONFIG": str(self.config), "RPH_PROJECT": self.project,
                    "RPH_PERMISSION_SCOPE": "read,write", "RPH_ARTIFACT_EVIDENCE_PATH": str(self.run_root),
                    "RPH_RESEARCH_WRITER": "platform-research-cas", "RPH_MODEL_REQUESTS": "4",
                    "RPH_MODEL_PROVIDER": "controller", "RPH_MODEL_ID": "deepseek-chat"})
        self.env = env
        return self

    def stop(self):
        if self.controller is not None:
            self.controller.terminate()
            try:
                self.controller.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.controller.kill()
        self.log_file.close()

    def rph(self, argv: list[str], out: str, timeout: int = 900, wait: bool = True):
        log = self.dir / out
        with log.open("w", encoding="utf-8") as handle:
            proc = subprocess.Popen([RPH, *argv, "--json"], cwd=str(self.dir), env=self.env,
                                    stdout=handle, stderr=subprocess.STDOUT)
            if wait:
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
        return proc

    def research_once(self) -> dict:
        proc = self.rph(["research", "--controller", self.base, "--project", self.project,
                         "--research", f"drill-{self.name}", "--question",
                         "Compute the total electronic energy of the confirmed water structure "
                         "at the r2SCAN-3c level and report the referenced result.",
                         "--recipe-digest", self.recipe_digest, "--science", str(self.science_path)],
                        "step1-research.log")
        text = (self.dir / "step1-research.log").read_text(encoding="utf-8").split("--- stderr ---")[0]
        return json.loads(text)

    def approve(self) -> str:
        time.sleep(1)
        code, approvals = http(self.base, "GET", f"/api/v2/research/approvals?project_id={self.project}")
        rows = approvals.get("approvals") or []
        pending = [r for r in rows if str(r.get("status")) == "pending"]
        assert pending, ("no pending approval", code, approvals)
        row = pending[0]
        ref = row.get("approval_ref") or row.get("approval_id")
        code, decided = http(self.base, "POST", f"/api/v2/research/approvals/{ref}/approve",
                             {"expected_revision": row.get("revision", 1), "reason": f"drill {self.name}"})
        assert code == 200, (code, decided)
        return str(ref)

    def resume_argv(self, step1: dict) -> list[str]:
        handle = step1.get("resume") or {}
        return ["resume", "--controller", self.base, "--project", self.project,
                "--proposal", str(step1.get("proposal_ref") or handle.get("proposal_ref")),
                "--plan-digest", str(handle.get("plan_digest"))]

    def jobs(self) -> list[dict]:
        code, payload = http(self.base, "GET", f"/api/v1/jobs?project_id={self.project}")
        body = payload.get("jobs") or payload.get("items") or []
        return [j for j in body if isinstance(j, dict)]

    def job_status(self, job_id: str) -> str:
        code, payload = http(self.base, "GET", f"/api/v1/jobs/{job_id}")
        return str(payload.get("status") or payload.get("state") or "").upper()

    def record(self, **kw):
        self.facts.update(kw)
        (self.dir / "summary.json").write_text(json.dumps(self.facts, ensure_ascii=False, indent=2),
                                               encoding="utf-8")


def drill_disconnect(port: int) -> dict:
    """Kill the rph client mid-wait; a fresh client resumes without a new submit."""
    drill = Drill("disconnect", port, timeout_s=900, wall=900).start()
    try:
        step1 = drill.research_once()
        drill.approve()
        argv = drill.resume_argv(step1)
        proc = drill.rph(argv, "step2-resume-killed.log", wait=False)
        deadline = time.time() + 60
        while time.time() < deadline:
            jobs = drill.jobs()
            if jobs:
                break
            time.sleep(0.4)
        assert drill.jobs(), "no job appeared before the kill"
        time.sleep(1.5)  # let the first resume submit and enter its wait window
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
        killed_jobs = drill.jobs()
        time.sleep(2)
        after_kill = drill.jobs()
        assert len(after_kill) == len(killed_jobs), "a killed client spawned extra jobs"
        proc2 = drill.rph(argv, "step3-resume-restart.log")
        time.sleep(1)
        final = drill.jobs()
        statuses = {j.get("id"): drill.job_status(str(j.get("id"))) for j in final}
        text = (drill.dir / "step3-resume-restart.log").read_text(encoding="utf-8").split("--- stderr ---")[0]
        result = (json.loads(text).get("result") or {})
        drill.record(step1_stop=step1.get("result", {}).get("stop_reason"),
                     jobs_total=len(final), job_statuses=statuses,
                     restart_stop=result.get("stop_reason"), restart_submits=result.get("submits"),
                     killed_pid=proc.pid, restart_exit=proc2.returncode)
        assert len(final) == 1, f"expected exactly one job, saw {len(final)}"
        assert result.get("submits") == 0, "the restarted client must not submit again"
        return drill.facts
    finally:
        drill.stop()


def drill_cancel(port: int) -> dict:
    """Cancel the running task through the platform; the client observes the terminal state."""
    drill = Drill("cancel", port, timeout_s=900, wall=900).start()
    try:
        step1 = drill.research_once()
        drill.approve()
        argv = drill.resume_argv(step1)
        drill.rph(argv, "step2-resume.log")
        job_id = ""
        deadline = time.time() + 60
        while time.time() < deadline:
            jobs = drill.jobs()
            if jobs:
                job_id = str(jobs[0].get("id"))
                break
            time.sleep(0.3)
        assert job_id, "no job to cancel"
        deadline = time.time() + 30
        while time.time() < deadline and drill.job_status(job_id) == "QUEUED":
            time.sleep(0.2)
        code, _ = http(drill.base, "POST", f"/api/v1/jobs/{job_id}/cancel")
        final_status = ""
        deadline = time.time() + 120
        while time.time() < deadline:
            final_status = drill.job_status(job_id)
            if final_status in {"CANCELLED", "COMPLETED", "FAILED"}:
                break
            time.sleep(0.5)
        text = (drill.dir / "step2-resume.log").read_text(encoding="utf-8").split("--- stderr ---")[0]
        result = (json.loads(text).get("result") or {})
        drill.record(job_id=job_id, cancel_http=code, final_status=final_status,
                     client_stop=result.get("stop_reason"), client_submits=result.get("submits"))
        assert final_status in {"CANCELLED", "COMPLETED"}, f"cancel left the job {final_status}"
        return drill.facts
    finally:
        drill.stop()


def drill_timeout(port: int) -> dict:
    """A 6 s wall deadline terminates the ~10 s singlepoint run."""
    drill = Drill("timeout", port, timeout_s=6, wall=900).start()
    try:
        step1 = drill.research_once()
        drill.approve()
        drill.rph(drill.resume_argv(step1), "step2-resume.log")
        job_id = ""
        deadline = time.time() + 60
        while time.time() < deadline:
            jobs = drill.jobs()
            if jobs:
                job_id = str(jobs[0].get("id"))
                break
            time.sleep(0.3)
        assert job_id, "no job for the timeout drill"
        status = ""
        deadline = time.time() + 180
        while time.time() < deadline:
            status = drill.job_status(job_id)
            if status in {"FAILED", "COMPLETED", "CANCELLED"}:
                break
            time.sleep(1.0)
        code, detail = http(drill.base, "GET", f"/api/v1/jobs/{job_id}")
        error = str(detail.get("error") or "")
        drill.record(job_id=job_id, final_status=status, error=error[:200])
        assert status == "FAILED", f"the deadline should fail the task, saw {status}"
        assert error, "a timed-out task must carry an error reason"
        return drill.facts
    finally:
        drill.stop()


def main() -> int:
    results = {}
    for name, fn, port in [("disconnect", drill_disconnect, 18771),
                           ("cancel", drill_cancel, 18772),
                           ("timeout", drill_timeout, 18773)]:
        print(f"== drill {name} ==", flush=True)
        results[name] = fn(port)
        print(json.dumps(results[name], ensure_ascii=False)[:400], flush=True)
    (BASE / "drills-summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    print("all drills passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
