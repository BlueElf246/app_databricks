# Project 1 bundle — how to deploy and run it

This repo is a **Databricks Asset Bundle** (DAB). One file, `databricks.yml`, plus the YAML files in `resources/` describe everything that runs on Databricks:

| Resource | Key in the bundle | What it is |
| --- | --- | --- |
| Job | `project1_daily` | The daily medallion pipeline (12 notebook tasks, refresh → Bronze → Silver → Gold → checks → ranking) |
| App | `youtube_search` | The Streamlit app in `src/app` |
| Schemas | `bronze`, `silver`, `gold` | The three medallion schemas in catalog `prod` |

You never import notebooks or create jobs by hand any more: you change files in git, then `deploy`.

---

## 1. One-time setup

### 1.1 Install the Databricks CLI (≥ 0.270)

Check what you have:

```bash
databricks --version
```

WSL has v1.8.0 and Windows has v1.4.0. Both are new enough. To install or upgrade on Linux / WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sudo sh
```

On Windows: `winget install Databricks.DatabricksCLI` (or `winget upgrade` it).

### 1.2 Log in to the workspace

The bundle targets `https://dbc-66b44822-7fe7.cloud.databricks.com` (set in `databricks.yml`). Log in **from the shell you will deploy from**. WSL and Windows keep separate logins.

```bash
databricks auth login --host https://dbc-66b44822-7fe7.cloud.databricks.com --profile dbc-66b44822-7fe7
```

A browser opens. Sign in, then check:

```bash
databricks auth profiles
```

The `dbc-66b44822-7fe7` row must say `Valid: YES`. If it later says *"refresh token is invalid"*, run the same `auth login` again.

Your `~/.databrickscfg` has more than one profile, so tell the CLI which one to use. Do one of these:

- add `--profile dbc-66b44822-7fe7` to every command below, or
- export it once per shell: `export DATABRICKS_CONFIG_PROFILE=dbc-66b44822-7fe7`

The commands below assume you exported it.

### 1.3 Get the code

```bash
git clone https://github.com/BlueElf246/app_databricks.git
cd app_databricks
git checkout dab-bundle      # until the branch is merged into main
```

All `databricks bundle ...` commands must run from the repo root, the folder that holds `databricks.yml`.

### 1.4 Secrets and grants (once per workspace)

The pipeline and the app read two secrets from scope `youtube`:

| Secret | Status | Used by |
| --- | --- | --- |
| `youtube/pg_password` | ✅ exists | refresh step + app (Postgres role `admin`) |
| `youtube/api_key` | ❌ **missing** | refresh step + app (YouTube Data API v3) |

Add the API key. The CLI asks for the value, so it never appears in your shell history:

```bash
databricks secrets put-secret youtube api_key
```

The transcript step reads `gemini/api_key` (Gemini API). Create that key in Google AI Studio in a GCP project **without billing**, so it stays on the free tier (over the limit the API returns 429 instead of charging):

```bash
databricks secrets create-scope gemini
databricks secrets put-secret gemini api_key
```

The deployed app reads Gold through its service principal. The bundle already grants it `USE SCHEMA` and `SELECT` on `prod.gold`. It also needs `USE CATALOG` on `prod`, which you run once in a SQL editor:

```sql
GRANT USE CATALOG ON CATALOG prod TO `ddf259f9-81a3-4582-a062-846173b290c2`;
```

---

## 2. The two targets

| | `dev` (default) | `prod` |
| --- | --- | --- |
| Schemas | `prod.dev_bronze`, `prod.dev_silver`, `prod.dev_gold` | `prod.bronze`, `prod.silver`, `prod.gold` |
| Job name | `[dev] Project 1 — YouTube restaurants medallion (daily)` | `Project 1 — YouTube restaurants medallion (daily)` (id 807264924625256) |
| Refresh step | dry run: calls the API and checks the guard, writes nothing to Postgres | writes to Postgres |
| Schedule | paused | 01:00 Asia/Ho_Chi_Minh, **paused until the api_key secret exists** |
| App name | `yt-search-lemanhdat2112` | `youtube-restaurant-search` |
| Files go to | `~/.bundle/project1_youtube_restaurants/dev` | `~/.bundle/project1_youtube_restaurants/prod` |

Both targets read the same Lakebase Postgres through the `lakebase_pg` catalog. Only `prod` writes to it.

Without `-t`, every command uses `dev`. Add `-t prod` for production.

---

## 3. Everyday workflow

### 3.1 Check

```bash
databricks bundle validate --strict           # dev
databricks bundle validate --strict -t prod
```

`Validation OK!` means the YAML is valid and every path exists.

### 3.2 See what a deploy would change (prod)

```bash
databricks bundle plan -t prod
```

Read it before every prod deploy. Normal output is `update ...` lines. Stop and investigate if you see `delete` or `recreate` on a schema or the job. The prod schemas also have `prevent_destroy`, so the CLI refuses to delete them.

