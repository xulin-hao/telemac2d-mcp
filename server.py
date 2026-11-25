"""Local Windows MCP server for standalone TELEMAC-2D cases."""

from contextlib import redirect_stdout
import json
import math
import msvcrt
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
import time
from uuid import uuid4

import numpy as np
from data_manip.extraction.telemac_file import TelemacFile
from execution.telemac_cas import TelemacCas
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

DEV = Path(__file__).resolve().parent
ROOT = Path(os.environ["HOMETEL"]).resolve()
CASES = Path(os.environ.get("TELEMAC_CASES_ROOT", DEV / "cases")).resolve()
RUNS = DEV / "runs"
DICTIONARY = ROOT / "dictionaries" / "telemac2d.dico"
SOLVER = ROOT / "scripts" / "python3" / "telemac2d.py"
TERMINAL = {"succeeded", "failed"}
server = MCPServer("TELEMAC-2D")


def relative_path(value):
    path = PureWindowsPath(value)
    if (path.drive or path.root or not path.parts or ".." in path.parts
            or any(part.endswith((".", " ")) or ":" in part or PureWindowsPath(part).is_reserved()
                   for part in path.parts)):
        raise ValueError(f"Expected a safe relative path: {value!r}")
    return Path(*path.parts)


def within(base, relative):
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ValueError(f"Links are unsupported in case and result paths: {relative}")
    if not current.resolve().is_relative_to(base):
        raise ValueError(f"Path resolves outside its base directory: {relative}")
    return current


def case_info(case_path):
    case_file = within(CASES, relative_path(case_path))
    if case_file.suffix.lower() != ".cas" or not case_file.is_file():
        raise ValueError(f"Steering file not found under {CASES}: {case_path!r}")
    with redirect_stdout(sys.stderr):
        case = TelemacCas(str(case_file), str(DICTIONARY), check_files=False)
    inputs, outputs, issues = {}, {}, []
    if any(str(value).strip() for value in np.atleast_1d(case.get("COUPLING WITH", "")) if value is not None):
        issues.append("Coupled cases are outside the standalone TELEMAC-2D workflow.")

    for declared, file_map, is_input in (
        (case.in_files, inputs, True), (case.out_files, outputs, False)
    ):
        for keyword in declared:
            raw = case.values[keyword]
            values = raw if isinstance(raw, list) else [raw]
            paths = []
            for value in values:
                value = value.strip()
                if not value:
                    continue
                try:
                    relative = relative_path(value)
                    resolved = within(case_file.parent, relative)
                    if is_input and not resolved.is_file() and not (
                        keyword == "FORTRAN FILE" and resolved.is_dir()
                    ):
                        issues.append(f"{keyword}: input missing or unsupported: {value}")
                    if not is_input and relative == Path(case_file.name):
                        issues.append(f"{keyword}: output conflicts with the steering file.")
                    paths.append(relative.as_posix())
                except (OSError, ValueError) as error:
                    issues.append(f"{keyword}: {error}")
            if paths:
                file_map[keyword] = paths

    result_paths = outputs.get("RESULTS FILE", [])
    if len(result_paths) != 1:
        issues.append("Specify one RESULTS FILE in the steering file.")
    result_format = str(case.get("RESULTS FILE FORMAT", "SERAFIN")).strip().upper()
    if result_format not in {"SERAFIN", "SERAFIND"}:
        issues.append(f"Expected SERAFIN or SERAFIND results, found {result_format}.")
    input_paths = [Path(item) for paths in inputs.values() for item in paths]
    for item in (Path(item) for paths in outputs.values() for item in paths):
        if any(item.is_relative_to(input_path) or input_path.is_relative_to(item)
               for input_path in input_paths):
            issues.append(f"Output conflicts with a staged input: {item}")

    return case_file, {
        "case_path": case_file.relative_to(CASES).as_posix(),
        "inputs": inputs,
        "outputs": outputs,
        "result_format": result_format,
        "time_step_seconds": case.get("TIME STEP"),
        "time_steps": case.get("NUMBER OF TIME STEPS"),
        "issues": issues,
        "ready": not issues,
    }


