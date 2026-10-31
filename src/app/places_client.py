"""Google Places API (New) Text Search: find places like a search on Google Maps.

One call returns one page of at most 20 places; a search asks for up to 3 pages
(60 places), the most Text Search returns. The field mask includes rating,
userRatingCount, priceLevel and priceRange, so every call is billed as
Text Search Enterprise: callers must count calls (see search_task.py).

Key: env MAPS_API_KEY (set by the job notebook from secret maps/api_key), else
the Databricks secret maps/api_key read through the SDK.
"""
import os
import time
from typing import Any

import httpx

SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
PAGE_SIZE = 20
MAX_PAGES = 3

FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.primaryType",
        "places.types",
        "places.rating",
        "places.userRatingCount",
        "places.priceLevel",
        "places.priceRange",
        "places.businessStatus",
        "places.googleMapsUri",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.regularOpeningHours",
        "nextPageToken",
    ]
)


class PlacesClientError(Exception):
    def __init__(self, status_code: int, detail: Any) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


def _api_key() -> str:
    key = os.getenv("MAPS_API_KEY")
    if key:
        return key
    try:
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient().dbutils.secrets.get(scope="maps", key="api_key")
    except Exception as error:  # noqa: BLE001 - any failure means "no key"
        raise PlacesClientError(500, f"No Places API key (env MAPS_API_KEY or secret maps/api_key): {error}")


def parse_location(value: str | None) -> dict[str, Any] | None:
    """'lat,lng,radius_m' -> Text Search locationBias circle; empty -> None."""
    if not value or not value.strip():
        return None
    lat, lng, radius = (float(part) for part in value.split(","))
    return {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius}}


def search_text_page(
    query: str,
    location_bias: dict[str, Any] | None = None,
    included_type: str | None = None,
    page_token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One Text Search call: (raw places, next page token or None)."""
    body: dict[str, Any] = {
        "textQuery": query,
        "languageCode": "vi",
        "regionCode": "VN",
        "pageSize": PAGE_SIZE,
    }
    if location_bias:
        body["locationBias"] = location_bias
    if included_type:
        body["includedType"] = included_type
    if page_token:
        body["pageToken"] = page_token

    headers = {"X-Goog-Api-Key": _api_key(), "X-Goog-FieldMask": FIELD_MASK}
    try:
        response = httpx.post(SEARCH_TEXT_URL, json=body, headers=headers, timeout=30.0)
    except httpx.RequestError as error:
        raise PlacesClientError(502, f"Cannot reach the Places API: {error}") from error
    if response.status_code != 200:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise PlacesClientError(response.status_code, detail)

    data = response.json()
    return data.get("places", []), data.get("nextPageToken")


def _vnd_units(money: dict[str, Any] | None) -> int | None:
    if not money or money.get("currencyCode") != "VND" or money.get("units") is None:
        return None
    return int(money["units"])


def to_row(place: dict[str, Any]) -> dict[str, Any]:
    """Raw Places object -> a row of the Lakebase table `places`."""
    location = place.get("location") or {}
    price_range = place.get("priceRange") or {}
    return {
        "place_id": place["id"],
        "display_name": (place.get("displayName") or {}).get("text"),
        "formatted_address": place.get("formattedAddress"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "primary_type": place.get("primaryType"),
        "types": place.get("types"),
        "rating": place.get("rating"),
        "user_rating_count": place.get("userRatingCount"),
        "price_level": place.get("priceLevel"),
        "price_range_start_vnd": _vnd_units(price_range.get("startPrice")),
        "price_range_end_vnd": _vnd_units(price_range.get("endPrice")),
        "business_status": place.get("businessStatus"),
        "google_maps_uri": place.get("googleMapsUri"),
        "website_uri": place.get("websiteUri"),
        "national_phone_number": place.get("nationalPhoneNumber"),
        "regular_opening_hours": place.get("regularOpeningHours"),
        "raw": place,
    }


def search_places(
    query: str,
    max_results: int = PAGE_SIZE,
    location_bias: dict[str, Any] | None = None,
    included_type: str | None = None,
    max_calls: int = MAX_PAGES,
) -> tuple[list[dict[str, Any]], int]:
    """Page through Text Search: (raw places in Google's order, API calls made).

    Stops at max_results, at max_calls (the caller's quota budget), or when
    Google has no next page.
    """
    pages = min(MAX_PAGES, max_calls, -(-max_results // PAGE_SIZE))
    places: list[dict[str, Any]] = []
    calls = 0
    token = None
    while calls < pages:
        if token:
            time.sleep(2)  # a fresh page token can be rejected for a moment
        page, token = search_text_page(query, location_bias, included_type, token)
        calls += 1
        places.extend(page)
        if not token:
            break
    return places[:max_results], calls
