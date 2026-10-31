"""Search keywords the system picks by itself (design "Tự động search keyword", 2026-10-31).

Table public.search_keywords holds one row per (normalized keyword, mode) with its schedule:
- origin grid: dish x district of Ho Chi Minh City ("quán phở Quận 3"), seeded once;
- origin verify: "<restaurant> review" for well-rated restaurants that only one channel reviewed;
- origin manual: every keyword searched by hand (chat, app, Jobs UI), so it is re-run too.
priority is recomputed every day by plan(); auto_search picks the due keywords with the
highest priority. After a run the keyword rests cooldown_days (7, doubled up to 60 while a
run finds no new video).
"""
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from database import APP_SCHEMA, get_connection

COOLDOWN_MIN, COOLDOWN_MAX = 7, 60
VERIFY_MIN_RATING, VERIFY_MIN_REVIEWS = 4.3, 100

# (dish as typed in the keyword, cuisine group used by the dashboard / gold.dim_restaurant)
DISHES: list[tuple[str, str]] = [
    ("phở", "Món Việt"), ("bún bò", "Món Việt"), ("cơm tấm", "Món Việt"), ("bánh mì", "Món Việt"),
    ("hủ tiếu", "Món Việt"), ("bún chả", "Món Việt"), ("bún riêu", "Món Việt"), ("bánh xèo", "Món Việt"),
    ("mì Quảng", "Món Việt"), ("bánh canh", "Món Việt"), ("ốc", "Hải sản"), ("hải sản", "Hải sản"),
    ("lẩu", "Lẩu & nướng"), ("nướng", "Lẩu & nướng"), ("buffet", "Buffet"), ("sushi", "Món Nhật"),
    ("ramen", "Món Nhật"), ("món Hàn", "Món Hàn"), ("dimsum", "Món Hoa"), ("món Thái", "Món Thái"),
    ("chay", "Chay"), ("pizza", "Món Âu / Mỹ"), ("bít tết", "Món Âu / Mỹ"), ("gà rán", "Ăn nhanh"),
    ("cafe", "Cafe & tráng miệng"), ("chè", "Cafe & tráng miệng"),
]
# District names as videos write them (pre-2025 names; Quận 2 / 9 are now Thủ Đức)
DISTRICTS: list[str] = [
    "Quận 1", "Quận 3", "Quận 4", "Quận 5", "Quận 6", "Quận 7", "Quận 8", "Quận 10", "Quận 11",
    "Quận 12", "Bình Thạnh", "Phú Nhuận", "Gò Vấp", "Tân Bình", "Tân Phú", "Bình Tân", "Thủ Đức",
    "Bình Chánh", "Nhà Bè", "Hóc Môn",
]


def normalize_keyword(text: str) -> str:
    """Same rule as gold _udfs.normalize_keyword: NFC, lower-case, trimmed, single spaces, diacritics kept."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "").lower()).strip()


def district_norm(text: str | None) -> str | None:
    """'Q.3' / 'quận 03' / 'Quận 3' -> 'quan 3'; 'TP Thủ Đức' -> 'thu duc' (as gold district_norm)."""
    if not text:
        return None
    s = unicodedata.normalize("NFD", text.replace("Đ", "D").replace("đ", "d"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()
    s = re.sub(r"^(tp\.?|thanh pho)\s+", "", s)
    s = re.sub(r"^(q\.?|quan)\s*0*(\d+)$", r"quan \2", s)
    return re.sub(r"\s+", " ", s) or None


def _execute_many(sql: str, rows: list[tuple]) -> None:
    if not rows:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.executemany(sql, rows)
        conn.commit()


def upsert_candidates(candidates: Iterable[dict[str, Any]]) -> None:
    """Insert new keywords; existing ones keep their schedule and history."""
    rows = [
        (
            normalize_keyword(c["keyword"]), c["keyword"], c.get("mode", "youtube"), c["origin"],
            c.get("dish"), c.get("cuisine"), c.get("district"),
        )
        for c in candidates
    ]
    _execute_many(
        """
        INSERT INTO search_keywords (keyword_normalized, keyword, mode, origin, dish, cuisine, district)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (keyword_normalized, mode) DO NOTHING
        """,
        rows,
    )


def grid_candidates() -> list[dict[str, Any]]:
    return [
        {"keyword": f"quán {dish} {district}", "origin": "grid", "dish": dish, "cuisine": cuisine, "district": district}
        for dish, cuisine in DISHES
        for district in DISTRICTS
    ]


def verify_candidates(restaurants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Well-rated restaurants that only one YouTube channel talked about: look for more reviews."""
    return [
        {"keyword": f"{r['restaurant_name']} review", "origin": "verify", "district": r.get("district"),
         "cuisine": r.get("cuisine")}
        for r in restaurants
        if r.get("restaurant_name")
        and (r.get("channel_count") or 0) <= 1
        and (r.get("google_rating") or 0) >= VERIFY_MIN_RATING
        and (r.get("google_rating_count") or 0) >= VERIFY_MIN_REVIEWS
    ]


