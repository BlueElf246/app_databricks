# YouTube Restaurant Search — Databricks App + Kimball medallion pipeline

Streamlit app tìm kiếm video YouTube review quán ăn, lưu vào Lakebase Postgres, và một pipeline Bronze → Silver → Gold (Kimball) trên Databricks để xếp hạng quán ăn theo dữ liệu YouTube.

## Tính năng

- 🔎 Tìm kiếm video YouTube qua YouTube Data API v3, lưu video / kênh / kết quả tìm kiếm (kèm `search_order`, `result_rank`) vào Lakebase Postgres (UPSERT)
- 💬 Lấy bình luận của video
- 💾 Xem danh sách video đã lưu với filters & pagination
- 🏆 Xếp hạng quán ăn từ Gold model (`prod.gold.agg_restaurant_ranking`)
- 🔁 Refresh hằng ngày + pipeline medallion chạy bằng một Databricks job
- 📦 Toàn bộ job, app và schemas được deploy bằng một Databricks Asset Bundle (`databricks.yml`)

## Cấu trúc

```
├── databricks.yml                  # Bundle: variables + targets dev / prod
├── resources/
│   ├── project1_daily.job.yml      # Daily medallion job (12 tasks)
│   ├── youtube_search.app.yml      # Streamlit app + its resources (secret, SQL warehouse)
│   └── medallion.schemas.yml       # bronze / silver / gold schemas (+ Gold grant for the app)
├── src/
│   ├── app/                        # Databricks App source (deployed as is)
│   │   ├── app.py                  # Streamlit main page: search, save, comments
│   │   ├── app.yaml                # App command + env (valueFrom → app resources)
│   │   ├── database.py             # Lakebase Postgres connection + schema migration (init_db)
│   │   ├── youtube_client.py       # YouTube Data API v3 client
│   │   ├── gold_client.py          # Read-only access to Gold via a SQL warehouse
│   │   ├── refresh_daily.py        # Daily re-read of saved videos / channels (empty-source guard); also job step 1
│   │   ├── requirements.txt
│   │   └── pages/                  # 2_saved_videos.py, 3_restaurant_ranking.py
│   └── notebooks/                  # Pipeline notebooks (job tasks)
│       ├── refresh/run_refresh     # Runs src/app/refresh_daily.py on serverless
│       ├── bronze/ingest_raw       # Lakebase → <catalog>.bronze.*_raw (append-only)
│       ├── stg/                    # Bronze → silver.stg_* (+ LLM restaurant extraction)
│       ├── gold/                   # Kimball dims, facts, checks, restaurant dim, ranking
│       └── landing/                # Old raw ingest (kept for reference, not in the job)
└── scripts/test_api.py             # YouTube API exploration script
```

## Deploy (Databricks Asset Bundle)

Step-by-step guide (setup, secrets, deploy, run, troubleshooting): [docs/BUNDLE_MANUAL.md](docs/BUNDLE_MANUAL.md).

Workspace `https://dbc-66b44822-7fe7.cloud.databricks.com`, Databricks CLI ≥ 0.270.

| Target | Schemas | Job | Refresh step | Root path |
| --- | --- | --- | --- | --- |
| `dev` (default) | `prod.dev_bronze` / `prod.dev_silver` / `prod.dev_gold` | `[dev] Project 1 — …`, paused | dry run (writes nothing to Postgres) | `~/.bundle/project1_youtube_restaurants/dev` |
| `prod` | `prod.bronze` / `prod.silver` / `prod.gold` | `Project 1 — …` (id 807264924625256), 01:00 Asia/Ho_Chi_Minh | writes | `~/.bundle/project1_youtube_restaurants/prod` |

```bash
databricks bundle validate --strict              # dev
databricks bundle deploy                         # dev
databricks bundle run project1_daily             # dev run
databricks bundle plan -t prod                   # review before a prod deploy
databricks bundle deploy -t prod
```

