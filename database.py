import os
from typing import Any

import psycopg

# --- Lakebase Postgres connection (configure via env vars) ---
PGHOST = os.environ.get("PGHOST", "your-lakebase-endpoint.database.us-east-2.cloud.databricks.com")
PGDATABASE = os.environ.get("PGDATABASE", "databricks_postgres")
PGUSER = os.environ.get("PGUSER", "your_postgres_user")
PGPASSWORD = os.environ.get("PGPASSWORD", "YOUR_POSTGRES_PASSWORD")
PGPORT = int(os.environ.get("PGPORT", "5432"))

APP_SCHEMA = os.environ.get("APP_SCHEMA", "public")


def get_connection() -> psycopg.Connection:
    """Create a psycopg connection to Lakebase Postgres."""
    conn = psycopg.connect(
        host=PGHOST,
        dbname=PGDATABASE,
        user=PGUSER,
        password=PGPASSWORD,
        port=PGPORT,
        sslmode="require",
    )
    return conn


def init_db() -> None:
    """Create the videos table if it doesn't exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{APP_SCHEMA}"')
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    video_id       TEXT PRIMARY KEY,
                    title          TEXT NOT NULL,
                    description    TEXT,
                    channel_id     TEXT,
                    channel_title TEXT,
                    published_at   TEXT,
                    thumbnail_url  TEXT,
                    video_url      TEXT NOT NULL,
                    view_count     INTEGER,
                    like_count    INTEGER,
                    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.commit()


def upsert_videos(videos: list[dict[str, Any]]) -> None:
    """Insert or update videos in the database."""
    if not videos:
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.executemany(
                """
                INSERT INTO videos (
                    video_id, title, description, channel_id,
                    channel_title, published_at, thumbnail_url,
                    video_url, view_count, like_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (video_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    channel_id = EXCLUDED.channel_id,
                    channel_title = EXCLUDED.channel_title,
                    published_at = EXCLUDED.published_at,
                    thumbnail_url = EXCLUDED.thumbnail_url,
                    video_url = EXCLUDED.video_url,
                    view_count = EXCLUDED.view_count,
                    like_count = EXCLUDED.like_count,
                    updated_at = NOW()
                """,
                [
                    (
                        v["video_id"],
                        v.get("title"),
                        v.get("description"),
                        v.get("channel_id"),
                        v.get("channel_title"),
                        v.get("published_at"),
                        v.get("thumbnail_url"),
                        v.get("video_url"),
                        v.get("view_count"),
                        v.get("like_count"),
                    )
                    for v in videos
                ],
            )
            conn.commit()


def get_saved_videos(
    page: int = 1,
    page_size: int = 20,
    min_view_count: int | None = None,
    min_like_count: int | None = None,
    keyword: str | None = None,
    sort_by: str = "updated_at",
    sort_order: str = "desc",
) -> dict[str, Any]:
    """Query saved videos with pagination and filters."""
    conditions: list[str] = []
    params: list[Any] = []

    if min_view_count is not None:
        conditions.append("view_count >= %s")
        params.append(min_view_count)

    if min_like_count is not None:
        conditions.append("like_count >= %s")
        params.append(min_like_count)

    if keyword:
        conditions.append("(title ILIKE %s OR description ILIKE %s)")
        pattern = f"%{keyword}%"
        params.extend([pattern, pattern])

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    allowed_sort_columns = {
        "updated_at": "updated_at",
        "view_count": "view_count",
        "like_count": "like_count",
        "title": "title",
    }
    sort_column = allowed_sort_columns.get(sort_by, "updated_at")
    sort_direction = "asc" if sort_order.lower() == "asc" else "desc"
    offset = (page - 1) * page_size

    count_query = f"SELECT COUNT(*) FROM videos {where_clause}"
    data_query = (
        f"SELECT * FROM videos {where_clause} "
        f"ORDER BY {sort_column} {sort_direction} "
        f"LIMIT %s OFFSET %s"
    )
    data_params = [*params, page_size, offset]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.execute(count_query, params)
            total = cur.fetchone()[0]

            cur.execute(data_query, data_params)
            columns = [desc.name for desc in cur.description]
            rows = cur.fetchall()

    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "items": [dict(zip(columns, row)) for row in rows],
    }