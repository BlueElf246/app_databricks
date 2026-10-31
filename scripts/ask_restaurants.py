"""Answer "what should I eat in Quận 7?" from the restaurant dashboard's Gold tables (read-only).

Used by Claude cloud threads (environment "run_search") when datle asks from the Claude app
e.g. "hôm nay nên ăn gì ở quận 7?". The Claude environment's network secret only reaches
/api/2.2/jobs/, so the query runs as a ONE-TIME run (jobs/runs/submit, serverless) of the
notebook src/notebooks/ask/ask_restaurants.py taken straight from GitHub: nothing is deployed,
no existing job is triggered, and the notebook only SELECTs from the Gold schema. A run takes
about 1-2 minutes (serverless start). Auth: same as scripts/run_search_job.py. Standard library only.

Usage:
    python scripts/ask_restaurants.py --district "quận 7"
    python scripts/ask_restaurants.py --district q1 --dish phở --max-price 1 --top 5
    python scripts/ask_restaurants.py --district "Bình Thạnh" --sort trending
    python scripts/ask_restaurants.py --dish sushi --legit-only --json

--max-price is the dashboard price band: 1 = dưới 100k, 2 = 100-300k, 3 = 300-600k, 4 = trên 600k
(per person). --sort: legit (default, legit_score), trending (views in the last 7 days),
hot (ranking_score, "Đang hot"), rating (Google rating).
"""
import argparse
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from run_search_job import _api, _token  # noqa: E402  (same host + OAuth handling)

REPO_URL = "https://github.com/BlueElf246/app_databricks"
NOTEBOOK = "src/notebooks/ask/ask_restaurants"
GOLD = {"dev": "prod.dev_gold", "prod": "prod.gold"}


def ask(args: argparse.Namespace) -> dict:
    token = _token()
    params = {
        "gold_schema": GOLD[args.target], "district": args.district or "", "dish": args.dish or "",
        "max_price_band": str(args.max_price or ""), "city": args.city or "", "sort": args.sort,
        "legit_only": "true" if args.legit_only else "false", "limit": str(args.top),
    }
    body = {
        "run_name": f"ask_restaurants {args.district or ''} {args.dish or ''}".strip(),
        "git_source": {"git_url": REPO_URL, "git_provider": "gitHub", "git_branch": args.branch},
        "tasks": [{"task_key": "ask", "notebook_task": {"notebook_path": NOTEBOOK, "source": "GIT",
                                                         "base_parameters": params}}],
        "timeout_seconds": args.timeout,
    }
    run_id = _api(token, "POST", "jobs/runs/submit", body)["run_id"]
    print(f"run {run_id} submitted: {params}", file=sys.stderr, flush=True)
    deadline = time.time() + args.timeout
    while True:
        run = _api(token, "GET", "jobs/runs/get", query={"run_id": run_id})
        if run["state"].get("life_cycle_state") in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            break
        if time.time() > deadline:
            raise SystemExit(f"still running after {args.timeout}s: {run.get('run_page_url')}")
        time.sleep(5)
    task_run = run["tasks"][0]["run_id"]
    output = _api(token, "GET", "jobs/runs/get-output", query={"run_id": task_run})
    if output.get("error"):
        raise SystemExit(f"ERROR: {output['error']}\n{(output.get('error_trace') or '')[-2000:]}\n{run.get('run_page_url')}")
    return json.loads((output.get("notebook_output") or {}).get("result") or "{}")


def show(result: dict) -> None:
    rows = result.get("rows", [])
    as_of = rows[0]["as_of_date_key"] if rows else "-"
    print(f"{result.get('count', 0)} quán · filters {json.dumps(result.get('filters'), ensure_ascii=False)} · data as of {as_of}")
    for i, r in enumerate(rows, start=1):
        rating = f"{r['google_rating']}★ ({r['google_rating_count']})" if r.get("google_rating") else "chưa có rating"
        price = r.get("price_range") or r.get("price_band") or "-"
        print(f"{i:>2}. {r['restaurant_name']} — {r.get('cuisine') or '-'} · {rating} · giá {price}"
              f" · legit {r.get('legit_score')} ({r.get('legit_label')}) · 7d views {r.get('views_7d')}")
        print(f"    {r.get('address') or '-'} [{r.get('district')}, {r.get('city')}]")
        print(f"    Maps: {r['maps_url']}")
        if r.get("top_video_url"):
            print(f"    Video: {r['top_video_title']} ({r.get('top_video_channel')}, {r.get('top_video_views')} view) {r['top_video_url']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--district", help='"quận 7", "q7", "Bình Thạnh", "Thủ Đức"...')
    parser.add_argument("--dish", help="cuisine or dish, matched on cuisine, name and video titles: phở, sushi, lẩu...")
    parser.add_argument("--max-price", type=int, choices=[1, 2, 3, 4], help="highest price band")
    parser.add_argument("--city", help="e.g. Hồ Chí Minh, Hà Nội")
    parser.add_argument("--sort", choices=["legit", "trending", "hot", "rating"], default="legit")
    parser.add_argument("--legit-only", action="store_true", help='only legit_label = "Legit"')
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--target", choices=["dev", "prod"], default="dev", help="dev = prod.dev_gold (what the dashboard uses now)")
    parser.add_argument("--branch", default="dab-bundle", help="Git branch the notebook is read from")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--json", action="store_true", help="print the raw JSON result")
    args = parser.parse_args(argv)
    result = ask(args)
    print(json.dumps(result, ensure_ascii=False, indent=1)) if args.json else show(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
