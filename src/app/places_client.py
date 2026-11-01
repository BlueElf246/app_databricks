"""Google Places API (New) Text Search: find places like a search on Google Maps.

One call returns one page of at most 20 places; a search asks for up to 3 pages
(60 places), the most Text Search returns. Google bills a call at the highest
SKU in its field mask, and each SKU has its own free calls per month, so there
are two masks (callers count calls per SKU, see search_task.py):

- "enterprise" (manual searches): adds rating, userRatingCount, priceLevel,
  priceRange, websiteUri, nationalPhoneNumber, regularOpeningHours, so every
  call is Text Search Enterprise (1,000 free calls / month).
- "pro" (auto_search, added 2026-11-01): only Pro fields (id, name, address,
  location, types, business status, Maps link), so every call is Text Search Pro
  (5,000 free calls / month, shared with stg_restaurant_geocode). The missing
  fields come later from Place Details (stg/restaurant_place_details).

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

PRO_FIELDS = [
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.primaryType",
    "places.types",
    "places.businessStatus",
    "places.googleMapsUri",
    "nextPageToken",
]
# Any one of these makes the call Text Search Enterprise
ENTERPRISE_FIELDS = [
    "places.rating",
    "places.userRatingCount",
    "places.priceLevel",
    "places.priceRange",
    "places.websiteUri",
    "places.nationalPhoneNumber",
    "places.regularOpeningHours",
]
SKUS = ("enterprise", "pro")
FIELD_MASKS = {
    "pro": ",".join(PRO_FIELDS),
    "enterprise": ",".join(PRO_FIELDS + ENTERPRISE_FIELDS),
}
FIELD_MASK = FIELD_MASKS["enterprise"]  # default (manual searches)
# Lakebase `places` columns that only an Enterprise call returns: a Pro call must not blank them
ENTERPRISE_COLUMNS = (
    "rating", "user_rating_count", "price_level", "price_range_start_vnd", "price_range_end_vnd",
    "website_uri", "national_phone_number", "regular_opening_hours",
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
    sku: str = "enterprise",
) -> tuple[list[dict[str, Any]], str | None]:
    """One Text Search call billed as `sku` ("enterprise" or "pro"): (raw places, next page token or None)."""
    if sku not in SKUS:
        raise ValueError(f"sku must be one of {SKUS}, got {sku!r}")
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

    headers = {"X-Goog-Api-Key": _api_key(), "X-Goog-FieldMask": FIELD_MASKS[sku]}
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
    sku: str = "enterprise",
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
        page, token = search_text_page(query, location_bias, included_type, token, sku=sku)
        calls += 1
        places.extend(page)
        if not token:
            break
    return places[:max_results], calls
