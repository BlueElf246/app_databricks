# Databricks notebook source
# MAGIC %md
# MAGIC # Ask the restaurant dashboard (read-only)
# MAGIC
# MAGIC Answers chat questions like "hôm nay nên ăn gì ở quận 7?" from the same Gold tables as the
# MAGIC `restaurant_finder` dashboard. Run by `scripts/ask_restaurants.py` as a one-time run
# MAGIC (`jobs/runs/submit`, notebook from Git), so no job is deployed or triggered. **Only SELECTs**;
# MAGIC the result goes back as JSON through `dbutils.notebook.exit`.
# MAGIC
# MAGIC Widgets: `gold_schema`, `district` ("Quận 7", "q7", "Bình Thạnh"...), `dish` (matched against
# MAGIC cuisine, name and review video titles), `max_price_band` (1-4), `city`, `sort`
# MAGIC (legit | trending | hot | rating), `legit_only`, `limit`.

# COMMAND ----------

import json
import re
import unicodedata

for name, default in [("gold_schema", "prod.dev_gold"), ("district", ""), ("dish", ""), ("max_price_band", ""),
                      ("city", ""), ("sort", "legit"), ("legit_only", "false"), ("limit", "10")]:
    dbutils.widgets.text(name, default)
arg = {name: dbutils.widgets.get(name).strip() for name in
       ["gold_schema", "district", "dish", "max_price_band", "city", "sort", "legit_only", "limit"]}

GOLD = arg["gold_schema"]
if not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", GOLD):
    raise ValueError(f"bad gold_schema {GOLD!r}")


def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").lower()


# "quận 7" / "quan 7" / "q7" / "Q.07" -> district number 7; anything else is a name ("Bình Thạnh").
district_no, district_name = None, None
if arg["district"]:
    m = re.fullmatch(r"(?:quan|q\.?)?\s*0*(\d{1,2})", _strip_accents(arg["district"]))
    if m:
        district_no = int(m.group(1))
    else:
        district_name = re.sub(r"(?i)^(quận|quan|huyện|huyen|q\.)\s*", "", arg["district"]).strip()

SORTS = {
    "legit": "legit_score DESC NULLS LAST, credited_views DESC",
    "trending": "views_7d DESC NULLS LAST, credited_views DESC",
    "hot": "hot_score DESC NULLS LAST, credited_views DESC",
    "rating": "google_rating DESC NULLS LAST, google_rating_count DESC NULLS LAST",
}
order_by = SORTS.get(arg["sort"], SORTS["legit"])
limit = max(1, min(int(arg["limit"] or 10), 30))

# COMMAND ----------

