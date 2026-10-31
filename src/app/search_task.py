"""Headless keyword search on YouTube or Google Places, saved to Lakebase Postgres.

mode youtube: the Streamlit "Tìm kiếm" button without Streamlit. Calls the
YouTube search API, pulls full video details and channels, upserts videos +
channels and appends search_results.

mode places: Google Places Text Search (results like a search on Google Maps),
upserts places and appends place_search_results. Every page is one billed
Text Search Enterprise call, so calls are counted per Vietnam month in
search_runs and capped at PLACES_MONTHLY_CALL_LIMIT.

Every run that is not a dry run writes one search_runs row (also when it fails).

Usage:
    python search_task.py "quán ăn Hồ Chí Minh"
    python search_task.py "phở ngon Sài Gòn" --max-results 25 --order viewCount
    python search_task.py "quán sushi" --mode places --max-results 40
    python search_task.py "bún bò" --mode places --dry-run

Config: .env (local) or Databricks secrets (youtube/api_key, maps/api_key),
plus the PG* vars for Postgres.
"""
import argparse
import uuid
from datetime import date, datetime, timezone
from typing import Any

from keywords import record_keyword_run
from database import (
    existing_ids,
    init_db,
    log_place_search_results,
    log_search_results,
    places_calls_this_month,
    record_search_run,
    upsert_channels,
    upsert_places,
    upsert_videos,
)

MODES = ("youtube", "places")
SOURCE = {"youtube": "search_youtube", "places": "search_places"}
AUTO_SOURCE = "search_auto"  # keywords picked by the system (auto_search task of the daily job)
PLACES_MONTHLY_CALL_LIMIT = 800  # under the 1,000 free Text Search Enterprise calls per month (all sources)
AUTO_PLACES_PER_DAY = 15         # auto_search Places keywords per day (1 page = 20 places = 1 call each)
PLACES_PAGE_RESULTS = 20         # one Text Search page
AUTO_PLACES_MONTHLY_LIMIT = 450  # auto_search share of the 800; manual searches keep the rest (~350)
DEFAULT_LOCATION = "10.7769,106.7009,20000"  # Ho Chi Minh City centre, 20 km bias


class QuotaReached(RuntimeError):
    """The Places monthly cap is used up; nothing was called."""


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _youtube(query: str, max_results: int, order: str, published_after, published_before,
             dry_run: bool, run: dict[str, Any]) -> dict[str, int]:
    from youtube_client import get_channels, get_video_details, search_videos

    data = search_videos(
        query=query,
        max_results=max_results,
        order=order,
        published_after=published_after,
        published_before=published_before,
    )
    run["api_calls"] = 1  # search.list; videos/channels.list are cheap and not counted

    items = data.get("items", [])
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
    if not video_ids:
        print(f'no results for "{query}"')
        return {"videos": 0, "channels": 0, "search_rows": 0}

    details = get_video_details(video_ids)
    videos = [details[vid] for vid in video_ids if vid in details]
    for video in videos:
        video["video_url"] = f"https://www.youtube.com/watch?v={video['video_id']}"
    channel_ids = list({v["channel_id"] for v in videos if v.get("channel_id")})
    channels = get_channels(channel_ids)
    run["result_count"] = len(search_entries)
    run["new_results"] = len(set(video_ids) - existing_ids("videos", "video_id", video_ids))

    print(f'"{query}" (order={order}): {len(search_entries)} hits ({run["new_results"]} new), '
          f'{len(videos)} videos, {len(channels)} channels')
    if not dry_run:
        source = run["source"]
        upsert_channels(list(channels.values()), source=source)
        upsert_videos(videos, source=source)
        log_search_results(search_entries, source=source, search_run_id=run["search_run_id"])
        print(f"written: {len(channels)} channels, {len(videos)} videos, {len(search_entries)} search rows")
    top = [
        {"title": v.get("title"), "channel": v.get("channel_title"), "views": v.get("view_count"), "url": v.get("video_url")}
        for v in videos[:10]
    ]
    return {"videos": len(videos), "new_videos": run["new_results"], "channels": len(channels),
            "search_rows": len(search_entries), "top": top}


