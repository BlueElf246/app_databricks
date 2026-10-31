"""fetch_comments task of the daily job: download the top comments of food review videos.

Before this task, comments were only fetched when someone clicked in the app, so
silver.stg_comments was almost empty. Each video costs one commentThreads.list call
(1 YouTube quota unit, at most 100 top-level comments, order=relevance), capped at
max_videos per run (default 200 units/day). A video is fetched again after refetch_days.
Videos with comment_count = 0 are skipped; comments-disabled videos are recorded and
retried only after refetch_days.
"""
from typing import Any

from database import init_db, record_comment_fetch, upsert_comments, videos_to_fetch_comments
from youtube_client import YouTubeClientError, get_comment_threads, is_quota_error

SOURCE = "fetch_comments"
MAX_COMMENTS_PER_VIDEO = 100  # API maximum per call


def run_fetch_comments(
    video_ids: list[str],
    max_videos: int = 200,
    refetch_days: int = 30,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch comments for up to max_videos of video_ids (food reviews from gold.dim_video). Never raises."""
    if not dry_run:
        init_db()
    todo = videos_to_fetch_comments(video_ids, max_videos, refetch_days)
    print(f"fetch_comments: {len(video_ids)} food review video(s), {len(todo)} to fetch (cap {max_videos})")

    fetched_videos, comments_total, calls, disabled, errors_in_row, stopped = 0, 0, 0, 0, 0, None
    for video in todo:
        video_id = video["video_id"]
        try:
            comments = get_comment_threads(video_id, max_results=MAX_COMMENTS_PER_VIDEO, order="relevance")
            calls += 1
            errors_in_row = 0
        except YouTubeClientError as error:
            calls += 1
            if is_quota_error(error):
                stopped = "youtube quota"
                print("⏸️ YouTube quota used up: stop, the rest runs tomorrow")
                break
            detail = str(error.detail)
            if error.status_code == 403 and "commentsdisabled" in detail.lower():
                disabled += 1
                if not dry_run:
                    record_comment_fetch(video_id, 0, "disabled")
                continue
            if error.status_code == 404:  # video removed or private
                if not dry_run:
                    record_comment_fetch(video_id, 0, "not_found", detail)
                continue
            errors_in_row += 1
            print(f"⚠️ {video_id}: {detail[:300]}")
            if errors_in_row >= 3:
                stopped = "3 errors in a row"
                break
            continue
        fetched_videos += 1
        comments_total += len(comments)
        if not dry_run:
            upsert_comments(comments, source=SOURCE)
            record_comment_fetch(video_id, len(comments), "ok")

    result = {
        "videos_to_fetch": len(todo),
        "videos_fetched": fetched_videos,
        "comments": comments_total,
        "comments_disabled": disabled,
        "api_calls": calls,
        "stopped": stopped,
    }
    print(f"fetch_comments done: {fetched_videos} video(s), {comments_total} comment(s), {calls} unit(s)"
          + (f", {disabled} with comments disabled" if disabled else "")
          + (f", stopped: {stopped}" if stopped else ""))
    if dry_run:
        print("dry run: nothing written")
    return result