### 3.3 Deploy

```bash
databricks bundle deploy                      # dev
databricks bundle deploy -t prod              # prod
```

Deploy uploads `src/` and creates or updates the job, the app configuration and the schemas. It does **not** run anything and does **not** start the app.

### 3.4 Run the pipeline

Full run, every task:

```bash
databricks bundle run project1_daily          # dev
databricks bundle run project1_daily -t prod
```

Everything **except** the refresh step. Use this while the api_key secret is missing, or to rebuild from what is already in Postgres:

```bash
databricks bundle run project1_daily -t prod \
  --only bronze_ingest,stg_channels,stg_videos,stg_comments,stg_search_results,stg_restaurant_mentions,gold_dims,stg_video_transcripts,gold_facts,gold_restaurants,gold_checks,gold_agg_ranking
```

Only the refresh, as a dry run in prod (API call + guard, nothing written):

```bash
databricks bundle run project1_daily -t prod --only refresh_daily --params dry_run=true
```

The command prints a **Run URL**. Open it to watch the tasks. A failed task stops every task after it, and you get an email.

All pipeline steps are safe to rerun: MERGE, `replaceWhere` and `CREATE OR REPLACE`, with no duplicates.

### 3.5 Run the app

```bash
databricks bundle run youtube_search -t prod
```

This deploys `src/app` to the app and starts it. The command prints the app URL (`https://youtube-restaurant-search-7474659725199695.aws.databricksapps.com`). A running app costs compute. Stop it with:

```bash
databricks apps stop youtube-restaurant-search
```

### 3.6 What is deployed where

```bash
databricks bundle summary -t prod
```

It lists the job, app and schema names with their URLs.

---

## 4. Common changes

| I want to… | Do this |
| --- | --- |
| Change a notebook | Edit it in `src/notebooks/...`, commit, `deploy`, then `run` to test (dev first, then prod) |
| Change the app | Edit `src/app/...`, `deploy -t prod`, then `bundle run youtube_search -t prod` |
| Add or reorder a job task | Edit `resources/project1_daily.job.yml`, then `validate` → `plan -t prod` → `deploy -t prod` |
| Turn on the daily schedule | After the api_key secret exists **and** a dry run passed: in `databricks.yml`, under `targets.prod.variables`, set `schedule_pause_status: UNPAUSED`, then `deploy -t prod` |
| Pause it again | Set it back to `PAUSED` and deploy |
| Change the failure email | Override `notification_email` under the target in `databricks.yml` |
| Clean up dev | `databricks bundle destroy` (dev only). It deletes the dev job, dev app and the `prod.dev_*` schemas **with their data** |

Never run `bundle destroy -t prod`.

---

## 5. Run things locally (no deploy)

App, from WSL:

```bash
cd src/app
pip install -r requirements.txt
streamlit run app.py
```

It reads `.env` at the repo root (see `.env.example`). The ranking page also needs a Databricks login (`DATABRICKS_CONFIG_PROFILE=dbc-66b44822-7fe7`).

Refresh:

```bash
cd src/app
python refresh_daily.py --dry-run     # API + guard, writes nothing
python refresh_daily.py               # writes to Postgres
```

A notebook run by hand in the workspace uses `prod.*` by default (widgets `bronze` / `silver` / `gold`). Change the widgets to `prod.dev_*` to test against dev.

---

## 6. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `refresh token is invalid` | `databricks auth login --profile dbc-66b44822-7fe7` in the same shell (WSL and Windows are separate) |
| `cannot resolve bundle auth configuration` / wrong workspace | Set `DATABRICKS_CONFIG_PROFILE=dbc-66b44822-7fe7` or pass `--profile` |
| Git Bash on Windows turns `/Users/...` into `C:/.../Users/...` | Run with `MSYS_NO_PATHCONV=1`, or use WSL / PowerShell |
| `refresh_daily` fails with `Secret does not exist ... api_key` | Add the secret (section 1.4), or run with `--only` and skip `refresh_daily` |
| `refresh_daily` fails with `EmptySourceError` | Working as designed: the YouTube API returned 0 rows or more than 20% zero counters, so nothing was written. Check the API key and quota, then rerun |
| `gold_checks` fails | Its output lists each failed check (duplicate grain, orphan key, missing day, ...). The ranking was **not** rebuilt. Fix the data or code, then rerun from `gold_dims` |
| Lakebase `endpoint has been disabled` | Re-enable it: `databricks postgres update-endpoint projects/my-database/branches/production/endpoints/primary spec.disabled --json '{"spec":{"disabled":false}}'` |
| App ranking page: `PERMISSION_DENIED` | Run the `GRANT USE CATALOG` from section 1.4 |
| Plan shows `delete` on a prod resource | Don't deploy. Something in the YAML renamed or removed it. Check `git diff` |