def stage_case(case_file, info, target):
    target.mkdir()
    shutil.copy2(case_file, target / case_file.name)
    for item in sorted({path for paths in info["inputs"].values() for path in paths}):
        relative = relative_path(item)
        source = within(case_file.parent, relative)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            for child in source.rglob("*"):
                within(source, child.relative_to(source))
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
    for paths in info["outputs"].values():
        for item in paths:
            (target / relative_path(item)).parent.mkdir(parents=True, exist_ok=True)


def write_state(job, state):
    temporary = job / "state.json.tmp"
    temporary.write_text(json.dumps(state, allow_nan=False), encoding="utf-8")
    for attempt in range(5):
        try:
            temporary.replace(job / "state.json")
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def read_state(job):
    return json.loads((job / "state.json").read_text(encoding="utf-8"))


def run_directory(run_id):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ToolError("Invalid run ID.")
    job = within(RUNS, Path(run_id))
    if not (job / "state.json").is_file():
        raise ToolError("Run ID not found.")
    return job


def open_result(job, state):
    path = within(job / "case", relative_path(state["result_file"]))
    return TelemacFile(str(path), fformat=state["result_format"])


def run_worker(job):
    state = read_state(job)
    state["pid"] = os.getpid()
    write_state(job, state)
    try:
        with (RUNS / "solver.lock").open("a+b") as lock:
            while True:
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno != 13:
                        raise
                    time.sleep(0.25)

            state.update(status="running", started_at=time.time())
            write_state(job, state)
            with (job / "solver.log").open("wb") as log:
                command = [sys.executable, "-B", str(SOLVER), state["steering_file"], "--ncsize=1"]
                completed = subprocess.run(
                    command, cwd=job / "case", stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, check=False,
                )
            state["exit_code"] = completed.returncode
            if completed.returncode:
                raise RuntimeError(f"TELEMAC exited with code {completed.returncode}.")
            result = open_result(job, state)
            try:
                if not len(result.times):
                    raise RuntimeError("The result contains no time frames.")
            finally:
                result.close()
        state["status"] = "succeeded"
    except Exception as error:
        state.update(status="failed", error=str(error))
    state["finished_at"] = time.time()
    write_state(job, state)


def worker_is_active(pid):
    import _winapi

    try:
        handle = _winapi.OpenProcess(0x00100000, False, pid)
    except OSError as error:
        return error.winerror != 87  # An unknown or exited process ID.
    try:
        return _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_TIMEOUT
    finally:
        _winapi.CloseHandle(handle)


@server.tool()
def check_telemac() -> dict[str, str]:
    """Report the local TELEMAC and Python configuration and the case directory."""
    return {
        "python": sys.executable, "telemac_root": str(ROOT),
        "telemac_config": os.environ["SYSTELCFG"], "cases_root": str(CASES),
        "result_reader": TelemacFile.__mro__[1].__name__,
    }


@server.tool()
def inspect_case(case_path: str) -> dict[str, object]:
    """Inspect a .cas file relative to the case directory and check its inputs."""
    try:
        _, info = case_info(case_path)
        return info
    except Exception as error:
        raise ToolError(f"Could not inspect the steering file: {error}") from error


