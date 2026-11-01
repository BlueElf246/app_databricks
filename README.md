# YouTube Restaurant Search — Databricks App + Kimball medallion pipeline

Streamlit app tìm kiếm video YouTube review quán ăn, lưu vào Lakebase Postgres, và một pipeline Bronze → Silver → Gold (Kimball) trên Databricks để xếp hạng quán ăn theo dữ liệu YouTube.

## Tính năng

- 🔎 Tìm video YouTube review quán ăn (YouTube Data API v3) và tìm quán trên Google Maps (Places API New), lưu vào Lakebase Postgres
- 🤖 Tự động tìm mỗi ngày (`auto_search`): ~20 keyword YouTube, video mới của 100 kênh ẩm thực đang theo dõi, tối đa 15 keyword Google Maps
- 💬 Lấy top 100 bình luận của video review (≤ 200 video / ngày) và phân tích cảm xúc bằng LLM
- 🗺️ Bổ sung quán từ OpenStreetMap (1 lần / tuần) và từ Google Place Details (rating, giá, loại món, review)
- 🏆 Xếp hạng quán (`agg_restaurant_ranking`: điểm hot 7/30 ngày + `legit_score`), dashboard AI/BI "Tìm quán ăn ngon", trang app "Xếp hạng quán ăn"
- 📦 Job, app, dashboard, schemas deploy bằng một Databricks Asset Bundle (`databricks.yml`); cloud thread của Claude deploy dev bằng service principal `claude-deploy`

## Cấu trúc

```
├── databricks.yml                     # Bundle: variables + targets dev / prod (+ quyền của claude-deploy ở dev)
├── resources/
│   ├── project1_daily.job.yml         # Daily medallion job (25 tasks)
│   ├── project1_search.job.yml        # On-demand search job (1 keyword, YouTube hoặc Google Places)
│   ├── project1_stats_refresh.job.yml # Refresh view / like / comment lúc 07/13/21h (7 tasks)
│   ├── restaurant_finder.dashboard.yml# AI/BI dashboard trên Gold
│   ├── youtube_search.app.yml         # Streamlit app + resources (secret, SQL warehouse, search job)
│   └── medallion.schemas.yml          # bronze / silver / gold schemas
├── src/
│   ├── app/                           # Databricks App (Streamlit) + code dùng chung với job
│   │   ├── app.py, pages/             # Search, Saved videos, Xếp hạng quán ăn, Search ingest
│   │   ├── youtube_client.py, places_client.py, search_task.py, comments_task.py, keywords.py
│   │   ├── refresh_daily.py           # Đọc lại video / kênh đã lưu mỗi ngày (empty-source guard)
│   │   └── database.py, gold_client.py
│   ├── dashboards/restaurant_finder.lvdash.json
│   └── notebooks/
│       ├── refresh/                   # auto_search, run_refresh, fetch_comments, run_search
│       ├── bronze/                    # ingest_raw (Lakebase → *_raw), osm_ingest (Overpass)
│       ├── stg/                       # Silver: stg_* + LLM (classification, mentions, sentiment, transcripts) + Google (geocode, place details)
│       ├── gold/                      # dims, facts, restaurants, checks, agg_*, run_summary
│       └── landing/                   # Old raw ingest (reference only)
└── scripts/
    ├── run_search_job.py              # Chạy job search từ chat ("tìm quán X")
    ├── run_daily_job.py               # Chạy daily job end to end, in trạng thái từng task + run_summary
    └── ask_restaurants.py             # Trả lời "hôm nay ăn gì ở quận X?" từ Gold (read-only)
```

## Deploy (Databricks Asset Bundle)

Hướng dẫn chi tiết (setup, secrets, deploy, run, troubleshooting): [docs/BUNDLE_MANUAL.md](docs/BUNDLE_MANUAL.md).

Workspace `https://dbc-66b44822-7fe7.cloud.databricks.com` (AWS), Databricks CLI ≥ 0.270.

| Target | Schemas | Job | Ghi vào Postgres | Root path |
| --- | --- | --- | --- | --- |
| `dev` (default) | `prod.dev_bronze` / `prod.dev_silver` / `prod.dev_gold` | `[dev] Project 1 — …`, 01:00 Asia/Ho_Chi_Minh, **đang bật** | có (`dry_run=false`) | `/Workspace/Users/lemanhdat2112@gmail.com/.bundle/project1_youtube_restaurants/dev` (cố định) |
| `prod` | `prod.bronze` / `prod.silver` / `prod.gold` | `Project 1 — …` (id 807264924625256), schedule paused | có | `~/.bundle/project1_youtube_restaurants/prod` |

