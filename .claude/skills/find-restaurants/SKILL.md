---
name: find-restaurants
description: Use when datle asks to find restaurants ("tìm quán sushi", "tìm quán phở trên YouTube"): runs the Databricks search job project1_search and replies with the top 10.
---

# Find restaurants via the Project 1 search job

Needs the Claude cloud environment `run_search` (network secret "OAuth 2.0 client credentials"
for `dbc-66b44822-7fe7.cloud.databricks.com`, path `/api/2.2/jobs/`, service principal
`claude-search` with CAN_MANAGE_RUN on the job). In any other environment the proxy answers 403:
say so and stop.

1. Pick the mode: `places` (Google Maps, default) or `youtube` when datle says YouTube / video.
2. Run from the repo root:
   `python scripts/run_search_job.py "<keyword exactly as typed>" [--mode youtube] [--max-results N]`
   - Dry run by default (nothing written to Lakebase). Add `--save` only when datle says "lưu" / save.
   - `--target prod` only when datle asks for prod.
3. Reply in Vietnamese: the run link, how many results, and the top 10
   (Places: name · rating (reviews) · price · address; YouTube: title · channel · views).
   Places costs 1 call per 20 results; the job stops at 800 calls per month.
4. Errors: "job not found" = bundle not deployed or the service principal lacks CAN_MANAGE_RUN;
   401/403 from Databricks = the network secret or its client secret is wrong.
