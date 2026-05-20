#!/usr/bin/env python3
"""Build Cardano epoch network metrics from a Koios-compatible API."""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_CSV = DATA_DIR / "cardano_epoch_network_metrics.csv"
INLINE_SQL = ROOT / "dune" / "cardano_usd_to_30pct_pledge_inline.sql"
CACHE_DIR = DATA_DIR / "koios_epoch_network_metrics_cache"

DEFAULT_BASE_URL = "https://koios.tosidrop.me/api/v1"
DEFAULT_MIN_EPOCH = 210


def request_json(url: str, retries: int = 6) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url,
                headers={"user-agent": "cardano-dune-research/1.0"},
            )
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504}:
                raise
            retry_after = exc.headers.get("Retry-After")
            if exc.code == 429 and retry_after and retry_after.isdigit():
                sleep_seconds = int(retry_after)
            else:
                sleep_seconds = min(2**attempt, 60)
            time.sleep(sleep_seconds)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(min(2**attempt, 60))

    raise RuntimeError(f"request failed after {retries} attempts: {url}") from last_error


def endpoint_url(base_url: str, endpoint: str, params: dict[str, Any] | None = None) -> str:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    if not params:
        return url
    return f"{url}?{urllib.parse.urlencode(params)}"


def cache_path(endpoint: str, suffix: str = "all") -> pathlib.Path:
    safe_endpoint = endpoint.strip("/").replace("/", "_")
    return CACHE_DIR / f"{safe_endpoint}_{suffix}.json"


def load_or_fetch(url: str, path: pathlib.Path, use_cache: bool) -> Any:
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    payload = request_json(url)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return payload


def fetch_tip_epoch(base_url: str, use_cache: bool) -> int:
    payload = load_or_fetch(
        endpoint_url(base_url, "tip"),
        cache_path("tip"),
        use_cache,
    )
    if not payload:
        raise RuntimeError("Koios-compatible /tip returned no rows")
    epoch = payload[0].get("epoch_no")
    if epoch is None:
        raise RuntimeError("Koios-compatible /tip response is missing epoch_no")
    return int(epoch)


def fetch_rows(base_url: str, endpoint: str, use_cache: bool) -> list[dict[str, Any]]:
    payload = load_or_fetch(
        endpoint_url(base_url, endpoint),
        cache_path(endpoint),
        use_cache,
    )
    if not isinstance(payload, list):
        raise RuntimeError(f"/{endpoint} returned {type(payload).__name__}, expected list")
    return payload


def fetch_epoch_row(base_url: str, endpoint: str, epoch: int, use_cache: bool) -> dict[str, Any] | None:
    payload = load_or_fetch(
        endpoint_url(base_url, endpoint, {"_epoch_no": epoch}),
        cache_path(endpoint, str(epoch)),
        use_cache,
    )
    if not payload:
        return None
    if not isinstance(payload, list):
        raise RuntimeError(f"/{endpoint}?_epoch_no={epoch} returned {type(payload).__name__}, expected list")
    return payload[0]