def _places(query: str, max_results: int, location: str | None, included_type: str | None,
            dry_run: bool, run: dict[str, Any]) -> dict[str, int]:
    from places_client import PAGE_SIZE, parse_location, search_places, to_row

    used = places_calls_this_month()
    budget = PLACES_MONTHLY_CALL_LIMIT - used
    if dry_run:
        budget = min(budget, 1)  # dry runs are not recorded, so they get one page only
    print(f"Places calls this month: {used}/{PLACES_MONTHLY_CALL_LIMIT} · this run may call: {max(budget, 0)}")
    if budget <= 0:
        raise QuotaReached(f"Places monthly cap reached ({used}/{PLACES_MONTHLY_CALL_LIMIT})")

    raw, calls = search_places(
        query,
        max_results=max_results,
        location_bias=parse_location(location),
        included_type=included_type or None,
        max_calls=budget,
    )
    run["api_calls"] = calls
    rows = [to_row(p) for p in raw]
    run["result_count"] = len(rows)
    ids = [row["place_id"] for row in rows]
    run["new_results"] = len(set(ids) - existing_ids("places", "place_id", ids))
    print(f'"{query}": {len(rows)} places in {calls} call(s) (page size {PAGE_SIZE})')
    for rank, row in enumerate(rows[:10], start=1):
        print(f"  {rank:>2}. {row['display_name']} · {row['rating']} ({row['user_rating_count']}) · {row['formatted_address']}")

    if not dry_run and rows:
        source = run["source"]
        upsert_places(rows, source=source)
        log_place_search_results(
            [
                {
                    "place_id": row["place_id"],
                    "search_keyword": query,
                    "result_rank": rank,
                    "rating": row["rating"],
                    "user_rating_count": row["user_rating_count"],
                }
                for rank, row in enumerate(rows, start=1)
            ],
            source=source,
            search_run_id=run["search_run_id"],
        )
        print(f"written: {len(rows)} places, {len(rows)} place_search_results rows")
    top = [
        {k: row[k] for k in ("display_name", "rating", "user_rating_count", "price_level",
                             "formatted_address", "google_maps_uri")}
        for row in rows[:10]
    ]
    return {"places": len(rows), "new_places": run["new_results"], "api_calls": calls, "top": top}


def run_search(
    query: str,
    mode: str = "youtube",
    max_results: int = 10,
    order: str = "relevance",
    published_after: date | None = None,
    published_before: date | None = None,
    location: str | None = DEFAULT_LOCATION,
    included_type: str | None = None,
    dry_run: bool = False,
    databricks_run_id: int | None = None,
    source: str | None = None,
    keyword_origin: str = "manual",
) -> dict[str, int]:
    """Search YouTube or Google Places and (unless dry_run) persist the results. Returns counts."""
    query = query.strip()
    if not query:
        raise ValueError("query is empty")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    if not dry_run:
        init_db()  # creates the new tables / source columns on first use

    params = {"max_results": max_results}
    if mode == "youtube":
        params.update(order=order, published_after=published_after, published_before=published_before)
    else:
        params.update(location=location, included_type=included_type)
    run: dict[str, Any] = {
        "search_run_id": str(uuid.uuid4()),
        "mode": mode,
        "keyword": query,
        "params": params,
        "source": source or SOURCE[mode],
        "databricks_run_id": databricks_run_id,
        "started_at": datetime.now(timezone.utc),
        "api_calls": 0,
        "result_count": 0,
    }

    try:
        if mode == "youtube":
            counts = _youtube(query, max_results, order, published_after, published_before, dry_run, run)
        else:
            counts = _places(query, max_results, location, included_type, dry_run, run)
        run["status"] = "ok" if run["result_count"] else "no_result"
    except QuotaReached as error:
        run["status"], run["error"] = "quota", str(error)
        print(f"⏸️ {error}; nothing called")
        counts = {"api_calls": 0, "status": "quota"}
    except Exception as error:
        run["status"], run["error"] = "error", str(error)[:2000]
        if not dry_run:
            record_search_run(run)
            record_keyword_run(query, mode, None, ok=False, origin=keyword_origin)
        raise

    if dry_run:
        print("dry run: nothing written")
    else:
        record_search_run(run)
        if run["status"] != "quota":
            record_keyword_run(query, mode, run.get("new_results"), ok=True, origin=keyword_origin)
    return counts


