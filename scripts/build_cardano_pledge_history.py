#!/usr/bin/env python3
"""Build Dune-ready Cardano pool pledge interval assets from Koios."""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PLEDGE_INTERVALS_CSV = DATA_DIR / "cardano_pool_pledge_intervals.csv"
OWNER_INTERVALS_CSV = DATA_DIR / "cardano_pool_owner_intervals.csv"

KOIOS_BASE_URL = "https://api.koios.rest/api/v1"
SOURCE_UPDATES = "https://api.koios.rest/api/v1/pool_updates"
SOURCE_TIP = "https://api.koios.rest/api/v1/tip"


def request_json(url: str, retries: int = 5) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(min(2**attempt, 30))
    raise RuntimeError(f"Koios request failed after {retries} attempts: {url}") from last_error


def fetch_tip_epoch(base_url: str) -> int:
    payload = request_json(f"{base_url.rstrip('/')}/tip")
    if not payload:
        raise RuntimeError("Koios /tip returned no rows")
    epoch = payload[0].get("epoch_no")
    if epoch is None:
        raise RuntimeError("Koios /tip response is missing epoch_no")
    return int(epoch)


def fetch_pool_updates(base_url: str, page_size: int) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({"limit": page_size, "offset": offset})
        page = request_json(f"{base_url.rstrip('/')}/pool_updates?{query}")
        if not page:
            break
        updates.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return updates


def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def build_intervals(
    updates: list[dict[str, Any]],
    latest_epoch: int,
    min_epoch: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    retirements_by_pool: dict[str, list[int]] = defaultdict(list)

    for update in updates:
        pool_hash = update.get("pool_id_hex")
        if not pool_hash:
            continue
        if update.get("update_type") == "deregistration":
            retiring_epoch = parse_int(update.get("retiring_epoch"))
            if retiring_epoch is not None:
                retirements_by_pool[pool_hash].append(retiring_epoch)
            continue
        active_epoch = parse_int(update.get("active_epoch_no"))
        pledge = parse_int(update.get("pledge"))
        if active_epoch is None or pledge is None:
            continue
        by_pool[pool_hash].append(update)

    pledge_rows: list[dict[str, Any]] = []
    owner_rows: list[dict[str, Any]] = []

    for pool_hash, pool_updates in by_pool.items():
        pool_updates.sort(
            key=lambda update: (
                parse_int(update.get("active_epoch_no")) or 0,
                parse_int(update.get("block_time")) or 0,
                str(update.get("tx_hash") or ""),
            )
        )
        retirement_epochs = sorted(retirements_by_pool.get(pool_hash, []))

        for index, update in enumerate(pool_updates):
            start_epoch = parse_int(update.get("active_epoch_no"))
            if start_epoch is None:
                continue

            next_start = (
                parse_int(pool_updates[index + 1].get("active_epoch_no"))
                if index + 1 < len(pool_updates)
                else None
            )
            end_epoch = latest_epoch if next_start is None else next_start - 1
            for retirement_epoch in retirement_epochs:
                if retirement_epoch >= start_epoch:
                    end_epoch = min(end_epoch, retirement_epoch - 1)
                    break
            if min_epoch is not None:
                start_epoch = max(start_epoch, min_epoch)
            if start_epoch > end_epoch:
                continue

            pool_id = str(update.get("pool_id_bech32") or "")
            interval_base = {
                "pool_hash": pool_hash,
                "pool_id": pool_id,
                "start_epoch": start_epoch,
                "end_epoch": end_epoch,
            }
            pledge_rows.append(
                {
                    **interval_base,
                    "declared_pledge_lovelace": parse_int(update.get("pledge")) or 0,
                }
            )

            owners = update.get("owners") or []
            for owner in sorted(set(owners)):
                owner_rows.append({**interval_base, "owner_stake_address": owner})

    return pledge_rows, owner_rows


def write_csv(path: pathlib.Path, fieldnames: list[str], rows: list[dict[str, Any]], generated_at: str) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "source": SOURCE_UPDATES, "generated_at": generated_at})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build compact pool pledge/owner interval CSVs for Dune uploads."
    )
    parser.add_argument("--base-url", default=KOIOS_BASE_URL)
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--latest-epoch", type=int)
    parser.add_argument("--min-epoch", type=int, default=208)
    args = parser.parse_args()

    latest_epoch = args.latest_epoch if args.latest_epoch is not None else fetch_tip_epoch(args.base_url)
    updates = fetch_pool_updates(args.base_url, args.page_size)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    pledge_rows, owner_rows = build_intervals(updates, latest_epoch, args.min_epoch)

    write_csv(
        PLEDGE_INTERVALS_CSV,
        [
            "pool_hash",
            "pool_id",
            "start_epoch",
            "end_epoch",
            "declared_pledge_lovelace",
            "source",
            "generated_at",
        ],
        pledge_rows,
        generated_at,
    )
    write_csv(
        OWNER_INTERVALS_CSV,
        [
            "pool_hash",
            "pool_id",
            "start_epoch",
            "end_epoch",
            "owner_stake_address",
            "source",
            "generated_at",
        ],
        owner_rows,
        generated_at,
    )

    print(f"Fetched {len(updates):,} Koios pool update rows")
    print(f"Wrote {len(pledge_rows):,} pledge intervals to {PLEDGE_INTERVALS_CSV}")
    print(f"Wrote {len(owner_rows):,} owner intervals to {OWNER_INTERVALS_CSV}")
    print(f"Latest epoch used for open intervals: {latest_epoch}")


if __name__ == "__main__":
    main()