def gap_scores(restaurants: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    """How thin the dashboard is per (district, cuisine): 1/(1+restaurants) + 0.5/(1+Legit restaurants)."""
    counts: dict[tuple[str, str], list[int]] = {}
    for r in restaurants:
        key = (district_norm(r.get("district")), r.get("cuisine"))
        n = counts.setdefault(key, [0, 0])
        n[0] += 1
        n[1] += r.get("legit_label") == "Legit"
    scores = {}
    for district in DISTRICTS:
        for _, cuisine in DISHES:
            n, legit = counts.get((district_norm(district), cuisine), [0, 0])
            scores[(district_norm(district), cuisine)] = 1 / (1 + n) + 0.5 / (1 + legit)
    return scores


def plan(restaurants: list[dict[str, Any]]) -> dict[str, int]:
    """Seed the grid, add verify keywords from Gold, recompute every priority. Returns counts."""
    upsert_candidates(grid_candidates())
    verify = verify_candidates(restaurants)
    upsert_candidates(verify)
    gaps = gap_scores(restaurants)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.execute("SELECT keyword_normalized, mode, origin, district, cuisine, runs, last_new_videos FROM search_keywords")
            rows = cur.fetchall()
            updates = []
            for kw, mode, origin, district, cuisine, runs, last_new in rows:
                score = 0.0
                if origin == "grid":
                    score += gaps.get((district_norm(district), cuisine), 0.5)
                elif origin == "verify":
                    score += 0.8
                else:  # manual
                    score += 0.6
                if not runs:
                    score += 1.0  # never searched
                score += min(last_new or 0, 10) / 10  # last run found new videos
                updates.append((round(score, 4), kw, mode, round(score, 4)))
            cur.executemany(
                "UPDATE search_keywords SET priority = %s, updated_at = NOW() "
                "WHERE keyword_normalized = %s AND mode = %s AND priority IS DISTINCT FROM %s",
                updates,
            )
        conn.commit()
    return {"keywords": len(rows), "verify_candidates": len(verify)}


def due_keywords(limit: int, mode: str = "youtube", verify_share: float = 0.25, per_dish: int = 2) -> list[dict[str, Any]]:
    """Due keywords to search today, highest priority first.

    verify keywords get up to verify_share of the slots first (they never win on priority against
    never-searched grid keywords); grid keywords then take at most per_dish per dish so one day
    covers several dishes; leftover slots are filled by priority. Ties are shuffled per day.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.execute(
                """
                SELECT keyword_normalized, keyword, origin, dish, runs, last_searched_at
                FROM search_keywords
                WHERE active AND mode = %s AND (next_due_at IS NULL OR next_due_at <= NOW())
                ORDER BY priority DESC, md5(keyword_normalized || CURRENT_DATE::text)
                """,
                (mode,),
            )
            columns = [d.name for d in cur.description]
            rows = [dict(zip(columns, r)) for r in cur.fetchall()]
    verify = [r for r in rows if r["origin"] == "verify"][: max(1, int(limit * verify_share))]
    picked = verify[:limit]
    taken = {r["keyword_normalized"] for r in picked}
    per: dict[str, int] = {}
    for r in rows:
        if len(picked) >= limit:
            break
        if r["keyword_normalized"] in taken or r["origin"] == "verify":
            continue
        if r["dish"] and per.get(r["dish"], 0) >= per_dish:
            continue
        per[r["dish"]] = per.get(r["dish"], 0) + 1
        picked.append(r)
        taken.add(r["keyword_normalized"])
    for r in rows:  # fill what the per-dish cap left empty
        if len(picked) >= limit:
            break
        if r["keyword_normalized"] not in taken and r["origin"] != "verify":
            picked.append(r)
            taken.add(r["keyword_normalized"])
    return picked


def record_keyword_run(keyword: str, mode: str, new_results: int | None, ok: bool, origin: str = "manual") -> None:
    """After a real search: update the keyword's history and its next due time (insert it if new)."""
    norm = normalize_keyword(keyword)
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{APP_SCHEMA}"')
            cur.execute(
                """
                INSERT INTO search_keywords (keyword_normalized, keyword, mode, origin)
                VALUES (%s, %s, %s, %s) ON CONFLICT (keyword_normalized, mode) DO NOTHING
                """,
                (norm, keyword, mode, origin),
            )
            cur.execute("SELECT cooldown_days FROM search_keywords WHERE keyword_normalized = %s AND mode = %s", (norm, mode))
            cooldown = cur.fetchone()[0]
            if not ok:  # API error: try again tomorrow, history unchanged
                cur.execute(
                    "UPDATE search_keywords SET next_due_at = %s, updated_at = NOW() "
                    "WHERE keyword_normalized = %s AND mode = %s",
                    (now + timedelta(days=1), norm, mode),
                )
            else:
                cooldown = min(cooldown * 2, COOLDOWN_MAX) if not new_results else COOLDOWN_MIN
                cur.execute(
                    """
                    UPDATE search_keywords SET
                        runs = runs + 1, last_searched_at = %s, last_new_videos = %s,
                        total_new_videos = total_new_videos + %s, cooldown_days = %s,
                        next_due_at = %s, updated_at = NOW()
                    WHERE keyword_normalized = %s AND mode = %s
                    """,
                    (now, new_results or 0, new_results or 0, cooldown, now + timedelta(days=cooldown), norm, mode),
                )
        conn.commit()
