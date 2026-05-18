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

BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


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

mav AS (
  SELECT
    entity_level,
    epoch,
    MIN(entity_rank) AS mav
  FROM ranked
  WHERE CAST(cumulative_lovelace AS DOUBLE) >= CAST(total_lovelace AS DOUBLE) * 0.50
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
  MAX(CASE WHEN m.entity_level = 'pool' THEN m.mav END) AS ungrouped_mav,
  MAX(CASE WHEN m.entity_level = 'operator_group' THEN m.mav END) AS grouped_mav
FROM totals t
JOIN mav m
  ON t.epoch = m.epoch
GROUP BY 1, 2
ORDER BY 1;
"""


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def bech32_polymod(values: list[int]) -> int:
    generators = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for index, generator in enumerate(generators):
            if (top >> index) & 1:
                chk ^= generator
    return chk


def bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def bech32_decode(value: str) -> tuple[str, list[int]]:
    if value.lower() != value and value.upper() != value:
        raise ValueError(f"mixed-case bech32 value: {value}")

    value = value.lower()
    separator_index = value.rfind("1")
    if separator_index < 1:
        raise ValueError(f"missing bech32 separator: {value}")
    if separator_index + 7 > len(value):
        raise ValueError(f"bech32 data too short: {value}")

    hrp = value[:separator_index]
    data = []
    for char in value[separator_index + 1 :]:
        try:
            data.append(BECH32_CHARSET.index(char))
        except ValueError as exc:
            raise ValueError(f"invalid bech32 character {char!r}: {value}") from exc

    if bech32_polymod(bech32_hrp_expand(hrp) + data) != 1:
        raise ValueError(f"invalid bech32 checksum: {value}")
    return hrp, data[:-6]


def convert_bits(data: list[int], from_bits: int, to_bits: int, pad: bool) -> list[int]:
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1

    for value in data:
        if value < 0 or value >> from_bits:
            raise ValueError(f"invalid {from_bits}-bit value: {value}")
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)

    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        raise ValueError("invalid trailing bits in bech32 data")

    return ret


def decode_pool_id(pool_id: str) -> str:
    hrp, data = bech32_decode(pool_id.strip())
    if hrp != "pool":
        raise ValueError(f"expected pool bech32 HRP, got {hrp!r}: {pool_id}")
    decoded = bytes(convert_bits(data, 5, 8, False))
    if len(decoded) != 28:
        raise ValueError(f"expected 28-byte pool hash, got {len(decoded)} bytes: {pool_id}")
    return decoded.hex()


def normalized_group(row: dict[str, str], pool_hash: str) -> str:
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
            fieldnames=["pool_hash", "pool_id", "operator_group", "source", "generated_at"],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            pool_id = row["pool_hash"].strip()
            pool_hash = decode_pool_id(pool_id)
            writer.writerow(
                {
                    "pool_hash": pool_hash,
                    "pool_id": pool_id,
                    "operator_group": normalized_group(row, pool_hash),
                    "source": SOURCE,
                    "generated_at": generated_at,
                }
            )


def write_sql(rows: list[dict[str, str]], generated_at: str) -> None:
    DUNE_DIR.mkdir(exist_ok=True)
    values = []
    grouped_rows = [row for row in rows if row["pool_group"].strip() != "SINGLEPOOL"]
    for row in grouped_rows:
        pool_hash = decode_pool_id(row["pool_hash"].strip())
        values.append(
            "    ("
            + ", ".join(
                [
                    sql_string(pool_hash),
                    sql_string(normalized_group(row, pool_hash)),
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
        "Decoded Balance bech32 pool IDs to hex pool hashes for direct Dune joins."
    )
    print(f"Wrote Dune SQL to {SQL_FILE}")


if __name__ == "__main__":
    main()