- Notebooks read the schema names from job parameters `bronze` / `silver` / `gold` (`catalog.schema`, default `prod.*` when a notebook is run by hand), so the same code runs in both targets.
- Both targets read the same Lakebase Postgres (`lakebase_pg`); dev only reads it (refresh is a dry run).
- The prod job, app and schemas existed before the bundle and are bound to it (`bundle deployment bind`), so they keep their ids and history. The prod schemas have `prevent_destroy`.
- Turning the daily schedule on: set `schedule_pause_status: UNPAUSED` for `prod` in `databricks.yml` once the secret `youtube/api_key` exists and a dry run passed, then deploy.

## App setup

### 1. Environment variables

Local: `.env` at the repo root (see `.env.example`). Databricks App: `src/app/app.yaml` + the app resources in `resources/youtube_search.app.yml`.

| Variable | Used by | Note |
| --- | --- | --- |
| `YOUTUBE_API_KEY` | `youtube_client.py` | On Databricks read from secret `youtube/api_key` first |
| `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGPORT`, `PGSSLMODE`, `APP_SCHEMA` | `database.py` | Lakebase Postgres (project `my-database`, branch `production`) |
| `DATABRICKS_WAREHOUSE_ID`, `GOLD_SCHEMA` | `gold_client.py` | Defaults: Serverless Starter warehouse, `prod.gold` |
| `DATABRICKS_CONFIG_PROFILE` (or `DATABRICKS_HOST` + token) | `gold_client.py` | Local only; the deployed app uses its service principal |