```bash
databricks bundle validate --strict              # dev
databricks bundle deploy                         # dev
python scripts/run_daily_job.py                  # dev run end to end (--dry-run: không ghi)
databricks bundle plan -t prod                   # review trước khi deploy prod
databricks bundle deploy -t prod
```

- Notebooks đọc tên schema từ job parameters `bronze` / `silver` / `gold`, nên cùng code chạy cho cả hai target.
- Dev và prod dùng **chung một Lakebase Postgres**: một run dev có ghi là ghi dữ liệu thật.
- Prod job, app và schemas có từ trước bundle và đã được bind (`bundle deployment bind`); prod schemas có `prevent_destroy`.

### Ai deploy được dev

| Ai | Cách đăng nhập | Quyền |
| --- | --- | --- |
| datle | CLI profile `dbc-66b44822-7fe7` trên máy | Owner của job / dashboard / app dev; job chạy dưới tên datle |
| `claude-deploy` (SP, app id `10d899a3-…`) | Cloud environment `databricks_deploy` (Network secret OAuth client credentials, proxy gắn token; CLI cài bằng setup script) | CAN_MANAGE trên thư mục bundle, job, dashboard, app dev (khai báo ở `targets.dev.permissions`); USE CATALOG `prod`, USE SCHEMA + MANAGE trên `dev_*`; CAN_MANAGE warehouse; MANAGE scope `youtube`. Không đọc dữ liệu |
| `claude-search` (SP) | Cloud environment `run_search` (chỉ `/api/2.2/jobs/`) | CAN_MANAGE_RUN job search + daily (dev); SELECT `gold` / `dev_gold`. Không deploy được |

Vì vậy `root_path`, `app_name`, `notification_email` của dev được cố định theo datle: ai deploy cũng cập nhật đúng một bộ resource. Sau này GitHub Actions dùng lại `claude-deploy`.

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
YouTube API ─► app / search job / auto_search ─┐
Google Places ─► search job / auto_search ─────┤
            └► refresh_daily.py, fetch_comments ┴─► Lakebase Postgres public.* (latest values only)
OpenStreetMap (Overpass, weekly) ─► bronze.osm_pois_raw ─► silver.stg_osm_restaurants
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
| Silver | `stg_video_classification` | `video_id` | `ai_query`: restaurant_review / food_other / not_food; not_food bị bỏ qua ở các bước sau |
| Silver | `stg_places` | `place_id` | Google Text Search (search job + auto_search mode places) → quán chỉ có trên Google |
| Silver | `stg_osm_restaurants` | OSM element | Quán ăn / cafe ở TP.HCM từ OpenStreetMap; bổ sung cuisine, giờ mở cửa cho `dim_restaurant` |
| Silver | `stg_comment_sentiment` | `comment_id` | `ai_query`: positive / negative / neutral + có nói về món ăn không |
| Silver | `stg_restaurant_place_reviews` | `review_id` | ≤ 5 Google review mỗi lần đọc Place Details, tích lũy qua các lần đọc lại |
| Silver | `stg_place_rating_reads` | `place_id` × `read_at` × `source` | Mọi lần đọc rating Google (Place Details + Text Search thủ công), append-only, không ghi đè: lịch sử rating |
| Gold | `dim_date` | `date_key` (yyyymmdd) | `CREATE OR REPLACE`, 2020–2030 + VN holiday seed |
| Gold | `dim_video`, `dim_channel`, `dim_commenter`, `dim_keyword` | natural key | SCD 1 MERGE guarded by `record_checksum`; key = `xxhash64(natural key)`, Unknown = −1 |
| Silver | `stg_restaurant_place_details` | `place_id` | Google Place Details (rating, reviews, price range, primary type, website, phone, opening hours, photo) of geocoded places (re-read every 30 days), then the top 20 by `legit_score` (re-read every 7 days, rating history), then of Google-only places that auto_search found with Text Search Pro (≤ 24 / day, most-found first, re-read every 90 days); max 900 calls per month (free Enterprise tier) |
| Gold | `dim_restaurant` | one row per version | SCD 2 on address / location; name, `cuisine`, `price_band`, `google_rating`, `google_rating_count` are type 1; dedup = Google `place_id`, else normalized name + district |
| Gold | `fact_video_daily_snapshot`, `fact_channel_daily_snapshot` | id × snapshot day | `replaceWhere` from the last loaded day; counts semi-additive, `*_gained` additive |
| Gold | `fact_restaurant_rating_daily_snapshot` | place × snapshot day | `CREATE OR REPLACE` from `stg_place_rating_reads`; dense (last read carried forward); `rating` / `rating_count` semi-additive, `rating_change` / `rating_count_gained` additive, `days_since_read` |
| Gold | `fact_comment` / `fact_search_hit` | comment / run × keyword × video | MERGE / insert-only MERGE |
| Gold | `fact_video_restaurant_mention` | video × restaurant | Full-sync MERGE; `mention_weight = 1 / restaurants in the video` |
| Gold | `agg_restaurant_ranking` | restaurant | `CREATE OR REPLACE` daily; split credit, `ranking_score = 0.7 × views gained 30d + 0.3 × like rate` (min-max within the city); `legit_score` 0–100 from Bayesian Google rating, review count, distinct channels, like rate, views 30d + `legit_label` |

