# Cardano Dune Research

Assets for Cardano stake concentration analysis on Dune.

## First Chart

- Dune query: https://dune.com/queries/7531566
- Dune visualization: https://dune.com/embeds/7531566/11482345
- Dune dashboard: https://dune.com/cerkoryn/cardano-stake-concentration-and-k-parameter-research

The deployed query computes total staked ADA and 51% Nakamoto coefficients by epoch. The pool-level coefficient is computed directly from `cardano.epoch_stake`.

The deployed operator-group line uses Dune's `cardano.off_chain_pool_data` ticker metadata as a runnable proxy because the current Dune MCP toolset does not expose CSV upload. The stricter Balance Analytics mapping is generated locally at `data/cardano_pool_group_map.csv`; `dune/cardano_stake_concentration.sql` contains an inline non-single-pool mapping version generated from the same source.

## Regenerate Mapping Assets

```powershell
python scripts\build_cardano_dune_assets.py
```
