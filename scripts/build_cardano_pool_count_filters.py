#!/usr/bin/env python3
"""Build pool-count-only filter CSVs for the IOR-aligned chart."""

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
INACTIVE_POOLS_CSV = DATA_DIR / "cardano_ior_inactive_pools.csv"
POOL_RETIREMENTS_CSV = DATA_DIR / "cardano_pool_retirements.csv"
POOL_REGISTRATIONS_CSV = DATA_DIR / "cardano_pool_registrations.csv"

IOR_INACTIVE_POOLS_URL = (
    "https://raw.githubusercontent.com/input-output-hk/spo-incentives/main/"
    "report-november-2025/scripts/inactive-pools.txt"
)
KOIOS_BASE_URL = "https://koios.tosidrop.me/api/v1"

BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def bech32_polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for index in range(5):
            if (top >> index) & 1:
                chk ^= generator[index]
    return chk


def bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def bech32_decode(value: str) -> tuple[str, list[int]]:
    if value.lower() != value and value.upper() != value:
        raise ValueError(f"mixed-case bech32 value: {value}")
    value = value.lower()
    separator = value.rfind("1")
    if separator < 1:
        raise ValueError(f"missing bech32 separator: {value}")
    if separator + 7 > len(value):
        raise ValueError(f"bech32 data too short: {value}")
    hrp = value[:separator]
    data = []
    for char in value[separator + 1 :]:
        try:
            data.append(BECH32_CHARSET.index(char))
        except ValueError as exc:
            raise ValueError(f"invalid bech32 character {char!r}: {value}") from exc
    if bech32_polymod(bech32_hrp_expand(hrp) + data) != 1:
        raise ValueError(f"invalid bech32 checksum: {value}")
    return hrp, data[:-6]


def convert_bits(data: list[int], from_bits: int, to_bits: int, pad: bool) -> bytes:
    acc = 0
    bits = 0
    maxv = (1 << to_bits) - 1
    result = []
    for value in data:
        if value < 0 or value >> from_bits:
            raise ValueError("invalid bech32 data value")
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            result.append((acc >> bits) & maxv)
    if pad:
        if bits:
            result.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        raise ValueError("invalid trailing bits in bech32 data")
    return bytes(result)


def decode_pool_id(pool_id: str) -> str:
    hrp, data = bech32_decode(pool_id.strip())
    if hrp != "pool":
        raise ValueError(f"expected pool bech32 HRP, got {hrp!r}: {pool_id}")
    decoded = convert_bits(data, 5, 8, False)
    if len(decoded) != 28:
        raise ValueError(f"expected 28-byte pool hash, got {len(decoded)} bytes: {pool_id}")
    return decoded.hex()


def request_text(url: str, retries: int = 5) -> str:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"user-agent": "cardano-dune-research/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(min(2**attempt, 30))
    raise RuntimeError(f"Request failed after {retries} attempts: {url}") from last_error


def request_json(url: str, retries: int = 5) -> Any:
    return json.loads(request_text(url, retries))


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


def write_csv(path: pathlib.Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build_inactive_pool_rows(source_url: str, generated_at: str) -> list[dict[str, Any]]:
    pool_ids = sorted({line.strip() for line in request_text(source_url).splitlines() if line.strip()})
    rows = []
    for pool_id in pool_ids:
        rows.append(
            {
                "pool_hash": decode_pool_id(pool_id),
                "pool_id": pool_id,
                "status": "Inactive",
                "source": source_url,
                "generated_at": generated_at,
            }
        )
    return rows


def build_registration_rows(
    updates: list[dict[str, Any]], base_url: str, generated_at: str
) -> list[dict[str, Any]]:
    rows_by_pool: dict[str, dict[str, Any]] = {}
    source = f"{base_url.rstrip('/')}/pool_updates"
    for update in updates:
        if update.get("update_type") == "deregistration":
            continue
        pool_hash = update.get("pool_id_hex")
        registration_epoch = parse_int(update.get("active_epoch_no"))
        if not pool_hash or registration_epoch is None:
            continue
        current = rows_by_pool.get(str(pool_hash))
        if current is not None and registration_epoch >= current["registration_epoch"]:
            continue
        rows_by_pool[str(pool_hash)] = {
            "pool_hash": pool_hash,
            "pool_id": str(update.get("pool_id_bech32") or ""),
            "registration_epoch": registration_epoch,
            "source": source,
            "generated_at": generated_at,
        }
    return sorted(rows_by_pool.values(), key=lambda row: (row["registration_epoch"], row["pool_hash"]))


def build_retirement_rows(
    updates: list[dict[str, Any]], base_url: str, generated_at: str
) -> list[dict[str, Any]]:
    rows_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    source = f"{base_url.rstrip('/')}/pool_updates"
    for update in updates:
        if update.get("update_type") != "deregistration":
            continue
        pool_hash = update.get("pool_id_hex")
        retiring_epoch = parse_int(update.get("retiring_epoch"))
        if not pool_hash or retiring_epoch is None:
            continue
        key = (str(pool_hash), retiring_epoch)
        rows_by_key[key] = {
            "pool_hash": pool_hash,
            "pool_id": str(update.get("pool_id_bech32") or ""),
            "retiring_epoch": retiring_epoch,
            "source": source,
            "generated_at": generated_at,
        }
    return sorted(rows_by_key.values(), key=lambda row: (row["pool_hash"], row["retiring_epoch"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build pool-count-only inactive and retirement filter CSVs.")
    parser.add_argument("--ior-inactive-url", default=IOR_INACTIVE_POOLS_URL)
    parser.add_argument("--base-url", default=KOIOS_BASE_URL)
    parser.add_argument("--page-size", type=int, default=1000)
    args = parser.parse_args()

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    inactive_rows = build_inactive_pool_rows(args.ior_inactive_url, generated_at)
    updates = fetch_pool_updates(args.base_url, args.page_size)
    registration_rows = build_registration_rows(updates, args.base_url, generated_at)
    retirement_rows = build_retirement_rows(updates, args.base_url, generated_at)

    write_csv(
        INACTIVE_POOLS_CSV,
        ["pool_hash", "pool_id", "status", "source", "generated_at"],
        inactive_rows,
    )
    write_csv(
        POOL_RETIREMENTS_CSV,
        ["pool_hash", "pool_id", "retiring_epoch", "source", "generated_at"],
        retirement_rows,
    )
    write_csv(
        POOL_REGISTRATIONS_CSV,
        ["pool_hash", "pool_id", "registration_epoch", "source", "generated_at"],
        registration_rows,
    )

    print(f"Wrote {len(inactive_rows):,} IOR inactive pools to {INACTIVE_POOLS_CSV}")
    print(f"Wrote {len(retirement_rows):,} pool retirement rows to {POOL_RETIREMENTS_CSV}")
    print(f"Wrote {len(registration_rows):,} first pool registrations to {POOL_REGISTRATIONS_CSV}")


if __name__ == "__main__":
    main()
