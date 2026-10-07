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


def get_video_statistics(
    video_ids: list[str],
) -> dict[str, dict[str, int | None]]:
    if not video_ids:
        return {}

    params = {
        "part": "statistics",
        "id": ",".join(video_ids),
    }
    data = _get_json(YOUTUBE_VIDEOS_URL, params)

    statistics: dict[str, dict[str, int | None]] = {}
    for item in data.get("items", []):
        video_id = item.get("id")
        values = item.get("statistics", {})
        if not video_id:
            continue
        statistics[video_id] = {
            "view_count": (
                int(values["viewCount"])
                if values.get("viewCount") is not None
                else None
            ),
            "like_count": (
                int(values["likeCount"])
                if values.get("likeCount") is not None
                else None
            ),
        }
    return statistics