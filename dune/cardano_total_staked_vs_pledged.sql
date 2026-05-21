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

epoch_network AS (
  SELECT
    CAST(epoch AS INTEGER) AS epoch,
    CAST(optimal_pool_count AS INTEGER) AS k,
    CAST(circulation_lovelace AS DECIMAL(38, 0)) AS circulation_lovelace
  FROM dune.cerkoryn.dataset_cardano_epoch_network_metrics
  WHERE CAST(epoch AS INTEGER) BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
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
    en.k,
    en.circulation_lovelace,
    CAST(en.circulation_lovelace AS DOUBLE) / 1e6 / en.k AS saturation_ada,
    CAST(en.circulation_lovelace AS DOUBLE) / 1000 AS saturation_lovelace_if_k_1000,
    CAST(en.circulation_lovelace AS DOUBLE) / en.k * 0.30 AS pledge_30pct_saturation_lovelace,
    CAST(en.circulation_lovelace AS DOUBLE) / 1e6 / en.k * 0.30 AS pledge_30pct_saturation_ada,
    COALESCE(CAST(dp.declared_pledge_lovelace AS DOUBLE), 0) AS declared_pledge_lovelace
  FROM pool_stake ps
  JOIN epoch_network en
    ON ps.epoch = en.epoch
  LEFT JOIN pool_declared_pledge dp
    ON ps.epoch = dp.epoch
    AND ps.pool_hash = dp.pool_hash
),

metrics AS (
  SELECT
    epoch,
    MAX(k) AS k,
    MAX(saturation_ada) AS saturation_ada,
    MAX(pledge_30pct_saturation_ada) AS pledge_30pct_saturation_ada,
    MAX(CAST(circulation_lovelace AS DOUBLE)) / 1e6 AS circulating_supply_ada,
    SUM(stake_lovelace) / 1e6 AS total_staked_ada,
    SUM(declared_pledge_lovelace) / 1e6 AS total_declared_pledge_ada,
    SUM(GREATEST(CAST(stake_lovelace AS DOUBLE) - saturation_lovelace_if_k_1000, 0)) / 1e6 AS oversat_if_k_1000_ada,
    SUM(stake_lovelace) / MAX(CAST(circulation_lovelace AS DOUBLE)) * 100 AS staking_participation_pct,
    SUM(
      CASE
        WHEN declared_pledge_lovelace >= pledge_30pct_saturation_lovelace THEN 1
        ELSE 0
      END
    ) AS pools_declared_pledge_gte_30pct_saturation
  FROM pool_metrics
  GROUP BY 1
)

SELECT
  es.epoch,
  m.k,
  m.saturation_ada,
  m.pledge_30pct_saturation_ada,
  m.circulating_supply_ada,
  m.total_staked_ada,
  m.total_declared_pledge_ada,
  m.oversat_if_k_1000_ada,
  m.staking_participation_pct,
  m.pools_declared_pledge_gte_30pct_saturation
FROM epoch_spine es
LEFT JOIN metrics m
  ON es.epoch = m.epoch
ORDER BY 1;
