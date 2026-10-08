import os
from typing import Any

import psycopg
from dotenv import load_dotenv

load_dotenv()

# --- Lakebase Postgres connection (configure via env vars) ---
PGHOST = os.environ.get("PGHOST", "your-lakebase-endpoint.database.us-east-2.cloud.databricks.com")
PGDATABASE = os.environ.get("PGDATABASE", "databricks_postgres")
PGUSER = os.environ.get("PGUSER", "your_postgres_user")
PGPASSWORD = os.environ.get("PGPASSWORD", "YOUR_POSTGRES_PASSWORD")
PGPORT = int(os.environ.get("PGPORT", "5432"))
PGSSLMODE = os.environ.get("PGSSLMODE", "require")

APP_SCHEMA = os.environ.get("APP_SCHEMA", "public")


def get_connection() -> psycopg.Connection:
    """Create a psycopg connection to Lakebase Postgres."""
    conn = psycopg.connect(
        host=PGHOST,
        dbname=PGDATABASE,
        user=PGUSER,
        password=PGPASSWORD,
        port=PGPORT,
        sslmode=PGSSLMODE,
    )
    return conn


def _try_execute(cur: psycopg.Cursor, sql: str) -> None:
    """Run a schema-migration statement; skip it if we don't own the target table.

    videos pre-dates the app's dedicated Postgres role and may be owned by a
    different user (e.g. whoever created it by hand), in which case ALTER TABLE /
    CREATE INDEX on it raises InsufficientPrivilege. Skipping keeps the app usable
    with the old column shape until an owner runs `ALTER TABLE videos OWNER TO ...`.
    """
    try:
        cur.execute(sql)
    except psycopg.errors.InsufficientPrivilege:
        pass


def init_db() -> None:
    """Create all app tables if they don't exist, and migrate existing ones to the current schema."""
    with get_connection() as conn:
        conn.autocommit = True  # one failed statement must not roll back the others
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{APP_SCHEMA}"')
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id          TEXT PRIMARY KEY,
                    channel_title       TEXT,
                    channel_description TEXT,
                    custom_url          TEXT,
                    country             TEXT,
                    default_language    TEXT,
                    published_at        TIMESTAMPTZ,
                    subscriber_count    BIGINT,
                    view_count          BIGINT,
                    video_count         BIGINT,
                    thumbnail_url       TEXT,
                    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    video_id       TEXT PRIMARY KEY,
                    title          TEXT NOT NULL,
                    description    TEXT,
                    channel_id     TEXT REFERENCES channels (channel_id),
                    channel_title  TEXT,
                    published_at   TIMESTAMPTZ,
                    thumbnail_url  TEXT,
                    video_url      TEXT NOT NULL,
                    category_id    TEXT,
                    tags           TEXT[],
                    duration       TEXT,
                    definition     TEXT,
                    has_caption    BOOLEAN,
                    view_count     BIGINT,
                    like_count     BIGINT,
                    comment_count  BIGINT,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            # BIGINT: some videos already exceed the INTEGER (~2.1B) range on view_count.
            _try_execute(cur, "ALTER TABLE videos ALTER COLUMN view_count TYPE BIGINT")
            _try_execute(cur, "ALTER TABLE videos ALTER COLUMN like_count TYPE BIGINT")
            _try_execute(
                cur,
                "ALTER TABLE videos ALTER COLUMN published_at TYPE TIMESTAMPTZ "
                "USING published_at::timestamptz",
            )
            _try_execute(cur, "ALTER TABLE videos ADD COLUMN IF NOT EXISTS category_id TEXT")
            _try_execute(cur, "ALTER TABLE videos ADD COLUMN IF NOT EXISTS tags TEXT[]")
            _try_execute(cur, "ALTER TABLE videos ADD COLUMN IF NOT EXISTS duration TEXT")
            _try_execute(cur, "ALTER TABLE videos ADD COLUMN IF NOT EXISTS definition TEXT")
            _try_execute(cur, "ALTER TABLE videos ADD COLUMN IF NOT EXISTS has_caption BOOLEAN")
            _try_execute(cur, "ALTER TABLE videos ADD COLUMN IF NOT EXISTS comment_count BIGINT")
            _try_execute(
                cur,
                "CREATE INDEX IF NOT EXISTS idx_videos_updated_at "
                "ON videos (updated_at DESC)",
            )
            _try_execute(
                cur,
                "CREATE INDEX IF NOT EXISTS idx_videos_view_count "
                "ON videos (view_count DESC)",
            )
            _try_execute(
                cur,
                "CREATE INDEX IF NOT EXISTS idx_videos_like_count "
                "ON videos (like_count DESC)",
            )
            _try_execute(
                cur,
                "CREATE INDEX IF NOT EXISTS idx_videos_channel_id "
                "ON videos (channel_id)",
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS comments (
                    comment_id          TEXT PRIMARY KEY,
                    video_id             TEXT REFERENCES videos (video_id),
                    author_display_name  TEXT,
                    author_channel_id    TEXT,
                    text                 TEXT,
                    like_count           BIGINT,
                    published_at         TIMESTAMPTZ,
                    total_reply_count    INTEGER,
                    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_comments_video_id "
                "ON comments (video_id)"
            )

            # Append-only discovery log: one row per (video, search_keyword) occurrence,
            # kept to analyze which keywords surfaced which videos over time.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS search_results (
                    id             BIGSERIAL PRIMARY KEY,
                    video_id       TEXT,
                    channel_id     TEXT,
                    title          TEXT,
                    published_at   TIMESTAMPTZ,
                    search_keyword TEXT NOT NULL,
                    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_search_results_video_id "
                "ON search_results (video_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_search_results_keyword "
                "ON search_results (search_keyword)"
            )