@server.tool()
def run_case(case_path: str) -> dict[str, object]:
    """Queue one serial run from a trusted case, which may execute its user Fortran."""
    try:
        case_file, info = case_info(case_path)
        if info["issues"]:
            raise ValueError(" ".join(info["issues"]))
        run_id = uuid4().hex
        job = RUNS / run_id
        job.mkdir(parents=True)
        state = {
            "run_id": run_id, "status": "queued", "case_path": info["case_path"],
            "steering_file": case_file.name, "result_file": info["outputs"]["RESULTS FILE"][0],
            "result_format": info["result_format"], "submitted_at": time.time(),
        }
        try:
            stage_case(case_file, info, job / "case")
            write_state(job, state)
            with (job / "worker.log").open("wb") as log:
                subprocess.Popen(
                    [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", run_id],
                    cwd=job, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
        except Exception as error:
            state.update(status="failed", error=str(error), finished_at=time.time())
            write_state(job, state)
            raise ValueError(f"Run {run_id} could not be started: {error}") from error
        return {"run_id": run_id, "status": "queued"}
    except Exception as error:
        raise ToolError(str(error)) from error


@server.tool()
def get_run_status(run_id: str) -> dict[str, object]:
    """Get the current state, solver log tail and result path for a submitted run."""
    job = run_directory(run_id)
    state = read_state(job)
    if state["status"] not in TERMINAL:
        pid = state.get("pid")
        if ((pid and not worker_is_active(pid)) or
                (not pid and time.time() - state["submitted_at"] > 60)):
            # A worker can finish between reading its state and checking the PID.
            state = read_state(job)
            if state["status"] not in TERMINAL:
                state.update(status="interrupted", error="The solver worker could not be found.")
    log = job / "solver.log"
    if not log.is_file():
        log = job / "worker.log"
    if log.is_file():
        with log.open("rb") as handle:
            handle.seek(max(0, log.stat().st_size - 8192))
            state["log_tail"] = handle.read(8192).decode("utf-8", errors="replace")
        state["log_path"] = str(log)
    if state["status"] == "succeeded":
        state["result_path"] = str(job / "case" / state["result_file"])
    return state


@server.tool()
def summarize_results(run_id: str, frame: int = -1, variables: list[str] | None = None) -> dict[str, object]:
    """Summarize nodal variables. Frame is zero-based, or -1 for the final frame."""
    job = run_directory(run_id)
    state = read_state(job)
    if state["status"] != "succeeded":
        raise ToolError("Results are available after the run succeeds.")
    result = open_result(job, state)
    try:
        count = len(result.times)
        index = count - 1 if frame == -1 else frame
        if index < 0 or index >= count:
            raise ToolError(f"Frame out of range. Available frames: 0 through {count - 1}.")
        stored = {name.strip().upper(): (name, unit.strip())
                  for name, unit in zip(result.varnames, result.varunits, strict=True)}
        derived = {}
        if {"VELOCITY U", "VELOCITY V"} <= stored.keys():
            derived["SPEED"] = ("VELOCITY U", "VELOCITY V")
        bed = next((name for name in ("BOTTOM", "BOTTOM ELEVATION") if name in stored), None)
        if bed and "WATER DEPTH" in stored:
            derived["FREE SURFACE"] = ("WATER DEPTH", bed)
        available = sorted(stored.keys() | derived.keys())
        requested = available if variables is None else list(dict.fromkeys(
            name.strip().upper() for name in variables
        ))
        missing = [name for name in requested if name not in available]
        if missing:
            raise ToolError(f"Unavailable variables: {', '.join(missing)}. Available: {', '.join(available)}.")
        arrays, summaries = {}, {}
        def values(name):
            if name not in arrays:
                arrays[name] = result.get_data_value(stored[name][0], index)
            return arrays[name]

        for name in requested:
            if name in stored:
                data, unit = values(name), stored[name][1]
            else:
                first, second = derived[name]
                a, b = values(first), values(second)
                data = np.hypot(a, b) if name == "SPEED" else a + b
                unit = stored[first][1]
            finite = data[np.isfinite(data)]
            summaries[name] = {
                "unit": unit, "valid_nodes": int(finite.size),
                "invalid_nodes": int(data.size - finite.size),
                "min": float(finite.min()) if finite.size else None,
                "max": float(finite.max()) if finite.size else None,
            }
        seconds = float(result.times[index])
        if not math.isfinite(seconds):
            raise ToolError("The selected frame has an invalid simulation time.")
        return {
            "run_id": run_id, "frame": index, "frames": count, "time_seconds": seconds,
            "nodes": int(result.npoin2), "available_variables": available, "variables": summaries,
        }
    finally:
        result.close()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        run_worker(run_directory(sys.argv[2]))
    else:
        server.run(transport="stdio")
