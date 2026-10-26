"""Daily refresh: re-read every saved video and channel from the YouTube API.

The app only updates a video's counters when someone searches it, so most
videos would have no snapshot on most days. Run this once a day, before the
Analytic/stg notebooks, so that stg_videos / stg_channels get one row per
video / channel per day.

Empty-source guard: everything is read from the API first and checked before
anything is written. If the API returns no rows, or more than 20% of the rows
have a zero counter, the run stops (exit code 1) and writes nothing, so a bad
API day never lands as a day of zeros.

Usage:
    python refresh_daily.py            # refresh and write to Postgres
    python refresh_daily.py --dry-run  # call the API, print counts, write nothing
"""
import argparse

from database import APP_SCHEMA, get_connection, upsert_channels, upsert_videos
from youtube_client import get_channels, get_video_details

BATCH_SIZE = 50  # max ids per videos.list / channels.list call
MAX_ZERO_COUNTER_SHARE = 0.20


class EmptySourceError(RuntimeError):
    """The API answer looks wrong; nothing was written."""


def _saved_ids(table: str, column: str) -> list[str]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.execute(f"SELECT {column} FROM {table} ORDER BY {column}")
            return [row[0] for row in cur.fetchall()]


def _batches(ids: list[str]) -> list[list[str]]:
    return [ids[i : i + BATCH_SIZE] for i in range(0, len(ids), BATCH_SIZE)]


def fetch_channels() -> list[dict]:
    channel_ids = _saved_ids("channels", "channel_id")
    channels = []
    for batch in _batches(channel_ids):
        channels.extend(get_channels(batch).values())
    print(f"channels: {len(channels)}/{len(channel_ids)} returned by the API, {len(channel_ids) - len(channels)} missing")
    return channels


def fetch_videos() -> list[dict]:
    video_ids = _saved_ids("videos", "video_id")
    videos = []
    for batch in _batches(video_ids):
        details = get_video_details(batch)
        for vid in batch:
            if vid in details:
                video = details[vid]
                video["video_url"] = f"https://www.youtube.com/watch?v={video['video_id']}"
                videos.append(video)
    # Deleted or private videos are not returned; their last snapshot is kept as is.
    print(f"videos: {len(videos)}/{len(video_ids)} returned by the API, {len(video_ids) - len(videos)} missing")
    return videos


def check_source(name: str, rows: list[dict], counter: str) -> None:
    if not rows:
        raise EmptySourceError(f"{name}: the API returned 0 rows")
    zeros = sum(1 for row in rows if not row.get(counter))
    share = zeros / len(rows)
    print(f"{name}: {zeros}/{len(rows)} rows with {counter} = 0 ({share:.0%})")
    if share > MAX_ZERO_COUNTER_SHARE:
        raise EmptySourceError(f"{name}: {share:.0%} of rows have {counter} = 0 (limit {MAX_ZERO_COUNTER_SHARE:.0%})")


def main(argv: list[str] | None = None) -> dict[str, int]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="call the API but write nothing")
    args = parser.parse_args(argv)

    channels = fetch_channels()
    videos = fetch_videos()
    check_source("channels", channels, "subscriber_count")
    check_source("videos", videos, "view_count")

    if args.dry_run:
        print("dry run: nothing written")
    else:
        # Channels first: videos.channel_id references channels.
        for batch in _batches(channels):
            upsert_channels(batch)
        for batch in _batches(videos):
            upsert_videos(batch)
        print(f"written: {len(channels)} channels, {len(videos)} videos")
    return {"channels": len(channels), "videos": len(videos)}


if __name__ == "__main__":
    main()