# Same district normalization as the dashboard's "restaurants" dataset (keep in sync).
sql = f"""
WITH r AS (
  SELECT a.*,
    CASE WHEN district RLIKE '(?iu)^(q\\\\.?\\\\s*|quận\\\\s+)0*[0-9]+$'
         THEN concat('Quận ', CAST(regexp_extract(district, '([0-9]+)', 1) AS INT))
         ELSE COALESCE(nullif(trim(regexp_replace(regexp_replace(district,
                '(?iu)^(q\\\\.\\\\s*|quận\\\\s+|huyện\\\\s+|thị trấn\\\\s+|tp\\\\.?\\\\s*|thành phố\\\\s+)', ''),
                '\\\\s*\\\\(cũ\\\\)$', '')), ''), 'Chưa rõ')
    END AS district_norm
  FROM {GOLD}.agg_restaurant_ranking a
),
videos AS (   -- review videos per restaurant with their latest view count
  SELECT r.restaurant_id, v.title, v.video_url, c.channel_title, s.view_count
  FROM {GOLD}.fact_video_restaurant_mention m
  JOIN {GOLD}.dim_restaurant r ON r.restaurant_key = m.restaurant_key
  JOIN {GOLD}.dim_video v      ON v.video_key = m.video_key
  LEFT JOIN {GOLD}.dim_channel c ON c.channel_id = v.channel_id
  LEFT JOIN (SELECT video_key, max_by(view_count, snapshot_date_key) AS view_count
             FROM {GOLD}.fact_video_daily_snapshot GROUP BY video_key) s ON s.video_key = m.video_key
  WHERE m.restaurant_key <> -1
),
top_video AS (
  SELECT restaurant_id,
         max_by(video_url, COALESCE(view_count, 0)) AS top_video_url,
         max_by(title, COALESCE(view_count, 0))     AS top_video_title,
         max_by(channel_title, COALESCE(view_count, 0)) AS top_video_channel,
         max(view_count) AS top_video_views
  FROM videos GROUP BY restaurant_id
),
picked AS (
  SELECT r.restaurant_id, r.restaurant_name, r.address, r.district_norm AS district, r.city,
         r.cuisine, r.cuisine_detail, r.price_band,
         CASE WHEN r.price_min_vnd IS NOT NULL
              THEN concat(CAST(round(r.price_min_vnd / 1000) AS INT), 'k',
                          COALESCE(' – ' || CAST(round(r.price_max_vnd / 1000) AS INT) || 'k', '+')) END AS price_range,
         r.google_rating, r.google_rating_count, r.legit_score, r.legit_label,
         round(100 * r.ranking_score, 1) AS hot_score,
         r.credited_views, r.credited_views_7d AS views_7d, r.trending_rank_in_city,
         r.channel_count, r.video_count,
         COALESCE(r.google_maps_uri, 'https://www.google.com/maps/search/?api=1&query='
                  || url_encode(concat_ws(' ', r.restaurant_name, r.address))) AS maps_url,
         t.top_video_url, t.top_video_title, t.top_video_channel, t.top_video_views,
         r.as_of_date_key
  FROM r LEFT JOIN top_video t ON t.restaurant_id = r.restaurant_id
  WHERE (:city = '' OR r.city ILIKE concat('%', :city, '%'))
    AND (:district_no = 0
         OR r.district_norm = concat('Quận ', :district_no)
         -- district missing on ~38% of rows: fall back to the address text
         OR (r.district_norm = 'Chưa rõ'
             AND r.address RLIKE concat('(?iu)(quận|quan|q\\\\.?)\\\\s*0?', :district_no, '([^0-9]|$)')))
    AND (:district_name = '' OR r.district_norm ILIKE concat('%', :district_name, '%')
         OR r.address ILIKE concat('%', :district_name, '%'))
    AND (:dish = '' OR r.cuisine ILIKE concat('%', :dish, '%') OR r.cuisine_detail ILIKE concat('%', :dish, '%')
         OR r.restaurant_name ILIKE concat('%', :dish, '%')
         OR EXISTS (SELECT 1 FROM videos v WHERE v.restaurant_id = r.restaurant_id AND v.title ILIKE concat('%', :dish, '%')))
    AND (:max_price = 0 OR TRY_CAST(substr(r.price_band, 1, 1) AS INT) <= :max_price)
    AND (:legit_only = 'false' OR r.legit_label = 'Legit')
)
SELECT * FROM picked ORDER BY {order_by} LIMIT {limit}
"""
params = {
    "city": arg["city"], "district_no": district_no or 0, "district_name": district_name or "",
    "dish": arg["dish"], "max_price": int(arg["max_price_band"] or 0),
    "legit_only": "true" if arg["legit_only"].lower() == "true" else "false",
}
rows = [row.asDict() for row in spark.sql(sql, args=params).collect()]

# COMMAND ----------

def _plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return float(value) if hasattr(value, "as_tuple") else str(value)   # Decimal -> float, dates -> str

result = {
    "filters": {**params, "sort": arg["sort"], "limit": limit, "gold_schema": GOLD},
    "count": len(rows),
    "rows": [{k: _plain(v) for k, v in row.items()} for row in rows],
}
dbutils.notebook.exit(json.dumps(result, ensure_ascii=False))
