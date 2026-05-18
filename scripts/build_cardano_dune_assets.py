#!/usr/bin/env python3
"""Build Dune-ready Cardano stake concentration assets."""

from __future__ import annotations

import csv
import json
import pathlib
import urllib.request
from datetime import datetime, timezone


ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DUNE_DIR = ROOT / "dune"
GROUP_CSV = DATA_DIR / "cardano_pool_group_map.csv"
SQL_FILE = DUNE_DIR / "cardano_stake_concentration.sql"

GROUPDATA_URL = "https://www.balanceanalytics.io/api/groupdata.json"
SOURCE = "https://www.balanceanalytics.io/api/groupdata.json"


SQL_PREFIX = """WITH pool_group_map(pool_hash, operator_group) AS (
  VALUES
"""

SQL_SUFFIX = """
),

pool_stake AS (
  SELECT
    epoch,
    pool_hash,
    SUM(stake_lovelace) AS stake_lovelace
  FROM cardano.epoch_stake
  WHERE stake_lovelace > 0
    AND pool_hash IS NOT NULL
  GROUP BY 1, 2
),

pool_entities AS (
  SELECT
    epoch,
    pool_hash AS entity_id,
    stake_lovelace,
    'pool' AS entity_level
  FROM pool_stake
),

operator_entities AS (
  SELECT
    ps.epoch,
    COALESCE(g.operator_group, ps.pool_hash) AS entity_id,
    SUM(ps.stake_lovelace) AS stake_lovelace,
    'operator_group' AS entity_level
  FROM pool_stake ps
  LEFT JOIN pool_group_map g
    ON ps.pool_hash = g.pool_hash
  GROUP BY 1, 2
),

entity_stake AS (
  SELECT * FROM pool_entities
  UNION ALL
  SELECT * FROM operator_entities
),

ranked AS (
  SELECT
    entity_level,
    epoch,
    entity_id,
    stake_lovelace,
    SUM(stake_lovelace) OVER (PARTITION BY entity_level, epoch) AS total_lovelace,
    SUM(stake_lovelace) OVER (
      PARTITION BY entity_level, epoch
      ORDER BY stake_lovelace DESC, entity_id
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_lovelace,
    ROW_NUMBER() OVER (
      PARTITION BY entity_level, epoch
      ORDER BY stake_lovelace DESC, entity_id
    ) AS entity_rank
  FROM entity_stake
),

nc AS (
  SELECT
    entity_level,
    epoch,
    MIN(entity_rank) AS nakamoto_51
  FROM ranked
  WHERE CAST(cumulative_lovelace AS DOUBLE) >= CAST(total_lovelace AS DOUBLE) * 0.51
  GROUP BY 1, 2
),

totals AS (
  SELECT
    epoch,
    SUM(stake_lovelace) / 1e6 AS total_staked_ada
  FROM pool_stake
  GROUP BY 1
)

SELECT
  t.epoch,
  t.total_staked_ada,
  MAX(CASE WHEN n.entity_level = 'pool' THEN n.nakamoto_51 END) AS nakamoto_coefficient_pools_51,
  MAX(CASE WHEN n.entity_level = 'operator_group' THEN n.nakamoto_51 END) AS nakamoto_coefficient_operator_groups_51
FROM totals t
JOIN nc n
  ON t.epoch = n.epoch
GROUP BY 1, 2
ORDER BY 1;
"""


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def normalized_group(row: dict[str, str]) -> str:
    pool_hash = row["pool_hash"].strip()
    pool_group = row["pool_group"].strip()
    return pool_hash if pool_group == "SINGLEPOOL" else pool_group


def fetch_group_rows() -> list[dict[str, str]]:
    with urllib.request.urlopen(GROUPDATA_URL, timeout=30) as response:
        payload = json.load(response)
    rows = payload[0]["pool_group_json"]
    return sorted(rows, key=lambda row: row["pool_hash"])


def write_csv(rows: list[dict[str, str]], generated_at: str) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with GROUP_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["pool_hash", "operator_group", "source", "generated_at"],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "pool_hash": row["pool_hash"].strip(),
                    "operator_group": normalized_group(row),
                    "source": SOURCE,
                    "generated_at": generated_at,
                }
            )


def write_sql(rows: list[dict[str, str]], generated_at: str) -> None:
    DUNE_DIR.mkdir(exist_ok=True)
    values = []
    grouped_rows = [row for row in rows if row["pool_group"].strip() != "SINGLEPOOL"]
    for row in grouped_rows:
        values.append(
            "    ("
            + ", ".join(
                [
                    sql_string(row["pool_hash"].strip()),
                    sql_string(normalized_group(row)),
                ]
            )
            + ")"
        )
    SQL_FILE.write_text(SQL_PREFIX + ",\n".join(values) + SQL_SUFFIX, encoding="utf-8")


def main() -> None:
    rows = fetch_group_rows()
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    write_csv(rows, generated_at)
    write_sql(rows, generated_at)
    print(f"Wrote {len(rows):,} pool mappings to {GROUP_CSV}")
    print(
        "Wrote only non-SINGLEPOOL mappings into SQL; unmapped pools are singleton groups."
    )
    print(f"Wrote Dune SQL to {SQL_FILE}")


if __name__ == "__main__":
    main()
