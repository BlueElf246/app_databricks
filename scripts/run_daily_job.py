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

JOB_NAME = "Project 1 — auto search + medallion (daily 01h)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target", choices=["dev", "prod"], default="dev")
    parser.add_argument("--timeout", type=int, default=7200, help="seconds to wait for the run")
    args = parser.parse_args(argv)

    token = _token()
    name = JOB_NAME if args.target == "prod" else f"[dev] {JOB_NAME}"
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
    for task in sorted(run.get("tasks", []), key=lambda t: t.get("start_time") or 0):
        state = task.get("state", {})
        minutes = round((task.get("execution_duration") or 0) / 60000, 1)
        print(f"- {task['task_key']}: {state.get('result_state') or state.get('life_cycle_state')} ({minutes} min)")
        if state.get("result_state") not in (None, "SUCCESS"):
            output = _api(token, "GET", "jobs/runs/get-output", query={"run_id": task["run_id"]})
            print(f"  ERROR: {output.get('error')}\n  {(output.get('error_trace') or '')[-1500:]}")
    summary = next((t for t in run.get("tasks", []) if t["task_key"] == "run_summary"), None)
    if summary:
        output = _api(token, "GET", "jobs/runs/get-output", query={"run_id": summary["run_id"]})
        result = (output.get("notebook_output") or {}).get("result")
        if result:
            print(json.dumps(json.loads(result), ensure_ascii=False, indent=1))
    return 0 if run["state"].get("result_state") == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