The ranking page needs **CAN USE** on the SQL warehouse and **SELECT** on `prod.gold` for whoever runs the app (your user locally, the app's service principal when deployed).

### 2. Install & run

```bash
cd src/app
pip install -r requirements.txt
streamlit run app.py
```

Deployed: `databricks bundle deploy -t prod`, then `databricks bundle run youtube_search -t prod` to deploy and start the app.

`init_db()` creates / migrates the Postgres tables on start (`ADD COLUMN IF NOT EXISTS`, so it is safe to rerun).

## Data pipeline (Kimball medallion)

Design: Notion "Redesign Project 1 data model with Kimball" (1.3 dimensions, 1.4 facts, 1.5 layers and load patterns).

```
YouTube API ─► app (search / save) ─┐
            └► refresh_daily.py ────┴─► Lakebase Postgres public.* (latest values only)
                                          │  (foreign catalog lakebase_pg)
                                          ▼
   BRONZE prod.bronze.*_raw        append-only copy of every read, partitioned by ingest_date, 180-day retention
                                          ▼
   SILVER prod.silver.stg_*        typed, deduped, history by snapshot_date; + stg_restaurant_mentions (LLM)
                                          ▼
   GOLD   prod.gold                dim_date, dim_video, dim_channel, dim_commenter, dim_keyword, dim_restaurant (SCD 2)
                                   fact_video_daily_snapshot, fact_channel_daily_snapshot, fact_comment,
                                   fact_search_hit, fact_video_restaurant_mention
                                   agg_restaurant_ranking  ─► app page "🏆 Xếp hạng quán ăn"
```

| Layer | Table | Grain | Load |
| --- | --- | --- | --- |
| Bronze | `videos_raw`, `channels_raw`, `comments_raw`, `search_results_raw` | 1 row per source row per read | Append, watermark on `COALESCE(updated_at, created_at)` (`fetched_at` for search results); new source columns are added automatically |
| Silver | `stg_videos`, `stg_channels` | id × `snapshot_date` (Vietnam day) | MERGE on the grain (latest Bronze read wins) |
| Silver | `stg_comments` / `stg_search_results` | `comment_id` / `id` | MERGE / insert-only MERGE |
| Silver | `stg_video_transcripts` | `video_id` | Gemini API reads the YouTube URL (free tier, ≤ 7 h video / day, videos ≤ 20 min); insert-only cache, `[không có lời nói]` when the video has no speech |
| Silver | `stg_restaurant_mentions` | `video_id` × normalized restaurant name | `ai_query` extraction from title + description + transcript, only for new / changed videos or when a transcript arrives (`stg_restaurant_extraction_log`) |
| Gold | `dim_date` | `date_key` (yyyymmdd) | `CREATE OR REPLACE`, 2020–2030 + VN holiday seed |
| Gold | `dim_video`, `dim_channel`, `dim_commenter`, `dim_keyword` | natural key | SCD 1 MERGE guarded by `record_checksum`; key = `xxhash64(natural key)`, Unknown = −1 |
| Silver | `stg_restaurant_place_details` | `place_id` | Google Place Details (rating, reviews, price range, primary type, photo) of geocoded places, re-read every 30 days, max 900 calls per month (free Enterprise tier) |
| Gold | `dim_restaurant` | one row per version | SCD 2 on address / location; name, `cuisine`, `price_band`, `google_rating`, `google_rating_count` are type 1; dedup = Google `place_id`, else normalized name + district |
| Gold | `fact_video_daily_snapshot`, `fact_channel_daily_snapshot` | id × snapshot day | `replaceWhere` from the last loaded day; counts semi-additive, `*_gained` additive |
| Gold | `fact_comment` / `fact_search_hit` | comment / run × keyword × video | MERGE / insert-only MERGE |
| Gold | `fact_video_restaurant_mention` | video × restaurant | Full-sync MERGE; `mention_weight = 1 / restaurants in the video` |
| Gold | `agg_restaurant_ranking` | restaurant | `CREATE OR REPLACE` daily; split credit, `ranking_score = 0.7 × views gained 30d + 0.3 × like rate` (min-max within the city); `legit_score` 0–100 from Bayesian Google rating, review count, distinct channels, like rate, views 30d + `legit_label` |

`src/notebooks/gold/checks` fails the run on duplicate grains, missing Unknown rows, orphan or NULL fact keys, counters in dimensions, missing snapshot days, and `SUM(mention_weight) ≠ 1`.

### Daily job

Job **"Project 1 — YouTube restaurants medallion (daily)"** (defined in `resources/project1_daily.job.yml`), serverless, 01:00 `Asia/Ho_Chi_Minh`, emails on failure. Each step depends on the previous one, so a failure stops everything after it:

```
refresh_daily → bronze_ingest → stg_channels / stg_videos / stg_comments / stg_search_results
  → gold_dims → stg_video_transcripts → stg_restaurant_mentions → stg_restaurant_geocode
  → stg_restaurant_place_details
  → gold_facts + gold_restaurants → gold_checks → gold_agg_ranking
```

Guards: `refresh_daily.py` reads everything first and stops **before writing** if the API returns 0 rows or more than 20% zero counters; all writes are atomic (MERGE / `replaceWhere` / `CREATE OR REPLACE`); every date key uses Vietnam time.

The refresh step needs secrets in scope `youtube`: `pg_password` (Postgres role `admin`) and `api_key` (YouTube Data API v3). Job parameter `dry_run=true` calls the API and runs the guard without writing.

To change the task list, edit `resources/project1_daily.job.yml`, then `bundle validate` / `plan` / `deploy`.

### Run the refresh locally

```bash
cd src/app
python refresh_daily.py --dry-run   # call the API, run the guard, write nothing
python refresh_daily.py             # write to Postgres
```

## YouTube API notes

- Works with an API key: `search.list`, `videos.list`, `channels.list`, `commentThreads.list`, `videoCategories.list`.
- `captions.list` needs OAuth2, so restaurant info is extracted from the video title / description instead.
- `videos.list` / `channels.list` cost 1 unit per 50 ids, so the daily refresh stays far below the 10,000 units/day quota.

## Known limits

- `cuisine` / `price_band` come only from Google Places: restaurants Google did not match, or whose only type is `restaurant`, show `Chưa rõ`.
- YouTube has no share count, so `legit_score` does not use shares.
- Snapshot history before 2026-10-26 is sparse (no daily refresh yet), so the first gains cover more than one day.
- `dim_keyword.dish` / `city` are NULL until keyword parsing is built; `search_order` / `result_rank` are NULL for searches before 2026-10-26.