def index_by_epoch(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        epoch = row.get("epoch_no")
        if epoch is not None:
            indexed[int(epoch)] = row
    return indexed


def ensure_epoch_rows(
    base_url: str,
    endpoint: str,
    rows_by_epoch: dict[int, dict[str, Any]],
    min_epoch: int,
    latest_epoch: int,
    use_cache: bool,
) -> dict[int, dict[str, Any]]:
    missing = [epoch for epoch in range(min_epoch, latest_epoch + 1) if epoch not in rows_by_epoch]
    if not missing:
        return rows_by_epoch

    for epoch in missing:
        row = fetch_epoch_row(base_url, endpoint, epoch, use_cache)
        if row:
            rows_by_epoch[epoch] = row
    return rows_by_epoch


def parse_int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    return int(value)


def build_rows(base_url: str, min_epoch: int, latest_epoch: int, use_cache: bool) -> list[dict[str, Any]]:
    epoch_info = index_by_epoch(fetch_rows(base_url, "epoch_info", use_cache))
    epoch_params = index_by_epoch(fetch_rows(base_url, "epoch_params", use_cache))
    totals = index_by_epoch(fetch_rows(base_url, "totals", use_cache))

    epoch_info = ensure_epoch_rows(base_url, "epoch_info", epoch_info, min_epoch, latest_epoch, use_cache)
    epoch_params = ensure_epoch_rows(base_url, "epoch_params", epoch_params, min_epoch, latest_epoch, use_cache)

    source = ";".join(
        [
            endpoint_url(base_url, "epoch_info"),
            endpoint_url(base_url, "epoch_params"),
            endpoint_url(base_url, "totals"),
        ]
    )
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows: list[dict[str, Any]] = []

    for epoch in range(min_epoch, latest_epoch + 1):
        info = epoch_info.get(epoch)
        params = epoch_params.get(epoch)
        total = totals.get(epoch)
        if not info or not params or not total:
            continue

        start_time = parse_int(info, "start_time")
        end_time = parse_int(info, "end_time")
        optimal_pool_count = parse_int(params, "optimal_pool_count")
        circulation_lovelace = parse_int(total, "circulation")
        if (
            start_time is None
            or end_time is None
            or optimal_pool_count is None
            or circulation_lovelace is None
        ):
            continue

        rows.append(
            {
                "epoch": epoch,
                "start_time": start_time,
                "end_time": end_time,
                "optimal_pool_count": optimal_pool_count,
                "circulation_lovelace": circulation_lovelace,
                "source": source,
                "generated_at": generated_at,
            }
        )

    return rows


def write_csv(rows: list[dict[str, Any]], output_csv: pathlib.Path) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "epoch",
                "start_time",
                "end_time",
                "optimal_pool_count",
                "circulation_lovelace",
                "source",
                "generated_at",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_inline_sql(rows: list[dict[str, Any]], output_sql: pathlib.Path) -> None:
    values = []
    for row in rows:
        values.append(
            "    "
            f"({row['epoch']}, {row['start_time']}, {row['end_time']}, {row['optimal_pool_count']}, "
            f"CAST('{row['circulation_lovelace']}' AS DECIMAL(38, 0)))"
        )
    values_sql = ",\n".join(values)

    sql = f"""WITH epoch_network(epoch, start_time, end_time, k, circulation_lovelace) AS (
  VALUES
{values_sql}
),

price_daily_preferred AS (
  SELECT
    CAST(date_trunc('day', timestamp) AS DATE) AS price_day,
    AVG(price) AS ada_usd
  FROM prices.day
  WHERE lower(symbol) = 'ada'
    AND source = 'coinpaprika'
    AND blockchain = 'bnb'
    AND timestamp >= TIMESTAMP '2020-08-01 00:00:00'
  GROUP BY 1
),

price_daily_fallback AS (
  SELECT
    CAST(date_trunc('day', minute) AS DATE) AS price_day,
    AVG(price) AS ada_usd
  FROM prices.usd
  WHERE lower(symbol) = 'ada'
    AND blockchain = 'bnb'
    AND minute >= TIMESTAMP '2020-08-01 00:00:00'
  GROUP BY 1
),

price_daily AS (
  SELECT
    COALESCE(p.price_day, f.price_day) AS price_day,
    COALESCE(p.ada_usd, f.ada_usd) AS ada_usd
  FROM price_daily_preferred p
  FULL OUTER JOIN price_daily_fallback f
    ON p.price_day = f.price_day
),

epoch_prices AS (
  SELECT
    en.epoch,
    AVG(pd.ada_usd) AS ada_usd,
    COUNT(pd.price_day) AS price_days
  FROM epoch_network en
  LEFT JOIN price_daily pd
    ON pd.price_day >= CAST(from_unixtime(en.start_time) AS DATE)
   AND pd.price_day < CAST(from_unixtime(en.end_time) AS DATE)
  GROUP BY 1
)

SELECT
  en.epoch,
  en.k,
  CAST(en.circulation_lovelace AS DOUBLE) / 1e6 AS circulating_supply_ada,
  (CAST(en.circulation_lovelace AS DOUBLE) / 1e6) / en.k AS saturation_ada,
  ((CAST(en.circulation_lovelace AS DOUBLE) / 1e6) / en.k) * 0.30 AS pledge_30pct_ada,
  ep.ada_usd,
  ((CAST(en.circulation_lovelace AS DOUBLE) / 1e6) / en.k) * 0.30 * ep.ada_usd AS usd_to_30pct_pledge
FROM epoch_network en
JOIN epoch_prices ep
  ON en.epoch = ep.epoch
WHERE ep.ada_usd IS NOT NULL
ORDER BY 1;
"""

    output_sql.parent.mkdir(parents=True, exist_ok=True)
    output_sql.write_text(sql, encoding="utf-8")


def summarize_k(rows: list[dict[str, Any]]) -> list[tuple[int, int]]:
    transitions: list[tuple[int, int]] = []
    previous_k: int | None = None
    for row in rows:
        k = int(row["optimal_pool_count"])
        if k != previous_k:
            transitions.append((int(row["epoch"]), k))
            previous_k = k
    return transitions


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Dune-ready Cardano epoch network metrics from Tosidrop or Koios."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--min-epoch", type=int, default=DEFAULT_MIN_EPOCH)
    parser.add_argument("--latest-epoch", type=int)
    parser.add_argument("--include-current", action="store_true")
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT_CSV)
    parser.add_argument("--inline-sql-output", type=pathlib.Path)
    parser.add_argument("--use-cache", action="store_true")
    args = parser.parse_args()

    tip_epoch = fetch_tip_epoch(args.base_url, args.use_cache)
    latest_epoch = args.latest_epoch
    if latest_epoch is None:
        latest_epoch = tip_epoch if args.include_current else tip_epoch - 1

    rows = build_rows(args.base_url, args.min_epoch, latest_epoch, args.use_cache)
    write_csv(rows, args.output)
    if args.inline_sql_output:
        write_inline_sql(rows, args.inline_sql_output)

    if not rows:
        raise RuntimeError("no epoch network rows were written")

    first_epoch = rows[0]["epoch"]
    last_epoch = rows[-1]["epoch"]
    transitions = ", ".join(f"{epoch}: k={k}" for epoch, k in summarize_k(rows))

    print(f"Wrote {len(rows):,} rows to {args.output}")
    print(f"Epoch coverage: {first_epoch} through {last_epoch}")
    print(f"Tip epoch from API: {tip_epoch}; latest epoch written: {latest_epoch}")
    print(f"k transitions: {transitions}")
    if args.inline_sql_output:
        print(f"Wrote inline Dune SQL to {args.inline_sql_output}")


if __name__ == "__main__":
    main()
