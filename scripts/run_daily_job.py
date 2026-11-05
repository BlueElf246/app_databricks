"""Run the Project 1 medallion job (project1_daily) end to end from a chat session and print
each task's state plus the run_summary JSON (row counts per table, rows added today, top 10).

Same auth as run_search_job.py (service principal claude-search, CAN_MANAGE_RUN on the dev job).

Usage:
    python scripts/run_daily_job.py              # dev, writes (dry_run=false, the bundle default)
    python scripts/run_daily_job.py --dry-run    # refresh calls the APIs but writes nothing
"""
import argparse
import json
import sys
import time

from run_search_job import _api, _token

# 2026-11-03: the pipeline is the refresh job (every 3 h); auto search is its own daily job that triggers it
JOB_NAME = "Project 1 — refresh → serving (every 3h)"
AUTO_SEARCH_JOB_NAME = "Project 1 — auto search (daily 01h)"


def _print_tasks(token: str, run: dict, indent: str = "") -> None:
    """Print each task's state and error; a run_job_task (2026-11-05: the refresh job runs the stg / gold / serving
    jobs) is expanded into the tasks of the child run it triggered."""
    for task in sorted(run.get("tasks", []), key=lambda t: t.get("start_time") or 0):
        state = task.get("state", {})
        minutes = round((task.get("execution_duration") or 0) / 60000, 1)
        print(f"{indent}- {task['task_key']}: {state.get('result_state') or state.get('life_cycle_state')} ({minutes} min)")
        if "run_job_task" in task and task.get("run_id"):
            output = _api(token, "GET", "jobs/runs/get-output", query={"run_id": task["run_id"]})
            child_id = (output.get("run_job_output") or {}).get("run_id")
            if child_id:
                _print_tasks(token, _api(token, "GET", "jobs/runs/get", query={"run_id": child_id}), indent + "    ")
                continue
        if state.get("result_state") not in (None, "SUCCESS", "EXCLUDED"):
            output = _api(token, "GET", "jobs/runs/get-output", query={"run_id": task["run_id"]})
            print(f"{indent}  ERROR: {output.get('error')}\n{indent}  {(output.get('error_trace') or '')[-1500:]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--auto-search", action="store_true",
                        help="run the auto search job (which then runs the refresh job) instead of the refresh job only")
    parser.add_argument("--target", choices=["dev", "prod"], default="dev")
    parser.add_argument("--timeout", type=int, default=7200, help="seconds to wait for the run")
    args = parser.parse_args(argv)

    token = _token()
    base = AUTO_SEARCH_JOB_NAME if args.auto_search else JOB_NAME
    name = base if args.target == "prod" else f"[dev] {base}"
    jobs = _api(token, "GET", "jobs/list", query={"name": name}).get("jobs", [])
    if not jobs:
        raise SystemExit(f'job "{name}" not found (deployed? does claude-search have CAN_MANAGE_RUN?)')
    body = {"job_id": jobs[0]["job_id"]}
    if args.dry_run:
        body["job_parameters"] = {"dry_run": "true"}
    run_id = _api(token, "POST", "jobs/run-now", body)["run_id"]
    print(f"run {run_id} started", flush=True)

    deadline = time.time() + args.timeout
    while True:
        run = _api(token, "GET", "jobs/runs/get", query={"run_id": run_id})
        if run["state"].get("life_cycle_state") in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            break
        if time.time() > deadline:
            print(f"still running after {args.timeout}s: {run.get('run_page_url')}")
            return 2
        time.sleep(20)

    print(f"result: {run['state'].get('result_state')} · {run.get('run_page_url')}")
    _print_tasks(token, run)
    summary = next((t for t in run.get("tasks", []) if t["task_key"] == "run_summary"), None)
    if summary:
        output = _api(token, "GET", "jobs/runs/get-output", query={"run_id": summary["run_id"]})
        result = (output.get("notebook_output") or {}).get("result")
        if result:
            print(json.dumps(json.loads(result), ensure_ascii=False, indent=1))
    return 0 if run["state"].get("result_state") == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
