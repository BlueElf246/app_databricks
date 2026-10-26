"""Write the job spec for the Project 1 daily medallion job (design 1.5 'Daily job order')."""
import json
import sys

BASE = "/Users/lemanhdat2112@gmail.com/app_databricks/Analytic"

# task_key -> (notebook, depends_on)
TASKS = {
    "refresh_daily":          ("refresh/run_refresh", []),
    "bronze_ingest":          ("bronze/ingest_raw", ["refresh_daily"]),
    "stg_channels":           ("stg/channels", ["bronze_ingest"]),
    "stg_videos":             ("stg/videos", ["bronze_ingest"]),
    "stg_comments":           ("stg/comments", ["bronze_ingest"]),
    "stg_search_results":     ("stg/search_results", ["bronze_ingest"]),
    "stg_restaurant_mentions": ("stg/restaurant_mentions", ["stg_videos"]),
    "gold_dims":              ("gold/dims", ["stg_channels", "stg_videos", "stg_comments", "stg_search_results"]),
    "gold_facts":             ("gold/facts", ["gold_dims"]),
    "gold_restaurants":       ("gold/restaurants", ["gold_dims", "stg_restaurant_mentions"]),
    "gold_checks":            ("gold/checks", ["gold_facts", "gold_restaurants"]),
    "gold_agg_ranking":       ("gold/agg_restaurant_ranking", ["gold_checks"]),
}

job = {
    "name": "Project 1 — YouTube restaurants medallion (daily)",
    "description": "refresh → Bronze → Silver → Gold dims → facts → checks → agg_restaurant_ranking. "
                   "Design: Notion 'Redesign Project 1 data model with Kimball' 1.5. A failed step stops every later step.",
    "schedule": {"quartz_cron_expression": "0 0 1 * * ?", "timezone_id": "Asia/Ho_Chi_Minh", "pause_status": sys.argv[1]},
    "max_concurrent_runs": 1,
    "timeout_seconds": 7200,
    "email_notifications": {"on_failure": ["lemanhdat2112@gmail.com"]},
    "parameters": [{"name": "dry_run", "default": "false"}],
    "tags": {"project": "project1", "layer": "medallion"},
    "tasks": [
        {
            "task_key": key,
            "notebook_task": {"notebook_path": f"{BASE}/{nb}", "source": "WORKSPACE"},
            **({"depends_on": [{"task_key": d} for d in deps]} if deps else {}),
            "timeout_seconds": 1800,
        }
        for key, (nb, deps) in TASKS.items()
    ],
}
print(json.dumps(job, ensure_ascii=False, indent=1))
