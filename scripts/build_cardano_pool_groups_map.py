#!/usr/bin/env python3
"""Build a Dune-ready Cardano pool grouping CSV from cardano-community/pool_groups."""

from __future__ import annotations

import csv
import json
import pathlib
import subprocess
from collections import OrderedDict
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
POOL_GROUPS_REPO = ROOT / "data" / "pool_groups_repo"
OUTPUT_CSV = ROOT / "data" / "cardano_pool_groups_map.csv"

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
    value = value.strip()
    if value.lower() != value and value.upper() != value:
        raise ValueError(f"mixed-case bech32 value: {value}")
    value = value.lower()
    separator = value.rfind("1")
    if separator < 1:
        raise ValueError(f"missing bech32 separator: {value}")
    data = [BECH32_CHARSET.index(char) for char in value[separator + 1 :]]
    if bech32_polymod(bech32_hrp_expand(value[:separator]) + data) != 1:
        raise ValueError(f"invalid bech32 checksum: {value}")
    return value[:separator], data[:-6]


def convert_bits(data: list[int], from_bits: int, to_bits: int, pad: bool = False) -> bytes:
    acc = 0
    bits = 0
    maxv = (1 << to_bits) - 1
    result = []
    for value in data:
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            result.append((acc >> bits) & maxv)
    if pad and bits:
        result.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        raise ValueError("invalid trailing bits in bech32 data")
    return bytes(result)


def decode_pool_id(pool_id: str) -> str:
    hrp, data = bech32_decode(pool_id)
    if hrp != "pool":
        raise ValueError(f"expected pool bech32 HRP, got {hrp!r}: {pool_id}")
    decoded = convert_bits(data, 5, 8, False)
    if len(decoded) != 28:
        raise ValueError(f"expected 28-byte pool hash, got {len(decoded)} bytes: {pool_id}")
    return decoded.hex()


def load_commit_json(commit: str, filename: str) -> Any | None:
    try:
        text = subprocess.check_output(
            ["git", "show", f"{commit}:{filename}"],
            cwd=POOL_GROUPS_REPO,
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return json.loads(text)


def clean_group(group: Any) -> str | None:
    if group is None:
        return None
    value = str(group).strip()
    if not value or value.upper() in {"SINGLEPOOL", "SOLO", "NONE", "N/A"}:
        return None
    return value


def main() -> None:
    rows: OrderedDict[str, dict[str, str]] = OrderedDict()

    def put(pool_hash: str | None, pool_id: str, group: Any, source: str, source_detail: str) -> None:
        operator_group = clean_group(group)
        if not pool_hash or not operator_group:
            return
        rows[pool_hash.lower()] = {
            "pool_hash": pool_hash.lower(),
            "pool_id": pool_id,
            "operator_group": operator_group,
            "source": source,
            "source_detail": source_detail,
        }

    snapshots = [
        ("pool_groups_2020_11_27_793e6094", "793e6094"),
        ("pool_groups_2021_01_06_99b3c61d", "99b3c61d"),
    ]

    for source, commit in snapshots:
        pool_list = load_commit_json(commit, "pool_list.json")
        if not isinstance(pool_list, list):
            continue
        for row in pool_list:
            put(
                row.get("pool_hash"),
                "",
                row.get("cluster_name"),
                source,
                f"pool_list.json cluster_name; ticker={row.get('ticker')}; name={row.get('name')}",
            )

    for filename in ["spos.json", "pool_clusters.json"]:
        path = POOL_GROUPS_REPO / filename
        current = json.loads(path.read_text(encoding="utf-8"))
        for row in current:
            group = clean_group(row.get("group"))
            pool_id = row.get("pool_id_bech32")
            if not group or not pool_id:
                continue
            try:
                pool_hash = decode_pool_id(pool_id)
            except ValueError:
                continue
            put(
                pool_hash,
                pool_id,
                group,
                "pool_groups_current_HEAD",
                (
                    f"{filename} group; ticker={row.get('ticker')}; "
                    f"balanceanalytics_group={row.get('balanceanalytics_group')}; "
                    f"adastat_group={row.get('adastat_group')}"
                ),
            )

    OUTPUT_CSV.parent.mkdir(exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["pool_hash", "pool_id", "operator_group", "source", "source_detail"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(sorted(rows.values(), key=lambda row: (row["operator_group"], row["pool_hash"])))

    print(f"Wrote {len(rows):,} pool_groups rows to {OUTPUT_CSV}")
    print(f"Distinct groups: {len({row['operator_group'] for row in rows.values()}):,}")


if __name__ == "__main__":
    main()
