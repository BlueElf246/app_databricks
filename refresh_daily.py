"""Daily refresh: re-read every saved video and channel from the YouTube API.

The app only updates a video's counters when someone searches it, so most
videos would have no snapshot on most days. Run this once a day, before the
Analytic/stg notebooks, so that stg_videos / stg_channels get one row per
video / channel per day.

Usage:
    python refresh_daily.py            # refresh and write to Postgres
    python refresh_daily.py --dry-run  # call the API, print counts, write nothing
"""
import argparse

from database import APP_SCHEMA, get_connection, upsert_channels, upsert_videos
from youtube_client import get_channels, get_video_details

BATCH_SIZE = 50  # max ids per videos.list / channels.list call


def _saved_ids(table: str, column: str) -> list[str]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.execute(f"SELECT {column} FROM {table} ORDER BY {column}")
            return [row[0] for row in cur.fetchall()]


def _batches(ids: list[str]) -> list[list[str]]:
    return [ids[i : i + BATCH_SIZE] for i in range(0, len(ids), BATCH_SIZE)]


def refresh_channels(dry_run: bool) -> None:
    channel_ids = _saved_ids("channels", "channel_id")
    refreshed = 0
    for batch in _batches(channel_ids):
        channels = get_channels(batch)
        refreshed += len(channels)
        if not dry_run:
            upsert_channels(list(channels.values()))
    missing = len(channel_ids) - refreshed
    print(f"channels: {refreshed}/{len(channel_ids)} refreshed, {missing} not returned by the API")


def refresh_videos(dry_run: bool) -> None:
    video_ids = _saved_ids("videos", "video_id")
    refreshed = 0
    for batch in _batches(video_ids):
        details = get_video_details(batch)
        videos = [details[vid] for vid in batch if vid in details]
        for video in videos:
            video["video_url"] = f"https://www.youtube.com/watch?v={video['video_id']}"
        refreshed += len(videos)
        if not dry_run:
            upsert_videos(videos)
    # Deleted or private videos are not returned; their last snapshot is kept as is.
    missing = len(video_ids) - refreshed
    print(f"videos: {refreshed}/{len(video_ids)} refreshed, {missing} not returned by the API")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="call the API but write nothing")
    args = parser.parse_args()

    # Channels first: videos.channel_id references channels.
    refresh_channels(args.dry_run)
    refresh_videos(args.dry_run)


if __name__ == "__main__":
    main()
