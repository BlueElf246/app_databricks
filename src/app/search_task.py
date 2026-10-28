"""Headless keyword search — the UI "Tìm kiếm" button without Streamlit.

Runs the same flow as the search button in app.py: call the YouTube search
API, pull full video details, fetch the channels, then upsert videos +
channels and append the search-discovery rows to Postgres. Use it from a
shell, a cron job, or a Databricks job task.

Usage:
    python search_task.py "quán ăn Hồ Chí Minh"
    python search_task.py "phở ngon Sài Gòn" --max-results 25 --order viewCount
    python search_task.py "bún bò" --published-after 2026-01-01 --dry-run

It reads config the same way the app does: .env (local) or Databricks
secrets (youtube/api_key) at runtime, plus the PG* vars for Postgres.
"""
import argparse
from datetime import date, datetime

from database import (
    log_search_results,
    upsert_channels,
    upsert_videos,
)
from youtube_client import (
    YouTubeClientError,
    get_channels,
    get_video_details,
    search_videos,
)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def run_search(
    query: str,
    max_results: int = 10,
    order: str = "relevance",
    published_after: date | None = None,
    published_before: date | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Search YouTube and (unless dry_run) persist the results. Returns counts."""
    query = query.strip()
    if not query:
        raise ValueError("query is empty")

    data = search_videos(
        query=query,
        max_results=max_results,
        order=order,
        published_after=published_after,
        published_before=published_before,
    )

    items = data.get("items", [])
    if not items:
        print(f'no results for "{query}"')
        return {"videos": 0, "channels": 0, "search_rows": 0}

    # search.list is discovery-only (snippet, no statistics): log it, then pull
    # full details per video from videos.list, exactly like the UI does.
    search_entries: list[dict] = []
    video_ids: list[str] = []
    for rank, item in enumerate(items, start=1):
        video_id = item.get("id", {}).get("videoId")
        snippet = item.get("snippet", {})
        if not video_id:
            continue
        video_ids.append(video_id)
        search_entries.append(
            {
                "video_id": video_id,
                "channel_id": snippet.get("channelId"),
                "title": snippet.get("title"),
                "published_at": snippet.get("publishedAt"),
                "search_keyword": query,
                "search_order": order,
                "result_rank": rank,
            }
        )

    details = get_video_details(video_ids)
    videos = [details[vid] for vid in video_ids if vid in details]
    for video in videos:
        video["video_url"] = f"https://www.youtube.com/watch?v={video['video_id']}"

    channel_ids = list({v["channel_id"] for v in videos if v.get("channel_id")})
    channels = get_channels(channel_ids)

    print(
        f'"{query}" (order={order}): '
        f"{len(search_entries)} hits, {len(videos)} videos, {len(channels)} channels"
    )

    if dry_run:
        print("dry run: nothing written")
        return {
            "videos": len(videos),
            "channels": len(channels),
            "search_rows": len(search_entries),
        }

    upsert_channels(list(channels.values()))
    upsert_videos(videos)
    log_search_results(search_entries)
    print(f"written: {len(channels)} channels, {len(videos)} videos, {len(search_entries)} search rows")

    return {
        "videos": len(videos),
        "channels": len(channels),
        "search_rows": len(search_entries),
    }


def main(argv: list[str] | None = None) -> dict[str, int]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", help="search keyword, e.g. \"quán ăn Hồ Chí Minh\"")
    parser.add_argument("--max-results", type=int, default=10, help="1-50 (default 10)")
    parser.add_argument(
        "--order",
        choices=["relevance", "date", "viewCount"],
        default="relevance",
        help="sort order sent to the API (default relevance)",
    )
    parser.add_argument("--published-after", help="YYYY-MM-DD")
    parser.add_argument("--published-before", help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="call the API but write nothing")
    args = parser.parse_args(argv)

    try:
        return run_search(
            query=args.query,
            max_results=args.max_results,
            order=args.order,
            published_after=_parse_date(args.published_after),
            published_before=_parse_date(args.published_before),
            dry_run=args.dry_run,
        )
    except YouTubeClientError as error:
        raise SystemExit(f"YouTube API error: {error.detail}")


if __name__ == "__main__":
    main()
