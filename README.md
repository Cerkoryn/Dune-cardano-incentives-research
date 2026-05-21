# Cardano Dune Research

Assets for the Cardano incentives research dashboard on Dune:
https://dune.com/cerkoryn/cardano-incentives-research

The production data flow keeps each source narrow:

- Dune `cardano.epoch_stake` for active delegated stake.
- Dune `cardano.block` for block-producing pool counts.
- Dune `cardano.adapot` for gross reward-pot values.
- Koios/Tosidrop-derived CSVs for declared pledge, pool lifecycle, protocol params, and network metrics.
- `cardano-community/pool_groups` for MAV/MAP and sSPO/MPO operator grouping.

All visible charts use fixed epoch bounds of `210..630` so dashboard x-axes stay aligned.

## Live Dashboard Queries

| Chart | Query | Visualization | Local SQL |
| --- | --- | --- | --- |
| Protocol Parameters | https://dune.com/queries/7540221 | https://dune.com/embeds/7540221/11491433 | `dune/cardano_protocol_parameter_timeline.sql` |
| MAV vs MAP | https://dune.com/queries/7532894 | https://dune.com/embeds/7532894/11483818 | `dune/cardano_mav_min_aggregate_pledge.sql` |
| >50% Stake/Pledge Cost | https://dune.com/queries/7544957 | https://dune.com/embeds/7544957/11496455 | `dune/cardano_stake_control_cost.sql` |
| Pledge vs Stake | https://dune.com/queries/7532966 | https://dune.com/embeds/7532966/11483875 | `dune/cardano_total_staked_vs_pledged.sql` |
| Pledge USD Cost vs Staking Rewards | https://dune.com/queries/7538277 | https://dune.com/embeds/7538277/11489404 | `dune/cardano_usd_to_30pct_pledge.sql` |
| Active Pools | https://dune.com/queries/7538507 | https://dune.com/embeds/7538507/11489651 | `dune/cardano_pool_count_breakdown.sql` |
| Single Pools vs MPOs | https://dune.com/queries/7540345 | https://dune.com/embeds/7540345/11491600 | `dune/cardano_sspo_vs_mpo_timeline.sql` |
| Pool Registrations vs Retirements | https://dune.com/queries/7541511 | https://dune.com/embeds/7541511/11492807 | `dune/cardano_pool_registrations_vs_retirements.sql` |

## Uploaded CSV Tables

These uploaded Dune tables are used by at least one live dashboard query:

| Dune table | Local CSV | Used by |
| --- | --- | --- |
| `dune.cerkoryn.dataset_cardano_epoch_network_metrics` | `data/cardano_epoch_network_metrics.csv` | pledge cost, ROI, staking participation, saturation |
| `dune.cerkoryn.dataset_cardano_epoch_protocol_params` | `data/cardano_epoch_protocol_params.csv` | protocol parameter timeline |
| `dune.cerkoryn.dataset_cardano_ior_inactive_pools` | `data/cardano_ior_inactive_pools.csv` | active pool count chart only |
| `dune.cerkoryn.dataset_cardano_pool_groups_map` | `data/cardano_pool_groups_map.csv` | MAV/MAP, stake-control cost, and sSPO/MPO charts |
| `dune.cerkoryn.dataset_cardano_pool_pledge_intervals` | `data/cardano_pool_pledge_intervals.csv` | declared pledge and MAP charts |
| `dune.cerkoryn.dataset_cardano_pool_registrations` | `data/cardano_pool_registrations.csv` | pool lifecycle chart |
| `dune.cerkoryn.dataset_cardano_pool_retirements` | `data/cardano_pool_retirements.csv` | active pool count and lifecycle charts |

`dune.cerkoryn.dataset_cardano_pool_owner_intervals` and `dune.cerkoryn.dataset_cardano_pool_group_map` are visible in the Dune upload UI but are not referenced by any live dashboard query. Owner intervals were generated for the abandoned live-pledge approach, and `cardano_pool_group_map` was the earlier Balance Analytics grouping table.

## Regeneration Scripts

| Script | Primary output |
| --- | --- |
| `scripts/build_cardano_epoch_network_metrics.py` | `data/cardano_epoch_network_metrics.csv` |
| `scripts/build_cardano_epoch_protocol_params.py` | `data/cardano_epoch_protocol_params.csv` |
| `scripts/build_cardano_pool_groups_map.py` | `data/cardano_pool_groups_map.csv` |
| `scripts/build_cardano_pledge_history.py` | `data/cardano_pool_pledge_intervals.csv` |
| `scripts/build_cardano_pool_count_filters.py` | `data/cardano_ior_inactive_pools.csv`, `data/cardano_pool_registrations.csv`, `data/cardano_pool_retirements.csv` |

`scripts/build_cardano_pledge_history.py` can also generate owner-interval data, but owner intervals are intentionally excluded from current production SQL.

## Metric Notes

- MAV means Minimum Attack Vector and is synonymous here with Nakamoto Coefficient.
- MAP means Min Aggregate Pledge and uses declared pledge only.
- Grouped MAV/MAP uses `cardano-community/pool_groups`; ungrouped MAV/MAP treats each pool independently.
- `Group MAV w/d` includes the Shelley-era `d` parameter as a federated block-production entity; after `d=0`, it equals grouped MAV.
- Declared pledge values above 45B ADA are treated as malformed and filtered to zero in SQL.
- The active pool count chart has local-only filters: it excludes the Input Output Research November 2025 inactive-pool list and pools retired by epoch.

MAP reference: https://dl.acm.org/doi/fullHtml/10.1145/3533271.3561787