def upsert_channels(channels: list[dict[str, Any]]) -> None:
    """Insert or update channels in the database."""
    if not channels:
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.executemany(
                """
                INSERT INTO channels (
                    channel_id, channel_title, channel_description, custom_url,
                    country, default_language, published_at, subscriber_count,
                    view_count, video_count, thumbnail_url
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (channel_id) DO UPDATE SET
                    channel_title = EXCLUDED.channel_title,
                    channel_description = EXCLUDED.channel_description,
                    custom_url = EXCLUDED.custom_url,
                    country = EXCLUDED.country,
                    default_language = EXCLUDED.default_language,
                    published_at = EXCLUDED.published_at,
                    subscriber_count = EXCLUDED.subscriber_count,
                    view_count = EXCLUDED.view_count,
                    video_count = EXCLUDED.video_count,
                    thumbnail_url = EXCLUDED.thumbnail_url,
                    updated_at = NOW()
                """,
                [
                    (
                        c["channel_id"],
                        c.get("channel_title"),
                        c.get("channel_description"),
                        c.get("custom_url"),
                        c.get("country"),
                        c.get("default_language"),
                        c.get("published_at"),
                        c.get("subscriber_count"),
                        c.get("view_count"),
                        c.get("video_count"),
                        c.get("thumbnail_url"),
                    )
                    for c in channels
                ],
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
                    video_url, category_id, tags, duration, definition,
                    has_caption, view_count, like_count, comment_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (video_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    channel_id = EXCLUDED.channel_id,
                    channel_title = EXCLUDED.channel_title,
                    published_at = EXCLUDED.published_at,
                    thumbnail_url = EXCLUDED.thumbnail_url,
                    video_url = EXCLUDED.video_url,
                    category_id = EXCLUDED.category_id,
                    tags = EXCLUDED.tags,
                    duration = EXCLUDED.duration,
                    definition = EXCLUDED.definition,
                    has_caption = EXCLUDED.has_caption,
                    view_count = EXCLUDED.view_count,
                    like_count = EXCLUDED.like_count,
                    comment_count = EXCLUDED.comment_count,
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
                        v.get("category_id"),
                        v.get("tags"),
                        v.get("duration"),
                        v.get("definition"),
                        v.get("has_caption"),
                        v.get("view_count"),
                        v.get("like_count"),
                        v.get("comment_count"),
                    )
                    for v in videos
                ],
            )
            conn.commit()


def upsert_comments(comments: list[dict[str, Any]]) -> None:
    """Insert or update comments in the database."""
    if not comments:
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.executemany(
                """
                INSERT INTO comments (
                    comment_id, video_id, author_display_name, author_channel_id,
                    text, like_count, published_at, total_reply_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (comment_id) DO UPDATE SET
                    author_display_name = EXCLUDED.author_display_name,
                    author_channel_id = EXCLUDED.author_channel_id,
                    text = EXCLUDED.text,
                    like_count = EXCLUDED.like_count,
                    published_at = EXCLUDED.published_at,
                    total_reply_count = EXCLUDED.total_reply_count,
                    updated_at = NOW()
                """,
                [
                    (
                        c["comment_id"],
                        c.get("video_id"),
                        c.get("author_display_name"),
                        c.get("author_channel_id"),
                        c.get("text"),
                        c.get("like_count"),
                        c.get("published_at"),
                        c.get("total_reply_count"),
                    )
                    for c in comments
                ],
            )
            conn.commit()


def log_search_results(entries: list[dict[str, Any]]) -> None:
    """Append search-discovery rows (one per video per search run)."""
    if not entries:
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.executemany(
                """
                INSERT INTO search_results (
                    video_id, channel_id, title, published_at, search_keyword
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                [
                    (
                        e.get("video_id"),
                        e.get("channel_id"),
                        e.get("title"),
                        e.get("published_at"),
                        e["search_keyword"],
                    )
                    for e in entries
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