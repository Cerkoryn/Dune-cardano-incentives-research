# Cardano Dune Research

Assets for Cardano stake concentration analysis on Dune.

## First Chart

- Dune query: https://dune.com/queries/7531566
- Dune visualization: https://dune.com/embeds/7531566/11482345
- Dune dashboard: https://dune.com/cerkoryn/cardano-incentives-research

The deployed query computes total staked ADA and 50% Minimum Attack Vector (MAV) values by epoch. The ungrouped MAV treats each stake pool independently.

The grouped MAV uses the uploaded Balance Analytics mapping at `dune.cerkoryn.dataset_cardano_pool_group_map` to combine pools identified as belonging to the same operator group. The CSV stores Balance pool IDs plus locally decoded hex pool hashes, so the query joins directly to `cardano.epoch_stake` without relying on Dune pool metadata.

## Regenerate Mapping Assets

```powershell
python scripts\build_cardano_dune_assets.py
```

## Upload the Accurate Group Map to Dune

Uploaded table currently used by the query:

```sql
dune.cerkoryn.dataset_cardano_pool_group_map
```

If the table is recreated under another handle or dataset name, update `dune/cardano_stake_concentration_uploaded_group_map.sql` and the saved Dune query with the new table name.

Expected CSV columns:

```csv
pool_hash,pool_id,operator_group,source,generated_at
```

`pool_hash` is the decoded hex hash used by `cardano.epoch_stake`. `pool_id` is the original Balance bech32 pool ID. Balance `SINGLEPOOL` entries use their own hex `pool_hash` as `operator_group`.

## QA Queries

- `dune/cardano_mav_qa_targets.sql` compares 50% and 51% grouped and ungrouped MAV for epochs 227, 234, and 628.
- `dune/cardano_mav_qa_top30_groups.sql` lists the top 30 grouped entities for those epochs with cumulative stake and threshold-crossing ranks.