Thêm vào Gold: `fact_google_review` (review × quán), `agg_restaurant_daily_views` (views mỗi ngày của quán, cho trending 7 ngày), `agg_restaurant_comments` (cảm xúc comment theo quán). `agg_restaurant_ranking` có thêm cột 7 ngày và `data_source` (YouTube + Google / Chỉ YouTube / Chỉ Google).

`src/notebooks/gold/checks` fails the run on duplicate grains, missing Unknown rows, orphan or NULL fact keys, counters in dimensions, missing snapshot days, and `SUM(mention_weight) ≠ 1`.

### Daily job

Job **"[dev] Project 1 — YouTube restaurants medallion (daily)"** (`resources/project1_daily.job.yml`), serverless, 01:00 `Asia/Ho_Chi_Minh`, email khi lỗi, 27 tasks. Một task lỗi thì các task phía sau dừng:

```
auto_search → refresh_daily → fetch_comments → bronze_ingest
osm_ingest → stg_osm_restaurants  (song song; chỉ gọi Overpass 1 lần / tuần)
bronze_ingest → stg_channels / stg_videos / stg_comments / stg_search_results / stg_places
stg_videos → stg_video_classification → gold_dims → gold_facts
gold_dims → stg_video_transcripts → stg_restaurant_mentions → stg_restaurant_geocode → stg_restaurant_place_details → stg_place_rating_reads
stg_place_rating_reads + gold_restaurants → gold_rating_snapshot → run_summary
gold_restaurants (dims + place details + places + OSM) → gold_checks → gold_agg_daily_views
stg_comment_sentiment → gold_agg_comments
gold_agg_daily_views + gold_agg_comments → gold_agg_ranking → run_summary
```

Guards: `refresh_daily.py` dừng **trước khi ghi** nếu API trả 0 dòng hoặc > 20% counter = 0; mọi lần ghi là atomic (MERGE / `replaceWhere` / `CREATE OR REPLACE`); mọi date key theo giờ Việt Nam. Các task gọi API ngoài (auto_search, fetch_comments, geocode, place details, transcripts) **không fail** khi hết quota: gặp HTTP 429 là dừng gọi, phần còn lại chờ run sau.

### Stats refresh trong ngày

Job **"[dev] Project 1 — YouTube stats refresh (07/13/21h)"** (`resources/project1_stats_refresh.job.yml`), 07:00, 13:00, 21:00 `Asia/Ho_Chi_Minh` (01:00 do daily job làm), email khi lỗi, 7 tasks:

```
refresh_daily → bronze_ingest → stg_channels / stg_videos → gold_snapshot_facts (facts.ipynb, facts=snapshots)
→ gold_agg_daily_views → gold_agg_ranking
```

Grain vẫn là video × ngày: mỗi lần chạy ghi đè dòng của hôm nay (lần đọc mới nhất thắng); Bronze giữ mọi lần đọc. Video mới, dims, comment, transcript và checks vẫn chỉ chạy trong daily job (video mới tìm trong ngày có `video_key = -1` cho tới 01:00 hôm sau). Đo ngày 01/11: like / comment đổi gần như mỗi phút, view chỉ đổi theo đợt cách nhau vài giờ, nên không chạy theo giờ.

