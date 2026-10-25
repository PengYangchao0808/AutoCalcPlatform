#!/usr/bin/env python3.11
"""B14b capability driver: one real MCP submission per (capability, backend).

This driver never synthesizes a science result.  For ``--target local`` it
starts the real stdio MCP adapter from the frozen candidate, submits exactly
one ``acp_submit`` tool call for the requested workflow, waits for the
Controller's terminal snapshot, and then validates the native ORCA output,
task_result, manifest and execution receipt.  For ``--target lsf`` it checks
the real LSF prerequisites first and records ``NOT_VERIFIED`` without
submitting anything when they are absent.

Exit codes: 0 PASS, 1 FAIL, 2 NOT_VERIFIED, 3 usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2
EXIT_USAGE = 3

LEVEL_KEY = {
    "singlepoint": "single_point",
    "optimize": "optimize",
    "frequency": "frequency",
}
NATIVE_FILES = (
    "RESULT/task_result.json",
    "RESULT/result_manifest.json",
    "RESULT/scientific_result.json",
    "RESULT/energy/singlepoint_energy.json",
    "RESULT/structures/singlepoint_final.xyz",
    "execution_receipt.json",
    "job.json",
    "task.json",
    "normal_modes.json",
    "optimization_trajectory.json",
    "optimized.xyz",
)
TERMINAL = {"completed", "failed", "cancelled"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def http_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class McpProbe:
    """Minimal real newline-delimited JSON-RPC client for the stdio adapter."""

    def __init__(self, launch: dict[str, Any], timeout: float) -> None:
        self.timeout = timeout
        self.queue: queue.Queue[bytes | None] = queue.Queue()
        self.stderr: list[str] = []
        merged_env = {**os.environ, **(launch.get("env") or {})}
        self.proc = subprocess.Popen(
            [launch["command"], *launch["args"]],
            cwd=launch["cwd"],
            env=merged_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                self.queue.put(None)
                return
            self.queue.put(line)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr.append(line.decode("utf-8", errors="replace"))

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no MCP response for {method}")
            try:
                raw = self.queue.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"no MCP response for {method}") from None
            if raw is None:
                raise RuntimeError(f"MCP adapter closed stdout during {method}")
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("not a JSON-RPC 2.0 object")
            if "id" not in message and "method" in message:
                continue
            if message.get("id") != request_id:
                raise ValueError("unexpected JSON-RPC response id")
            if "error" in message:
                raise RuntimeError(f"{method}: {message['error']}")
            return message["result"]

    def close(self) -> int:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                return self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return self.proc.wait(timeout=5)


class OrcaSampler:
    def __init__(self, executable: str, interval: float = 0.01) -> None:
        self.executable = str(Path(executable).resolve())
        self.interval = interval
        self.stop_event = threading.Event()
        self.pids: set[int] = set()
        self.max_concurrent = 0
        self.samples = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def _scan(self) -> set[int]:
        pids: set[int] = set()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            if not raw:
                continue
            argv0 = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
            if not argv0:
                continue
            try:
                resolved = str(Path(argv0).resolve())
            except OSError:
                resolved = argv0
            if resolved == self.executable:
                pids.add(int(entry.name))
        return pids

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                found = self._scan()
            except OSError:
                found = set()
            self.samples += 1
            self.pids.update(found)
            self.max_concurrent = max(self.max_concurrent, len(found))
            time.sleep(self.interval)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def task_count(controller: str, timeout: float) -> int:
    payload = http_json(controller.rstrip("/") + "/api/v1/jobs?limit=1000", timeout)
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    return len(jobs) if isinstance(jobs, list) else 0


def submit_task_id(result: dict[str, Any]) -> str:
    data = result.get("structuredContent", {}).get("data", {})
    if not isinstance(data, dict):
        return ""
    task_ref = data.get("task_ref")
    if isinstance(task_ref, dict):
        value = task_ref.get("task_id") or task_ref.get("job_id")
        if isinstance(value, str) and value:
            return value
    reference = data.get("reference")
    if isinstance(reference, dict):
        value = reference.get("id")
        if isinstance(value, str) and value:
            return value
    return ""


def job_started_events(work_dir: Path) -> int:
    events = work_dir / "WORK" / "00_RUNTIME" / "events.jsonl"
    if not events.is_file():
        return 0
    count = 0
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("type") == "job.started":
            count += 1
    return count


def orca_output(work_dir: Path) -> tuple[Path | None, str]:
    for path in sorted(work_dir.rglob("*.out")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "ORCA TERMINATED NORMALLY" in text:
            return path, text
    return None, ""


def final_energy(text: str) -> float | None:
    matches = re.findall(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", text)
    return float(matches[-1]) if matches else None


def copy_native(work_dir: Path, out_dir: Path) -> list[dict[str, Any]]:
    native = out_dir / "native"
    native.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    candidates = list(NATIVE_FILES)
    candidates.extend(str(path.relative_to(work_dir)) for path in sorted(work_dir.rglob("*.out")))
    candidates.extend(str(path.relative_to(work_dir)) for path in sorted(work_dir.rglob("*.inp")))
    for name in ("step_result.json", "scientific_result.json", "result_manifest.json",
                 "optimization_trajectory.json", "normal_modes.json", "optimize.xyz",
                 "optimize_trj.xyz", "optimize.opt"):
        candidates.extend(
            str(path.relative_to(work_dir)) for path in sorted(work_dir.rglob(name))
        )
    seen: set[str] = set()
    for relative in candidates:
        if relative in seen:
            continue
        seen.add(relative)
        source = work_dir / relative
        if not source.is_file():
            continue
        target = native / relative.replace("/", "__")
        shutil.copy2(source, target)
        copied.append(
            {
                "path": str(target.relative_to(out_dir)),
                "size_bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
    return copied


def build_submit(workflow: str, xyz_text: str, method: str, basis: str, key: str) -> dict[str, Any]:
    return {
        "workflow": workflow,
        "input": {
            "source_type": "xyz_text",
            "source": xyz_text,
            "charge": 0,
            "multiplicity": 1,
        },
        "method": {
            "levels": {
                LEVEL_KEY[workflow]: {
                    "engine": "orca",
                    "functional": method,
                    "basis": basis,
                }
            }
        },
        "resources": {"cores": 1, "nproc": 1, "mem": "1024MB"},
        "plan_digest": "sha256:" + "0" * 64,
        "idempotency_key": key,
    }


def lsf_prerequisites() -> list[str]:
    missing = [tool for tool in ("bsub", "bjobs", "bhosts", "bqueues") if shutil.which(tool) is None]
    for env_name in ("LSF_ENVDIR", "ACP_LSF_NODE"):
        if not os.environ.get(env_name):
            missing.append(env_name)
    return missing


def run_lsf(args: argparse.Namespace) -> int:
    missing = lsf_prerequisites()
    record = {
        "schema": "acp2.b14b.capability-run.v1",
        "capability": args.workflow,
        "backend": "lsf",
        "result": "NOT_VERIFIED",
        "fixture_id": args.fixture_id,
        "fixture_path": args.input,
        "exact_command": " ".join(args.replay),
        "candidate_sha": args.candidate_sha,
        "missing_prerequisites": missing,
        "reason": "lsf_prerequisite_missing:" + ",".join(missing),
        "submitted": False,
        "remote_job_attempted": False,
        "recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (args.out / "run.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return EXIT_NOT_VERIFIED


def run_local(args: argparse.Namespace) -> int:
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    controller = args.controller.rstrip("/")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {}
    probe: McpProbe | None = None
    sampler: OrcaSampler | None = None
    failure: str | None = None
    try:
        launch = json.loads(Path(args.launch).read_text(encoding="utf-8"))
        if not isinstance(launch, dict):
            raise ValueError("launch descriptor must be a JSON object")
        xyz_text = Path(args.input).read_text(encoding="utf-8")
        launch_env = launch.get("env") if isinstance(launch.get("env"), dict) else {}
        orca_path = str(launch_env.get("CONFSEARCH_ORCA_PATH") or args.orca)
        if str(Path(orca_path).resolve()) == "/usr/bin/orca":
            raise ValueError("/usr/bin/orca is the GNOME screen reader, not QC ORCA")
        sampler = OrcaSampler(orca_path)
        before = task_count(controller, args.timeout)
        if args.reuse_task:
            task_id = args.reuse_task
            submitted = {"reused_existing_task": True}
            after = before
            prior = read_json(Path(args.prior_run)) if args.prior_run else {}
            reused_orca = {
                "pids": prior.get("orca_pids") or [],
                "max_concurrent": prior.get("orca_max_concurrent"),
                "samples": prior.get("orca_samples") or None,
                "source": args.prior_run,
            }
        else:
            sampler.start()
            probe = McpProbe(launch, args.timeout)
            probe.request(
                1,
                "initialize",
                {
                    "protocolVersion": launch.get("protocol_version", "2025-06-18"),
                    "capabilities": {},
                    "clientInfo": {"name": "acp2-b14b-driver", "version": "1"},
                },
            )
            probe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            probe.request(2, "tools/list", {})
            key = "b14b-" + uuid.uuid4().hex[:24]
            submitted = probe.request(
                3,
                "tools/call",
                {
                    "name": "acp_submit",
                    "arguments": build_submit(args.workflow, xyz_text, args.method, args.basis, key),
                },
            )
            task_id = submit_task_id(submitted)
            if not task_id:
                raise RuntimeError("acp_submit returned no task id")
            reused_orca = None

        deadline = time.monotonic() + args.timeout
        job: dict[str, Any] = {}
        while time.monotonic() < deadline:
            job = http_json(controller + f"/api/v1/jobs/{urllib.parse.quote(task_id, safe='')}", min(args.timeout, 60))
            if isinstance(job, dict) and str(job.get("status") or "").lower() in TERMINAL:
                break
            time.sleep(0.2)
        sampler.stop()
        if not args.reuse_task:
            after = task_count(controller, args.timeout)

        status = str(job.get("status") or "").lower() if isinstance(job, dict) else ""
        work_dir = Path(str(job.get("work_dir") or "")) if isinstance(job, dict) else Path()
        attempt = int(job.get("attempt") or 0) if isinstance(job, dict) else 0
        if not work_dir.is_dir():
            raise RuntimeError(f"job work_dir is not readable: {work_dir}")

        task_result = read_json(work_dir / "RESULT" / "task_result.json")
        manifest = read_json(work_dir / "RESULT" / "result_manifest.json")
        receipt = read_json(work_dir / "execution_receipt.json")
        job_json = read_json(work_dir / "job.json")
        envelope = read_json(work_dir / "WORK" / "00_RUNTIME" / "frozen" / "envelope.json")
        out_path, orca_text = orca_output(work_dir)
        orca_energy = final_energy(orca_text)
        result = task_result.get("result") if isinstance(task_result.get("result"), dict) else {}
        # Legacy simple workflows (optimize/frequency) publish a step_result
        # under WORK/<stage>/ instead of the envelope task_result/receipt.
        step_result = read_json(next(iter(sorted(work_dir.glob("WORK/*/step_result.json"))), work_dir / "__missing__"))
        scientific = read_json(next(iter(sorted(work_dir.glob("WORK/*/scientific_result.json"))), work_dir / "__missing__"))
        scientific_summary = scientific.get("summary") if isinstance(scientific.get("summary"), dict) else {}
        energy = result.get("energy_hartree", step_result.get("energy", scientific_summary.get("energy_hartree")))
        frequencies = result.get("frequencies", step_result.get("frequencies"))
        symbols = result.get("symbols", step_result.get("symbols"))
        coordinates = result.get("coordinates", step_result.get("coords"))
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        if not payload:
            payload = step_result.get("metadata") if isinstance(step_result.get("metadata"), dict) else {}

        native = copy_native(work_dir, out)
        release_id = str(receipt.get("release_id") or "")
        result_digest = str(receipt.get("result_digest") or "")
        plan_identity = str(receipt.get("plan_identity") or "")
        chain = "envelope-worker" if receipt else "legacy-cli"
        release = {
            "release_id": release_id or None,
            "release_digest": receipt.get("release_digest"),
            "release_status": "RECORDED" if release_id else "NOT_EMITTED_ON_LEGACY_CHAIN",
        }

        if receipt:
            complete_ok = (
                task_result.get("complete") is True
                and task_result.get("qualification") == "complete"
                and task_result.get("status") == "completed"
                and task_result.get("receipt_state") == "completed"
                and task_result.get("missing_required_facts") == []
                and task_result.get("exit_code") == 0
            )
            attempt_ok = (
                attempt > 0
                and attempt == int(receipt.get("attempt") or -1) == int(envelope.get("attempt") or -1)
                and str(job_json.get("attempt") or "") == str(attempt)
                and bool(release_id)
                and receipt.get("release_id") == envelope.get("release_id")
            )
        else:
            complete_ok = (
                step_result.get("status") == "completed"
                and step_result.get("errors") == []
                and manifest.get("status") == "completed"
                and int(job.get("exit_code") or 0) == 0
            )
            attempt_ok = attempt > 0 and str(job_json.get("attempt") or "") == str(attempt)
        energy_ok = (
            isinstance(energy, (int, float))
            and orca_energy is not None
            and abs(float(orca_energy) - float(energy)) <= 1e-9
        )
        geometry_ok = (
            symbols == ["H", "H"]
            and isinstance(coordinates, list)
            and len(coordinates) == 2
            and all(isinstance(row, list) and len(row) == 3 for row in coordinates)
        )
        dispatch_ok = job_started_events(work_dir) == 1
        submit_ok = (
            True
            if args.reuse_task
            else before + 1 == after and isinstance(submitted, dict) and not submitted.get("isError")
        )
        orca_ok = out_path is not None
        observed_max = reused_orca["max_concurrent"] if reused_orca else sampler.max_concurrent
        observed_pids = reused_orca["pids"] if reused_orca else sorted(sampler.pids)
        observed_samples = reused_orca["samples"] if reused_orca else sampler.samples

        if args.workflow == "singlepoint":
            science_ok = isinstance(frequencies, list) and len(frequencies) == 0
            science_detail = f"frequencies={frequencies} (singlepoint must not emit modes)"
        elif args.workflow == "optimize":
            converged = bool(re.search(r"OPTIMIZATION HAS CONVERGED|HURRAY", orca_text))
            payload_ok = str(payload.get("optimization_status") or "") == "converged"
            science_ok = converged and payload_ok and geometry_ok and energy_ok
            science_detail = (
                f"converged={converged} payload.optimization_status={payload.get('optimization_status')} "
                f"geometry={symbols} energy={energy}"
            )
        else:
            modes = frequencies if isinstance(frequencies, list) else []
            negatives = [value for value in modes if float(value) < 0.0]
            n_imaginary = payload.get("n_imaginary")
            science_ok = (
                len(modes) == 1
                and not negatives
                and n_imaginary == 0
                and geometry_ok
                and energy_ok
            )
            science_detail = (
                f"frequencies={modes} n_imaginary={n_imaginary} geometry={symbols} energy={energy}"
            )

        assertions = [
            {"name": "one_mcp_submission_one_task", "status": "PASS" if submit_ok else "FAIL",
             "detail": (f"reused existing task {task_id}; no new submission; task_count={before}"
                        if args.reuse_task else f"task_count_before={before} after={after}")},
            {"name": "job_completed", "status": "PASS" if status == "completed" else "FAIL",
             "detail": f"status={status} attempt={attempt}"},
            {"name": "real_orca_process_once",
             "status": "PASS" if len(observed_pids) == 1 and observed_max == 1 else "FAIL",
             "detail": f"pids={observed_pids} max_concurrent={observed_max} samples={observed_samples}"
                       + (" (from prior run of the same task)" if reused_orca else "")},
            {"name": "orca_terminated_normally", "status": "PASS" if orca_ok else "FAIL",
             "detail": str(out_path) if out_path else "no ORCA output"},
            {"name": "orca_energy_matches_task_result", "status": "PASS" if energy_ok else "FAIL",
             "detail": f"orca={orca_energy} task_result={energy}"},
            {"name": ("task_result_complete" if receipt else "step_result_and_manifest_complete"),
             "status": "PASS" if complete_ok else "FAIL",
             "detail": (json.dumps(task_result.get("missing_required_facts")) if receipt
                        else f"step_result={step_result.get('status')} manifest={manifest.get('status')}")},
            {"name": ("attempt_and_release_agree" if receipt else "attempt_recorded_release_not_emitted"),
             "status": "PASS" if attempt_ok else "FAIL",
             "detail": f"attempt={attempt} release_id={release_id or 'NOT_EMITTED'} chain={chain}"},
            {"name": "exactly_one_dispatch", "status": "PASS" if dispatch_ok else "FAIL",
             "detail": f"job.started={job_started_events(work_dir)}"},
            {"name": "capability_science", "status": "PASS" if science_ok else "FAIL",
             "detail": science_detail},
        ]
        passed = all(item["status"] == "PASS" for item in assertions)
        extensions = {
            "schema": "acp2.b14b.capability-run.v1",
            "capability": args.workflow,
            "backend": "local",
            "result": "PASS" if passed else "FAIL",
            "fixture_id": args.fixture_id,
            "fixture_path": str(Path(args.input).resolve()),
            "fixture_sha256": sha256_file(Path(args.input)),
            "exact_command": (read_json(Path(args.prior_run)).get("exact_command") if args.reuse_task and args.prior_run else " ".join(args.replay)),
            "reuse_command": " ".join(args.replay) if args.reuse_task else None,
            "candidate_sha": args.candidate_sha,
            "controller": controller,
            "task_ref": task_id,
            "attempt": attempt,
            "release_id": release["release_id"],
            "release_digest": release["release_digest"],
            "release_status": release["release_status"],
            "execution_chain": chain,
            "submission_id": receipt.get("submission_id"),
            "plan_identity": plan_identity,
            "result_digest": result_digest,
            "step_identity": step_result.get("step_identity"),
            "result_manifest_digest": "sha256:" + sha256_file(work_dir / "RESULT" / "result_manifest.json")
            if (work_dir / "RESULT" / "result_manifest.json").is_file() else None,
            "work_dir": str(work_dir),
            "orca_binary": str(Path(orca_path).resolve()),
            "orca_output": str(out_path) if out_path else None,
            "orca_terminated_normally": orca_ok,
            "orca_energy_hartree": orca_energy,
            "orca_pids": observed_pids,
            "orca_max_concurrent": observed_max,
            "orca_samples": observed_samples,
            "orca_observation_source": "reused_prior_run" if reused_orca else "live_sampler",
            "reused_task": bool(args.reuse_task),
            "job_started_events": job_started_events(work_dir),
            "observed": {
                "status": status,
                "energy_hartree": energy,
                "symbols": symbols,
                "coordinates": coordinates,
                "frequencies": frequencies,
                "n_imaginary": payload.get("n_imaginary"),
                "optimization_status": payload.get("optimization_status"),
                "required_facts": task_result.get("required_facts"),
                "exit_code": task_result.get("exit_code"),
                "manifest_status": manifest.get("status"),
                "execution_chain": chain,
                "step_result_status": step_result.get("status"),
                "step_result_present": bool(step_result),
            },
            "assertions": assertions,
            "native_artifacts": native,
            "written_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    except Exception as exc:  # noqa: BLE001 - the driver records every failure verbatim
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        if sampler is not None:
            sampler.stop()
        if probe is not None:
            probe.close()

    if failure is not None:
        extensions.update(
            {
                "schema": "acp2.b14b.capability-run.v1",
                "capability": args.workflow,
                "backend": "local",
                "result": "FAIL",
                "fixture_id": args.fixture_id,
                "fixture_path": str(Path(args.input).resolve()),
                "exact_command": " ".join(args.replay),
                "candidate_sha": args.candidate_sha,
                "controller": controller,
                "error": failure,
                "assertions": assertions,
                "written_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
    (out / "run.json").write_text(json.dumps(extensions, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(extensions, indent=2))
    return EXIT_PASS if extensions.get("result") == "PASS" else EXIT_FAIL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--launch", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--workflow", required=True, choices=sorted(LEVEL_KEY))
    parser.add_argument("--target", required=True, choices=("local", "lsf"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--fixture-id", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--method", default="HF")
    parser.add_argument("--basis", default="STO-3G")
    parser.add_argument("--orca", default="/home/redacted-user/orca611/orca")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--reuse-task", default=None,
                        help="Validate an existing TaskRef without submitting a new task")
    parser.add_argument("--prior-run", default=None,
                        help="run.json from the original submission of a reused task")
    args = parser.parse_args(argv)
    args.out = Path(args.out)
    args.replay = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    if args.target == "lsf":
        return run_lsf(args)
    return run_local(args)


if __name__ == "__main__":
    raise SystemExit(main())
