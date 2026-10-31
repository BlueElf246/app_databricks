"""Trigger the on-demand search job (project1_search) from a chat session and print the result.

Used by Claude cloud threads when datle types e.g. "tìm quán sushi trên Google Maps" in the
Claude app. Auth = OAuth M2M of the service principal "claude-search" (CAN_MANAGE_RUN on the
job). Either the Claude environment's network secret ("OAuth 2.0 client credentials" on the
workspace host) adds the token to every request, or env DATABRICKS_CLIENT_ID +
DATABRICKS_CLIENT_SECRET are set and the script gets the token itself. Host: env
DATABRICKS_HOST, default the AWS workspace below. Standard library only.

Usage:
    python scripts/run_search_job.py "quán sushi"                      # places, 20 results, saved to Lakebase
    python scripts/run_search_job.py "phở" --mode youtube --max-results 10
    python scripts/run_search_job.py "quán sushi" --dry-run           # call the API, write nothing
    python scripts/run_search_job.py "quán sushi" --target prod      # job without the "[dev] " prefix
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

JOB_NAME = "Project 1 — keyword search (YouTube / Google Places)"


def _host() -> str:
    host = os.environ.get("DATABRICKS_HOST", "https://dbc-66b44822-7fe7.cloud.databricks.com").rstrip("/")
    return host if host.startswith("https://") else f"https://{host}"


def _token() -> str | None:
    """OAuth M2M token, or None when the Claude environment's network secret
    (type "OAuth 2.0 client credentials") adds the Authorization header itself."""
    client_id = os.environ.get("DATABRICKS_CLIENT_ID")
    secret = os.environ.get("DATABRICKS_CLIENT_SECRET")
    if not (client_id and secret):
        return None
    request = urllib.request.Request(
        f"{_host()}/oidc/v1/token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "all-apis"}).encode(),
        headers={"Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["access_token"]


def _api(token: str | None, method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    url = f"{_host()}/api/2.2/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Databricks API {method} {path} failed: {error.code} {error.read().decode()[:500]}")


def find_job(token: str | None, target: str) -> int:
    name = JOB_NAME if target == "prod" else f"[dev] {JOB_NAME}"
    jobs = _api(token, "GET", "jobs/list", query={"name": name}).get("jobs", [])
    if not jobs:
        raise SystemExit(f'job "{name}" not found (deployed? does the service principal have CAN_MANAGE_RUN?)')
    return jobs[0]["job_id"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("keyword")
    parser.add_argument("--mode", choices=["places", "youtube"], default="places")
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true", help="call the API but write nothing (testing a new feature)")
    parser.add_argument("--target", choices=["dev", "prod"], default="dev")
    parser.add_argument("--timeout", type=int, default=900, help="seconds to wait for the run")
    args = parser.parse_args(argv)

    token = _token()
    job_id = find_job(token, args.target)
    params = {
        "mode": args.mode,
        "keyword": args.keyword,
        "max_results": str(args.max_results),
        "dry_run": "true" if args.dry_run else "false",
    }
    run_id = _api(token, "POST", "jobs/run-now", {"job_id": job_id, "job_parameters": params})["run_id"]
    print(f"run {run_id} started: {params}", flush=True)

    deadline = time.time() + args.timeout
    while True:
        run = _api(token, "GET", "jobs/runs/get", query={"run_id": run_id})
        state = run["state"]
        if state.get("life_cycle_state") in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            break
        if time.time() > deadline:
            print(f"still running after {args.timeout}s: {run.get('run_page_url')}")
            return 2
        time.sleep(10)

    print(f"result: {state.get('result_state')} · {run.get('run_page_url')}")
    for task in run.get("tasks", []):
        output = _api(token, "GET", "jobs/runs/get-output", query={"run_id": task["run_id"]})
        if output.get("error"):
            print(f"ERROR: {output['error']}\n{(output.get('error_trace') or '')[-2000:]}")
            continue
        result = (output.get("notebook_output") or {}).get("result") or ""
        try:
            summary = json.loads(result)
        except ValueError:
            print(result)
            continue
        top = summary.pop("top", [])
        print(json.dumps(summary, ensure_ascii=False))
        for rank, item in enumerate(top, start=1):
            if args.mode == "places":
                print(f"{rank:>2}. {item['display_name']} · {item['rating']} ({item['user_rating_count']} review)"
                      f" · {item.get('price_level') or '-'} · {item['formatted_address']} · {item['google_maps_uri']}")
            else:
                print(f"{rank:>2}. {item['title']} · {item['channel']} · {item['views']} view · {item['url']}")
    return 0 if state.get("result_state") == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
