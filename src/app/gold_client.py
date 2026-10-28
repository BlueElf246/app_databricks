"""Read-only access to the Gold model (prod.gold) through a Databricks SQL warehouse.

The Lakebase Postgres tables hold only the latest counters; the ranking lives in
the lakehouse (Analytic/gold/agg_restaurant_ranking), so the app reads it here.

Auth comes from the Databricks SDK default chain: the app's service principal when
deployed as a Databricks App, or DATABRICKS_CONFIG_PROFILE / DATABRICKS_HOST locally.
The principal needs CAN USE on the warehouse and SELECT on prod.gold.
"""
import os
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem, StatementState
from dotenv import load_dotenv

load_dotenv()

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "b47ed9c735902fd0")
GOLD_SCHEMA = os.environ.get("GOLD_SCHEMA", "prod.gold")

_client: WorkspaceClient | None = None


def _query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    global _client
    if _client is None:
        _client = WorkspaceClient()
    response = _client.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=sql,
        parameters=[
            StatementParameterListItem(name=name, value=None if value is None else str(value))
            for name, value in (params or {}).items()
        ],
        wait_timeout="50s",
    )
    if response.status.state != StatementState.SUCCEEDED:
        error = response.status.error.message if response.status.error else response.status.state
        raise RuntimeError(f"Gold query failed: {error}")
    columns = [column.name for column in response.manifest.schema.columns]
    return [dict(zip(columns, row)) for row in (response.result.data_array or [])]


def get_cities() -> list[str]:
    rows = _query(f"SELECT DISTINCT city FROM {GOLD_SCHEMA}.agg_restaurant_ranking ORDER BY city")
    return [row["city"] for row in rows]


def get_restaurant_ranking(city: str | None = None, keyword: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Restaurants ordered by rank_in_city (split-credit ranking, non-additive score)."""
    rows = _query(
        f"""
        SELECT restaurant_id, rank_in_city, restaurant_name, address, district, city, video_count,
               credited_views, credited_views_30d, like_rate, ranking_score, as_of_date_key
        FROM {GOLD_SCHEMA}.agg_restaurant_ranking
        WHERE (:city IS NULL OR city = :city)
          AND (:keyword IS NULL OR restaurant_name ILIKE concat('%', :keyword, '%')
                                OR address ILIKE concat('%', :keyword, '%'))
        ORDER BY city, rank_in_city, credited_views DESC
        LIMIT {int(limit)}
        """,
        {"city": city, "keyword": keyword},
    )
    for row in rows:
        for key in ("rank_in_city", "video_count", "credited_views", "credited_views_30d"):
            row[key] = int(row[key]) if row[key] is not None else None
        for key in ("like_rate", "ranking_score"):
            row[key] = float(row[key]) if row[key] is not None else None
    return rows


def get_restaurant_videos(restaurant_id: str) -> list[dict[str, Any]]:
    """Videos that mention a restaurant, with their latest snapshot counters."""
    rows = _query(
        f"""
        WITH latest AS (
            SELECT video_key, view_count, like_count,
                   ROW_NUMBER() OVER (PARTITION BY video_key ORDER BY snapshot_date_key DESC) AS rn
            FROM {GOLD_SCHEMA}.fact_video_daily_snapshot
        )
        SELECT v.title, v.video_url, l.view_count, l.like_count, m.mention_weight
        FROM {GOLD_SCHEMA}.fact_video_restaurant_mention m
        JOIN {GOLD_SCHEMA}.dim_restaurant r ON r.restaurant_key = m.restaurant_key
        JOIN {GOLD_SCHEMA}.dim_video v ON v.video_key = m.video_key
        LEFT JOIN latest l ON l.video_key = m.video_key AND l.rn = 1
        WHERE r.restaurant_id = :restaurant_id
        ORDER BY l.view_count DESC NULLS LAST
        """,
        {"restaurant_id": restaurant_id},
    )
    for row in rows:
        for key in ("view_count", "like_count"):
            row[key] = int(row[key]) if row[key] is not None else None
        row["mention_weight"] = float(row["mention_weight"]) if row["mention_weight"] is not None else None
    return rows
