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