Secrets: scope `youtube` (`pg_password`, `api_key`), scope `maps` (`api_key`, Places API New). Job parameter `dry_run=true` gọi API nhưng không ghi.

### Nguồn dữ liệu và quota (giữ ở mức miễn phí)

| Nguồn | Task | Giới hạn trong code | Quota / chi phí |
| --- | --- | --- | --- |
| YouTube Data API v3 | auto_search, refresh_daily (01h + stats refresh 07/13/21h), fetch_comments, search job | 20 keyword × 100 unit; 100 kênh; 200 video comment / ngày | Free 10,000 unit / ngày. Run 01/11: ~2,650 unit (auto_search ~2,150, refresh ~280, comments 200); stats refresh thêm ~3 × 280 = ~840 unit / ngày |
| Google Places Text Search Enterprise (đủ trường) | search job (search tay) | 800 call / tháng | Free 1,000 / tháng |
| Google Places Text Search Pro (không rating / giá / giờ / điện thoại / web) | auto_search (places, 40 keyword / ngày), stg_restaurant_geocode | auto ≤ 1,500 / tháng; geocode ≤ 100 / ngày | Free 5,000 / tháng (chung); Place Details bù các trường thiếu |
| Google Place Details (Enterprise + Atmosphere) | stg_restaurant_place_details | ≤ 300 / ngày, ≤ 900 / tháng, đọc lại mỗi 30 ngày (top 20 legit: mỗi 7 ngày) | Free 1,000 / tháng; GCP giới hạn **"GetPlaceRequest per day"** |
| Gemini API (transcript) | stg_video_transcripts | ≤ 20 call / run, ≤ 7 h video / ngày | Free tier; hay gặp 503 / 429 |
| OpenStreetMap Overpass | osm_ingest | 1 query / tuần | Miễn phí |
| Databricks `ai_query` (Qwen3 80B) | stg_video_classification, stg_restaurant_mentions (≤ 150 video / run), stg_comment_sentiment (≤ 5,000 comment / run) | | **Tính phí** (Foundation Model API + serverless), không có free tier |

### Run gần nhất (dev, 01/11/2026)

[Run 687321359620570](https://dbc-66b44822-7fe7.cloud.databricks.com/?o=7474659725199695#job/1097737746449974/run/687321359620570): 25/25 task SUCCESS, ~20 phút.

- Có dữ liệu: 355 video mới từ 20 keyword + 2,468 video mới từ kênh theo dõi; 13,280 comment của 199 video; 5,965 điểm OSM; 3,691 comment được phân tích cảm xúc; 150 video được trích quán; 543 quán trong ranking (3 Legit, 196 Bình thường, 344 Ít dữ liệu).
- Không có dữ liệu mới: Google Places auto (dừng sau 1 keyword), geocode (1 call), Place Details (2 call) đều bị **GCP daily quota** (HTTP 429) vì run lúc 01:00 đã dùng hết hạn mức ngày; `fact_google_review` = 0 dòng. Transcript: 0 (Gemini 503 / 429).

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

- Quota ngày của Places API (100 / ngày) trước 01/11 là do billing account cũ của GCP project bị đóng; đã chuyển sang billing account đang hoạt động, giờ SearchTextRequest per day = 75,000. Giới hạn chi phí thật là các cap theo tháng trong code (bảng trên).
- YouTube "Search Queries per day" = 100 (10,000 unit): không phụ thuộc billing, chỉ tăng được qua form audit của YouTube.
- Transcript (Gemini free tier) thường bị 503 / 429, mới có 40 video có transcript.
- `ai_query` tính phí theo token: là phần duy nhất của run không miễn phí.
- `cuisine` / `price_band` lấy từ Google Places (và cuisine từ OSM): quán Google không match hiển thị `Chưa rõ`.
- Top "hot" có thể lẫn kênh / video không phải quán ăn (ví dụ "MyGear" ngày 01/11, chưa kiểm tra): classifier và extraction chưa lọc hết.
- YouTube không có share count, nên `legit_score` không dùng share.
- `dim_keyword.dish` / `city` NULL cho tới khi có keyword parsing.
