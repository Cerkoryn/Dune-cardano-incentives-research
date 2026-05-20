WITH epoch_bounds AS (
  SELECT
    210 AS start_epoch,
    630 AS end_epoch
),

epoch_spine AS (
  SELECT epoch
  FROM epoch_bounds
  CROSS JOIN UNNEST(SEQUENCE(start_epoch, end_epoch)) AS t(epoch)
),

pool_group_map AS (
  SELECT
    pool_hash,
    operator_group
  FROM dune.cerkoryn.dataset_cardano_pool_groups_map
),

operator_classification AS (
  SELECT
    operator_group,
    COUNT(DISTINCT pool_hash) AS mapped_pool_count,
    CASE
      WHEN COUNT(DISTINCT pool_hash) > 1 THEN 'MPO'
      ELSE 'sSPO'
    END AS operator_type
  FROM pool_group_map
  GROUP BY 1
),

pool_stake AS (
  SELECT
    epoch,
    pool_hash,
    SUM(stake_lovelace) AS stake_lovelace
  FROM cardano.epoch_stake
  WHERE stake_lovelace > 0
    AND pool_hash IS NOT NULL
    AND epoch BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
  GROUP BY 1, 2
),

pool_operator AS (
  SELECT
    ps.epoch,
    ps.pool_hash,
    COALESCE(g.operator_group, ps.pool_hash) AS operator_group,
    CASE
      WHEN g.operator_group IS NULL THEN 'sSPO'
      ELSE c.operator_type
    END AS operator_type,
    ps.stake_lovelace,
    CASE WHEN g.operator_group IS NULL THEN 1 ELSE 0 END AS is_unmapped_active_pool
  FROM pool_stake ps
  LEFT JOIN pool_group_map g
    ON ps.pool_hash = g.pool_hash
  LEFT JOIN operator_classification c
    ON g.operator_group = c.operator_group
),

operator_epoch_stake AS (
  SELECT
    epoch,
    operator_group,
    operator_type,
    SUM(stake_lovelace) AS stake_lovelace,
    SUM(is_unmapped_active_pool) AS unmapped_active_pools
  FROM pool_operator
  GROUP BY 1, 2, 3
),

metrics AS (
  SELECT
    epoch,
    SUM(CASE WHEN operator_type = 'sSPO' THEN 1 ELSE 0 END) AS sspo_count,
    SUM(CASE WHEN operator_type = 'MPO' THEN 1 ELSE 0 END) AS mpo_count,
    SUM(CASE WHEN operator_type = 'sSPO' THEN stake_lovelace ELSE 0 END) / 1e6 AS sspo_stake_ada,
    SUM(CASE WHEN operator_type = 'MPO' THEN stake_lovelace ELSE 0 END) / 1e6 AS mpo_stake_ada,
    SUM(unmapped_active_pools) AS unmapped_active_pools,
    COUNT(*) AS total_active_operator_groups,
    SUM(stake_lovelace) / 1e6 AS total_stake_ada
  FROM operator_epoch_stake
  GROUP BY 1
)

SELECT
  es.epoch,
  m.sspo_count,
  m.mpo_count,
  m.sspo_stake_ada,
  m.mpo_stake_ada,
  m.unmapped_active_pools,
  m.total_active_operator_groups,
  m.total_stake_ada
FROM epoch_spine es
LEFT JOIN metrics m
  ON es.epoch = m.epoch
ORDER BY 1;
