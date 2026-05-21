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

protocol_params AS (
  SELECT
    CAST(epoch AS INTEGER) AS epoch,
    COALESCE(CAST(decentralisation AS DOUBLE), 0) AS decentralisation
  FROM dune.cerkoryn.dataset_cardano_epoch_protocol_params
  WHERE CAST(epoch AS INTEGER) BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
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

pool_declared_pledge AS (
  SELECT
    ps.epoch,
    ps.pool_hash,
    MAX(
      CASE
        WHEN CAST(pi.declared_pledge_lovelace AS DECIMAL(38, 0)) > 45000000000000000
          THEN CAST(0 AS DECIMAL(38, 0))
        ELSE CAST(pi.declared_pledge_lovelace AS DECIMAL(38, 0))
      END
    ) AS declared_pledge_lovelace
  FROM pool_stake ps
  LEFT JOIN dune.cerkoryn.dataset_cardano_pool_pledge_intervals pi
    ON ps.pool_hash = pi.pool_hash
    AND ps.epoch BETWEEN CAST(pi.start_epoch AS INTEGER) AND CAST(pi.end_epoch AS INTEGER)
  GROUP BY 1, 2
),

pool_metrics AS (
  SELECT
    ps.epoch,
    ps.pool_hash,
    ps.stake_lovelace,
    COALESCE(dp.declared_pledge_lovelace, 0) AS declared_pledge_lovelace
  FROM pool_stake ps
  LEFT JOIN pool_declared_pledge dp
    ON ps.epoch = dp.epoch
    AND ps.pool_hash = dp.pool_hash
),

entity_metrics AS (
  SELECT
    pm.epoch,
    COALESCE(g.operator_group, pm.pool_hash) AS entity_id,
    SUM(pm.stake_lovelace) AS stake_lovelace,
    SUM(pm.declared_pledge_lovelace) AS declared_pledge_lovelace
  FROM pool_metrics pm
  LEFT JOIN pool_group_map g
    ON pm.pool_hash = g.pool_hash
  GROUP BY 1, 2
),

stake_ranked AS (
  SELECT
    epoch,
    entity_id,
    stake_lovelace,
    declared_pledge_lovelace,
    SUM(stake_lovelace) OVER (PARTITION BY epoch) AS total_lovelace,
    SUM(stake_lovelace) OVER (
      PARTITION BY epoch
      ORDER BY stake_lovelace DESC, entity_id
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_lovelace,
    ROW_NUMBER() OVER (
      PARTITION BY epoch
      ORDER BY stake_lovelace DESC, entity_id
    ) AS entity_rank
  FROM entity_metrics
),

grouped_mav AS (
  SELECT
    epoch,
    MIN(entity_rank) AS grouped_mav
  FROM stake_ranked
  WHERE CAST(cumulative_lovelace AS DOUBLE) >= CAST(total_lovelace AS DOUBLE) * 0.50
  GROUP BY 1
),

grouped_block_production_entities AS (
  SELECT
    em.epoch,
    em.entity_id,
    CAST(em.stake_lovelace AS DOUBLE) * (1 - COALESCE(pp.decentralisation, 0)) AS production_weight
  FROM entity_metrics em
  LEFT JOIN protocol_params pp
    ON em.epoch = pp.epoch

  UNION ALL

  SELECT
    em.epoch,
    'federated_core_nodes' AS entity_id,
    SUM(CAST(em.stake_lovelace AS DOUBLE)) * COALESCE(MAX(pp.decentralisation), 0) AS production_weight
  FROM entity_metrics em
  LEFT JOIN protocol_params pp
    ON em.epoch = pp.epoch
  GROUP BY 1
),

grouped_block_production_ranked AS (
  SELECT
    epoch,
    entity_id,
    production_weight,
    SUM(production_weight) OVER (PARTITION BY epoch) AS total_production_weight,
    SUM(production_weight) OVER (
      PARTITION BY epoch
      ORDER BY production_weight DESC, entity_id
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_production_weight,
    ROW_NUMBER() OVER (
      PARTITION BY epoch
      ORDER BY production_weight DESC, entity_id
    ) AS entity_rank
  FROM grouped_block_production_entities
  WHERE production_weight > 0
),

grouped_mav_with_d AS (
  SELECT
    epoch,
    MIN(entity_rank) AS grouped_mav_with_d
  FROM grouped_block_production_ranked
  WHERE cumulative_production_weight >= total_production_weight * 0.50
  GROUP BY 1
),

pool_stake_ranked AS (
  SELECT
    epoch,
    pool_hash AS entity_id,
    stake_lovelace,
    declared_pledge_lovelace,
    SUM(stake_lovelace) OVER (PARTITION BY epoch) AS total_lovelace,
    SUM(stake_lovelace) OVER (
      PARTITION BY epoch
      ORDER BY stake_lovelace DESC, pool_hash
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_lovelace,
    ROW_NUMBER() OVER (
      PARTITION BY epoch
      ORDER BY stake_lovelace DESC, pool_hash
    ) AS entity_rank
  FROM pool_metrics
),

ungrouped_mav AS (
  SELECT
    epoch,
    MIN(entity_rank) AS ungrouped_mav
  FROM pool_stake_ranked
  WHERE CAST(cumulative_lovelace AS DOUBLE) >= CAST(total_lovelace AS DOUBLE) * 0.50
  GROUP BY 1
),

pledge_efficiency_ranked AS (
  SELECT
    epoch,
    entity_id,
    stake_lovelace,
    declared_pledge_lovelace,
    SUM(stake_lovelace) OVER (PARTITION BY epoch) AS total_lovelace,
    SUM(stake_lovelace) OVER (
      PARTITION BY epoch
      ORDER BY
        CASE WHEN declared_pledge_lovelace = 0 THEN 1 ELSE 0 END DESC,
        CAST(stake_lovelace AS DOUBLE) / NULLIF(CAST(declared_pledge_lovelace AS DOUBLE), 0) DESC,
        stake_lovelace DESC,
        entity_id
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_stake_lovelace,
    SUM(declared_pledge_lovelace) OVER (
      PARTITION BY epoch
      ORDER BY
        CASE WHEN declared_pledge_lovelace = 0 THEN 1 ELSE 0 END DESC,
        CAST(stake_lovelace AS DOUBLE) / NULLIF(CAST(declared_pledge_lovelace AS DOUBLE), 0) DESC,
        stake_lovelace DESC,
        entity_id
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_pledge_lovelace
  FROM entity_metrics
),

min_aggregate_pledge AS (
  SELECT
    epoch,
    MIN(
      CASE
        WHEN CAST(cumulative_stake_lovelace AS DOUBLE) >= CAST(total_lovelace AS DOUBLE) * 0.50
          THEN cumulative_pledge_lovelace
      END
    ) / 1e6 AS declared_map
  FROM pledge_efficiency_ranked
  GROUP BY 1
),

pool_pledge_efficiency_ranked AS (
  SELECT
    epoch,
    pool_hash AS entity_id,
    stake_lovelace,
    declared_pledge_lovelace,
    SUM(stake_lovelace) OVER (PARTITION BY epoch) AS total_lovelace,
    SUM(stake_lovelace) OVER (
      PARTITION BY epoch
      ORDER BY
        CASE WHEN declared_pledge_lovelace = 0 THEN 1 ELSE 0 END DESC,
        CAST(stake_lovelace AS DOUBLE) / NULLIF(CAST(declared_pledge_lovelace AS DOUBLE), 0) DESC,
        stake_lovelace DESC,
        pool_hash
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_stake_lovelace,
    SUM(declared_pledge_lovelace) OVER (
      PARTITION BY epoch
      ORDER BY
        CASE WHEN declared_pledge_lovelace = 0 THEN 1 ELSE 0 END DESC,
        CAST(stake_lovelace AS DOUBLE) / NULLIF(CAST(declared_pledge_lovelace AS DOUBLE), 0) DESC,
        stake_lovelace DESC,
        pool_hash
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_pledge_lovelace
  FROM pool_metrics
),

ungrouped_min_aggregate_pledge AS (
  SELECT
    epoch,
    MIN(
      CASE
        WHEN CAST(cumulative_stake_lovelace AS DOUBLE) >= CAST(total_lovelace AS DOUBLE) * 0.50
          THEN cumulative_pledge_lovelace
      END
    ) / 1e6 AS ungrouped_map
  FROM pool_pledge_efficiency_ranked
  GROUP BY 1
),

metrics AS (
  SELECT
    map.epoch,
    mav.grouped_mav,
    mavd.grouped_mav_with_d,
    umav.ungrouped_mav,
    map.declared_map,
    umap.ungrouped_map
  FROM min_aggregate_pledge map
  JOIN grouped_mav mav
    ON map.epoch = mav.epoch
  LEFT JOIN grouped_mav_with_d mavd
    ON map.epoch = mavd.epoch
  LEFT JOIN ungrouped_mav umav
    ON map.epoch = umav.epoch
  LEFT JOIN ungrouped_min_aggregate_pledge umap
    ON map.epoch = umap.epoch
)

SELECT
  es.epoch,
  m.grouped_mav,
  m.grouped_mav_with_d,
  m.grouped_mav - m.grouped_mav_with_d AS d_impact_on_grouped_mav,
  m.ungrouped_mav,
  m.declared_map,
  m.ungrouped_map
FROM epoch_spine es
LEFT JOIN metrics m
  ON es.epoch = m.epoch
ORDER BY 1;