def run_auto(
    limit: int = 20,
    max_results: int = 25,
    restaurants: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
    databricks_run_id: int | None = None,
    places_limit: int = AUTO_PLACES_PER_DAY,
) -> dict[str, Any]:
    """auto_search: plan the keywords, then search the due keywords with the highest priority.

    YouTube: `limit` keywords. First run of a keyword: order=relevance (best videos). Later runs:
    order=date and publishedAfter = day of the previous search, so only new uploads cost quota.
    A YouTube quota error stops the loop without failing; 3 errors in a row stop it too.
    Google Maps (places): `places_limit` keywords, 1 page (20 places, 1 call) each, while the
    auto_search calls this month stay under AUTO_PLACES_MONTHLY_LIMIT (and all calls under
    PLACES_MONTHLY_CALL_LIMIT, checked in _places).
    """
    from keywords import due_keywords, plan
    from youtube_client import YouTubeClientError

    if not dry_run:
        init_db()
        print(f"plan: {plan(restaurants or [])}")
    picked = due_keywords(limit)
    print(f"auto_search: {len(picked)} due YouTube keyword(s) picked (limit {limit})")
    done, new_videos, errors_in_row, stopped = [], 0, 0, None
    for kw in picked:
        repeat = bool(kw["runs"]) and kw["last_searched_at"] is not None
        try:
            counts = run_search(
                kw["keyword"],
                mode="youtube",
                max_results=max_results,
                order="date" if repeat else "relevance",
                published_after=kw["last_searched_at"].date() if repeat else None,
                dry_run=dry_run,
                databricks_run_id=databricks_run_id,
                source=AUTO_SOURCE,
                keyword_origin=kw["origin"],
            )
            errors_in_row = 0
            new_videos += counts.get("new_videos", 0)
            done.append({"keyword": kw["keyword"], "mode": "youtube", "origin": kw["origin"],
                         "videos": counts.get("videos", 0), "new_videos": counts.get("new_videos", 0)})
        except YouTubeClientError as error:
            if error.status_code == 403 and "quota" in str(error.detail).lower():
                stopped = "youtube quota"
                print("⏸️ YouTube quota used up: stop, the rest runs tomorrow")
                break
            errors_in_row += 1
            print(f"⚠️ {kw['keyword']}: {error.detail}")
        except Exception as error:  # noqa: BLE001 - one bad keyword must not stop the others
            errors_in_row += 1
            print(f"⚠️ {kw['keyword']}: {error}")
        if errors_in_row >= 3:
            stopped = "3 errors in a row"
            break
    print(f"auto_search YouTube done: {len(done)} search(es), {new_videos} new video(s)" + (f", stopped: {stopped}" if stopped else ""))
    places = run_auto_places(places_limit, dry_run=dry_run, databricks_run_id=databricks_run_id)
    return {"searches": len(done), "new_videos": new_videos, "stopped": stopped, "keywords": done, "places": places}


