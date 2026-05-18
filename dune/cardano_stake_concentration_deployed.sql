WITH pool_stake AS (
  SELECT
    epoch,
    pool_hash,
    SUM(stake_lovelace) AS stake_lovelace
  FROM cardano.epoch_stake
  WHERE stake_lovelace > 0
    AND pool_hash IS NOT NULL
  GROUP BY 1, 2
),

pool_metadata AS (
  SELECT
    pool_hash,
    NULLIF(TRIM(MAX_BY(ticker, fetched_at)), '') AS ticker
  FROM cardano.off_chain_pool_data
  GROUP BY 1
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
    COALESCE(pm.ticker, ps.pool_hash) AS entity_id,
    SUM(ps.stake_lovelace) AS stake_lovelace,
    'operator_group' AS entity_level
  FROM pool_stake ps
  LEFT JOIN pool_metadata pm
    ON ps.pool_hash = pm.pool_hash
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

nc AS (
  SELECT
    entity_level,
    epoch,
    MIN(entity_rank) AS nakamoto_51
  FROM ranked
  WHERE CAST(cumulative_lovelace AS DOUBLE) >= CAST(total_lovelace AS DOUBLE) * 0.51
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
  MAX(CASE WHEN n.entity_level = 'pool' THEN n.nakamoto_51 END) AS nakamoto_coefficient_pools_51,
  MAX(CASE WHEN n.entity_level = 'operator_group' THEN n.nakamoto_51 END) AS nakamoto_coefficient_operator_groups_51
FROM totals t
JOIN nc n
  ON t.epoch = n.epoch
GROUP BY 1, 2
ORDER BY 1;
