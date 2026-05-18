WITH pool_group_map AS (
  SELECT
    pool_hash,
    operator_group
  FROM dune.cerkoryn.dataset_cardano_pool_group_map
),

pool_stake AS (
  SELECT
    epoch,
    pool_hash,
    SUM(stake_lovelace) AS stake_lovelace
  FROM cardano.epoch_stake
  WHERE stake_lovelace > 0
    AND pool_hash IS NOT NULL
  GROUP BY 1, 2
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
    COALESCE(g.operator_group, ps.pool_hash) AS entity_id,
    SUM(ps.stake_lovelace) AS stake_lovelace,
    'operator_group' AS entity_level
  FROM pool_stake ps
  LEFT JOIN pool_group_map g
    ON ps.pool_hash = g.pool_hash
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

mav AS (
  SELECT
    entity_level,
    epoch,
    MIN(entity_rank) AS mav
  FROM ranked
  WHERE CAST(cumulative_lovelace AS DOUBLE) >= CAST(total_lovelace AS DOUBLE) * 0.50
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
  MAX(CASE WHEN m.entity_level = 'pool' THEN m.mav END) AS ungrouped_mav,
  MAX(CASE WHEN m.entity_level = 'operator_group' THEN m.mav END) AS grouped_mav
FROM totals t
JOIN mav m
  ON t.epoch = m.epoch
GROUP BY 1, 2
ORDER BY 1;
