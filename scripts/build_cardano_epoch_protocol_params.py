#!/usr/bin/env python3
"""Build a compact protocol-parameter CSV from cached Koios epoch params."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CACHE_FILE = ROOT / "data" / "koios_epoch_network_metrics_cache" / "epoch_params_all.json"
OUTPUT_FILE = ROOT / "data" / "cardano_epoch_protocol_params.csv"
SOURCE = "https://koios.tosidrop.me/api/v1/epoch_params"


def ada_from_lovelace(value: object) -> float:
    return int(value) / 1_000_000


def main() -> None:
    rows = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    output_rows = []
    for row in sorted(rows, key=lambda item: int(item["epoch_no"])):
        epoch = int(row["epoch_no"])
        if epoch < 210:
            continue

        output_rows.append(
            {
                "epoch": epoch,
                "k": int(row["optimal_pool_count"]),
                "decentralisation": row["decentralisation"],
                "min_pool_cost_ada": ada_from_lovelace(row["min_pool_cost"]),
                "source": SOURCE,
                "generated_at": generated_at,
            }
        )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "epoch",
                "k",
                "decentralisation",
                "min_pool_cost_ada",
                "source",
                "generated_at",
            ],
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Wrote {len(output_rows)} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