def run_auto_places(limit: int, dry_run: bool = False, databricks_run_id: int | None = None) -> dict[str, Any]:
    """auto_search on Google Maps: due places keywords, 1 page each, within the auto monthly share."""
    from keywords import due_keywords

    if limit <= 0:
        return {"searches": 0, "new_places": 0, "stopped": "disabled", "keywords": []}
    used = places_calls_this_month(source=AUTO_SOURCE)
    budget = min(limit, AUTO_PLACES_MONTHLY_LIMIT - used)
    print(f"auto_search Places: {used}/{AUTO_PLACES_MONTHLY_LIMIT} auto calls this month · today up to {max(budget, 0)}")
    if budget <= 0:
        return {"searches": 0, "new_places": 0, "stopped": "auto monthly cap", "keywords": []}
    picked = due_keywords(budget, mode="places", verify_share=0)
    done, new_places, errors_in_row, stopped = [], 0, 0, None
    for kw in picked:
        try:
            counts = run_search(
                kw["keyword"],
                mode="places",
                max_results=PLACES_PAGE_RESULTS,
                location=DEFAULT_LOCATION,
                dry_run=dry_run,
                databricks_run_id=databricks_run_id,
                source=AUTO_SOURCE,
                keyword_origin=kw["origin"],
            )
        except QuotaReached as error:
            stopped = str(error)
            print(f"⏸️ {error}")
            break
        except Exception as error:  # noqa: BLE001 - one bad keyword must not stop the others
            errors_in_row += 1
            print(f"⚠️ {kw['keyword']}: {error}")
            if errors_in_row >= 3:
                stopped = "3 errors in a row"
                break
            continue
        if counts.get("status") == "quota":
            stopped = "places monthly cap"
            break
        errors_in_row = 0
        new_places += counts.get("new_places", 0)
        done.append({"keyword": kw["keyword"], "mode": "places", "origin": kw["origin"],
                     "places": counts.get("places", 0), "new_places": counts.get("new_places", 0)})
    print(f"auto_search Places done: {len(done)} search(es), {new_places} new place(s)" + (f", stopped: {stopped}" if stopped else ""))
    return {"searches": len(done), "new_places": new_places, "stopped": stopped, "keywords": done}


CHANNEL_SOURCE = "search_channel"  # new uploads of followed food channels (auto_search source A)
CHANNEL_FIRST_LOOKBACK_DAYS = 90    # first check of a channel: keep uploads of the last 90 days
CHANNEL_OVERLAP_DAYS = 2            # later checks: uploads since the previous check minus 2 days


