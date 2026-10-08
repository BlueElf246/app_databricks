import os
from datetime import date
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

# Try Databricks Secrets first (runtime), fall back to env var (local dev)
try:
    from databricks.sdk import WorkspaceClient

    _w = WorkspaceClient()
    YOUTUBE_API_KEY = _w.dbutils.secrets.get(scope="youtube", key="api_key")
except Exception:
    YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_COMMENT_THREADS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"


class YouTubeClientError(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(str(detail))


def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    if not YOUTUBE_API_KEY:
        raise YouTubeClientError(
            status_code=500,
            detail="Chưa cấu hình YOUTUBE_API_KEY",
        )

    params = {**params, "key": YOUTUBE_API_KEY}

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, params=params)
    except httpx.RequestError as error:
        raise YouTubeClientError(
            status_code=502,
            detail=f"Không thể kết nối tới YouTube API: {error}",
        ) from error

    if response.status_code != 200:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise YouTubeClientError(
            status_code=response.status_code,
            detail=detail,
        )

    return response.json()


def search_videos(
    query: str,
    max_results: int = 50,
    order: str = "relevance",
    published_after: date | None = None,
    published_before: date | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "part": "snippet",
        "type": "video",
        "q": query,
        "regionCode": "VN",
        "relevanceLanguage": "vi",
        "maxResults": max_results,
        "order": order,
    }

    if published_after is not None:
        params["publishedAfter"] = f"{published_after.isoformat()}T00:00:00Z"
    if published_before is not None:
        params["publishedBefore"] = f"{published_before.isoformat()}T23:59:59Z"

    return _get_json(YOUTUBE_SEARCH_URL, params)


def get_video_details(video_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch full snippet/contentDetails/statistics for videos, keyed by video_id."""
    if not video_ids:
        return {}

    params = {
        "part": "snippet,contentDetails,statistics",
        "id": ",".join(video_ids),
    }
    data = _get_json(YOUTUBE_VIDEOS_URL, params)

    details: dict[str, dict[str, Any]] = {}
    for item in data.get("items", []):
        video_id = item.get("id")
        if not video_id:
            continue
        snippet = item.get("snippet", {})
        content = item.get("contentDetails", {})
        stats = item.get("statistics", {})
        details[video_id] = {
            "video_id": video_id,
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "channel_id": snippet.get("channelId"),
            "channel_title": snippet.get("channelTitle"),
            "published_at": snippet.get("publishedAt"),
            "category_id": snippet.get("categoryId"),
            "tags": snippet.get("tags", []),
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url"),
            "duration": content.get("duration"),
            "definition": content.get("definition"),
            "has_caption": content.get("caption") == "true",
            "view_count": (
                int(stats["viewCount"]) if stats.get("viewCount") is not None else None
            ),
            "like_count": (
                int(stats["likeCount"]) if stats.get("likeCount") is not None else None
            ),
            "comment_count": (
                int(stats["commentCount"])
                if stats.get("commentCount") is not None
                else None
            ),
        }
    return details


def get_channels(channel_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch snippet/statistics for channels, keyed by channel_id."""
    if not channel_ids:
        return {}

    params = {
        "part": "snippet,statistics",
        "id": ",".join(dict.fromkeys(channel_ids)),  # dedupe, keep order
    }
    data = _get_json(YOUTUBE_CHANNELS_URL, params)

    channels: dict[str, dict[str, Any]] = {}
    for item in data.get("items", []):
        channel_id = item.get("id")
        if not channel_id:
            continue
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        channels[channel_id] = {
            "channel_id": channel_id,
            "channel_title": snippet.get("title"),
            "channel_description": snippet.get("description"),
            "custom_url": snippet.get("customUrl"),
            "country": snippet.get("country"),
            "default_language": snippet.get("defaultLanguage"),
            "published_at": snippet.get("publishedAt"),
            "subscriber_count": (
                int(stats["subscriberCount"])
                if stats.get("subscriberCount") is not None
                else None
            ),
            "view_count": (
                int(stats["viewCount"]) if stats.get("viewCount") is not None else None
            ),
            "video_count": (
                int(stats["videoCount"]) if stats.get("videoCount") is not None else None
            ),
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url"),
        }
    return channels


def get_comment_threads(video_id: str, max_results: int = 20) -> list[dict[str, Any]]:
    """Fetch top-level comment threads for a video."""
    params = {
        "part": "snippet",
        "videoId": video_id,
        "maxResults": max_results,
        "textFormat": "plainText",
    }
    data = _get_json(YOUTUBE_COMMENT_THREADS_URL, params)

    comments: list[dict[str, Any]] = []
    for item in data.get("items", []):
        comment_id = item.get("id")
        top_level = item.get("snippet", {}).get("topLevelComment", {})
        snippet = top_level.get("snippet", {})
        if not comment_id:
            continue
        comments.append(
            {
                "comment_id": comment_id,
                "video_id": video_id,
                "author_display_name": snippet.get("authorDisplayName"),
                "author_channel_id": snippet.get("authorChannelId", {}).get("value"),
                "text": snippet.get("textOriginal"),
                "like_count": snippet.get("likeCount"),
                "published_at": snippet.get("publishedAt"),
                "total_reply_count": item.get("snippet", {}).get("totalReplyCount"),
            }
        )
    return comments