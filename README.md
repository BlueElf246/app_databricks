# YouTube Restaurant Search — Databricks App

Streamlit app tìm kiếm video YouTube review quán ăn, lưu vào Lakebase Postgres, và lấy transcript tiếng Việt.

## Tính năng

- 🔎 Tìm kiếm video YouTube qua YouTube Data API v3
- 💾 Lưu video vào Lakebase Postgres (UPSERT)
- 📝 Lấy transcript tiếng Việt qua proxy (Webshare)
- 📊 Xem danh sách video đã lưu với filters & pagination

## Cấu trúc

```
├── app.py              # Streamlit main page
├── app.yaml            # Databricks App config (env vars)
├── database.py         # Lakebase Postgres connection
├── transcript.py       # YouTube transcript fetcher (proxy + direct download)
├── youtube_client.py   # YouTube Data API v3 client
├── requirements.txt    # Python dependencies
└── pages/
    └── 2_saved_videos.py  # Saved videos page
```

## Setup

### 1. Environment Variables (app.yaml)

```yaml
env:
  - name: YOUTUBE_API_KEY
    value: "YOUR_YOUTUBE_API_KEY"
  - name: PROXY_USER
    value: "YOUR_WEBSHARE_PROXY_USERNAME"
  - name: PROXY_PASS
    value: "YOUR_WEBSHARE_PROXY_PASSWORD"
```

### 2. Lakebase Postgres

Set the following env vars in `database.py` or via app config:
- `PGHOST` — Lakebase Postgres endpoint
- `PGDATABASE` — Database name
- `PGUSER` — Postgres username
- `PGPASSWORD` — Postgres password

### 3. Install & Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Transcript Approach

YouTube blocks cloud IPs from scraping transcripts. This app uses a two-step approach:
1. **Proxy** (Webshare) scrapes the YouTube watch page to get the signed caption URL
2. **Direct download** (no proxy) fetches the caption XML from the signed URL

Signed URLs are not IP-restricted, so step 2 works from any IP.


------
# Handoff: Project 1 — YouTube API ingestion pipeline (food/restaurant review verification app)

Notion: Project 1 → tasks "Explore API" (done), "Test API" (done), "Desgin database" (in progress)

## Confirmed working endpoints (API key auth)
- `search.list`
- `videos.list`
- `channels.list`
- `commentThreads.list`
- `captions.list` — needs OAuth2, **not** usable with API key. Workaround: extract restaurant info from video description + comments instead.

## Recommended table / field mapping
(also written to the Notion "Desgin database" page)

### dim_channel — from channels.list
| col_name | data_type | source JSON path |
|---|---|---|
| channel_id | string | items[].id |
| channel_title | string | items[].snippet.title |
| channel_description | string | items[].snippet.description |
| custom_url | string | items[].snippet.customUrl |
| country | string | items[].snippet.country |
| default_language | string | items[].snippet.defaultLanguage |
| published_at | timestamp | items[].snippet.publishedAt |
| subscriber_count | bigint | items[].statistics.subscriberCount |
| view_count | bigint | items[].statistics.viewCount |
| video_count | int | items[].statistics.videoCount |
| thumbnail_url | string | items[].snippet.thumbnails.high.url |

### fact_video — from videos.list
| col_name | data_type | source JSON path |
|---|---|---|
| video_id | string | items[].id |
| channel_id | string | items[].snippet.channelId (FK → dim_channel) |
| title | string | items[].snippet.title |
| description | string | items[].snippet.description (raw text — restaurant name/address/price extracted downstream) |
| published_at | timestamp | items[].snippet.publishedAt |
| category_id | string | items[].snippet.categoryId |
| tags | array<string> | items[].snippet.tags |
| duration | string | items[].contentDetails.duration (ISO 8601) |
| definition | string | items[].contentDetails.definition (hd/sd) |
| has_caption | boolean | items[].contentDetails.caption |
| view_count | bigint | items[].statistics.viewCount |
| like_count | bigint | items[].statistics.likeCount |
| comment_count | bigint | items[].statistics.commentCount |
| thumbnail_url | string | items[].snippet.thumbnails.high.url |

### fact_comment — from commentThreads.list
| col_name | data_type | source JSON path |
|---|---|---|
| comment_id | string | items[].snippet.topLevelComment.id |
| video_id | string | items[].snippet.videoId (FK → fact_video) |
| author_display_name | string | ...topLevelComment.snippet.authorDisplayName |
| author_channel_id | string | ...topLevelComment.snippet.authorChannelId.value |
| text_display | string | ...topLevelComment.snippet.textDisplay |
| like_count | bigint | ...topLevelComment.snippet.likeCount |
| published_at | timestamp | ...topLevelComment.snippet.publishedAt |
| total_reply_count | int | items[].snippet.totalReplyCount |

### stg_search_result — from search.list (staging/discovery only, not retained long-term)
| col_name | data_type | source JSON path |
|---|---|---|
| video_id | string | items[].id.videoId |
| channel_id | string | items[].snippet.channelId |
| title | string | items[].snippet.title |
| published_at | timestamp | items[].snippet.publishedAt |
| search_keyword | string | input param (log the query that produced this row) |

**Grain:** dim_channel = 1 row/channel. fact_video = 1 row/video (FK channel_id). fact_comment = 1 row/top-level comment (FK video_id). Matches MVP phase order (videos + channels first, comments later).

**Naming convention** (dim_/fact_/stg_) borrowed from the `data-testing-datdev` repo's docs style for reference only — not the actual codebase.

## Links
- Notion "Desgin database" task: https://app.notion.com/p/3f3053b1b7ea805f9484c281a375efcf

## Note
This file lives in the `data-testing-datdev` repo (Windows), which is **unrelated** to Project 1. The real Project 1 codebase is on WSL at `/home/datlm11/app_databricks`. Copy this file's content over manually (or re-save it there) when you start the session in that directory — there is no automated sync between the two.