def run_channel_uploads(
    channels: list[dict[str, Any]],
    limit: int = 100,
    dry_run: bool = False,
    databricks_run_id: int | None = None,
) -> dict[str, Any]:
    """auto_search source A: list the newest uploads of food channels we already know.

    channels: channel_id, channel_title, food_review_videos (from gold.dim_video, classified food reviews).
    One playlistItems.list call (1 unit, 50 newest uploads) per channel, then videos.list for the new
    videos only (1 unit per 50). Writes videos with source search_channel, one followed_channels
    update per channel and one search_runs row (mode channel). Never raises: errors are reported.
    """
    from datetime import timedelta

    from database import (
        due_followed_channels,
        record_channel_check,
        upsert_followed_channels,
    )
    from youtube_client import YouTubeClientError, get_channel_uploads, get_video_details, is_quota_error

    run: dict[str, Any] = {
        "search_run_id": str(uuid.uuid4()),
        "mode": "channel",
        "keyword": "",
        "params": {"limit": limit, "first_lookback_days": CHANNEL_FIRST_LOOKBACK_DAYS},
        "source": CHANNEL_SOURCE,
        "databricks_run_id": databricks_run_id,
        "started_at": datetime.now(timezone.utc),
        "api_calls": 0,
        "result_count": 0,
        "new_results": 0,
    }
    if dry_run:  # nothing is written, so take the input list as is
        due = sorted(channels, key=lambda c: -(c.get("food_review_videos") or 0))[:limit]
    else:
        init_db()
        upsert_followed_channels(channels)
        due = due_followed_channels(limit)
    run["keyword"] = f"uploads of {len(due)} channel(s)"
    print(f"channel uploads: {len(channels)} food channel(s) known, {len(due)} checked today")

    now = datetime.now(timezone.utc)
    new_by_channel: dict[str, list[str]] = {}
    checked, errors_in_row, stopped = [], 0, None
    for ch in due:
        last = ch.get("last_checked_at")
        cutoff = (last - timedelta(days=CHANNEL_OVERLAP_DAYS)) if last else now - timedelta(days=CHANNEL_FIRST_LOOKBACK_DAYS)
        try:
            uploads = get_channel_uploads(ch["channel_id"])
            run["api_calls"] += 1
            errors_in_row = 0
        except YouTubeClientError as error:
            if is_quota_error(error):
                stopped = "youtube quota"
                print("⏸️ YouTube quota used up: stop, the rest runs tomorrow")
                break
            errors_in_row += 1
            print(f"⚠️ {ch.get('channel_title') or ch['channel_id']}: {str(error.detail)[:300]}")
            if errors_in_row >= 3:
                stopped = "3 errors in a row"
                break
            continue
        recent = [
            u["video_id"] for u in uploads
            if u.get("published_at") and datetime.fromisoformat(u["published_at"].replace("Z", "+00:00")) >= cutoff
        ]
        known = existing_ids("videos", "video_id", recent) if recent else set()
        new_ids = [vid for vid in recent if vid not in known]
        run["result_count"] += len(recent)
        new_by_channel[ch["channel_id"]] = new_ids
        checked.append({"channel": ch.get("channel_title") or ch["channel_id"], "recent": len(recent), "new_videos": len(new_ids)})

    all_new = list(dict.fromkeys(vid for ids in new_by_channel.values() for vid in ids))
    videos: list[dict[str, Any]] = []
    try:
        for i in range(0, len(all_new), 50):
            details = get_video_details(all_new[i:i + 50])
            run["api_calls"] += 1
            for vid in all_new[i:i + 50]:
                if vid in details:
                    details[vid]["video_url"] = f"https://www.youtube.com/watch?v={vid}"
                    videos.append(details[vid])
    except YouTubeClientError as error:
        stopped = stopped or ("youtube quota" if is_quota_error(error) else f"videos.list: {str(error.detail)[:200]}")
        print(f"⚠️ videos.list stopped: {str(error.detail)[:300]}")
    run["new_results"] = len(videos)
    run["status"] = "ok" if videos else ("error" if stopped and not checked else "no_result")
    run["error"] = stopped

    print(f"channel uploads done: {len(checked)} channel(s), {run['result_count']} recent upload(s), "
          f"{len(videos)} new video(s), {run['api_calls']} unit(s)" + (f", stopped: {stopped}" if stopped else ""))
    if dry_run:
        print("dry run: nothing written")
    else:
        saved = {v["video_id"] for v in videos}
        upsert_videos(videos, source=CHANNEL_SOURCE)
        for ch in due:
            if ch["channel_id"] in new_by_channel:  # checked (not skipped by an error / quota stop)
                record_channel_check(ch["channel_id"], sum(v in saved for v in new_by_channel[ch["channel_id"]]))
        record_search_run(run)
    top = [{"title": v.get("title"), "channel": v.get("channel_title"), "views": v.get("view_count"),
            "url": v.get("video_url")} for v in videos[:10]]
    return {"channels": len(checked), "recent_uploads": run["result_count"], "new_videos": len(videos),
            "api_calls": run["api_calls"], "stopped": stopped, "top": top}


def main(argv: list[str] | None = None) -> dict[str, int]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", help="search keyword, e.g. \"quán ăn Hồ Chí Minh\"")
    parser.add_argument("--mode", choices=MODES, default="youtube")
    parser.add_argument("--max-results", type=int, default=10, help="youtube 1-50, places 1-60 (default 10)")
    parser.add_argument(
        "--order",
        choices=["relevance", "date", "viewCount"],
        default="relevance",
        help="youtube: sort order sent to the API (default relevance)",
    )
    parser.add_argument("--published-after", help="youtube: YYYY-MM-DD")
    parser.add_argument("--published-before", help="youtube: YYYY-MM-DD")
    parser.add_argument("--location", default=DEFAULT_LOCATION, help="places: lat,lng,radius_m bias ('' = none)")
    parser.add_argument("--included-type", default="", help="places: e.g. restaurant ('' = any)")
    parser.add_argument("--dry-run", action="store_true", help="call the API but write nothing")
    args = parser.parse_args(argv)

    return run_search(
        query=args.query,
        mode=args.mode,
        max_results=args.max_results,
        order=args.order,
        published_after=_parse_date(args.published_after),
        published_before=_parse_date(args.published_before),
        location=args.location,
        included_type=args.included_type,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
