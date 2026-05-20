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

epoch_network AS (
  SELECT
    CAST(epoch AS INTEGER) AS epoch,
    CAST(start_time AS BIGINT) AS start_time,
    CAST(end_time AS BIGINT) AS end_time
  FROM dune.cerkoryn.dataset_cardano_epoch_network_metrics
  WHERE CAST(epoch AS INTEGER) BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
),

price_daily_preferred AS (
  SELECT
    CAST(date_trunc('day', timestamp) AS DATE) AS price_day,
    AVG(price) AS ada_usd
  FROM prices.day
  WHERE lower(symbol) = 'ada'
    AND source = 'coinpaprika'
    AND blockchain = 'bnb'
    AND timestamp >= TIMESTAMP '2020-08-01 00:00:00'
  GROUP BY 1
),

price_daily_fallback AS (
  SELECT
    CAST(date_trunc('day', minute) AS DATE) AS price_day,
    AVG(price) AS ada_usd
  FROM prices.usd
  WHERE lower(symbol) = 'ada'
    AND blockchain = 'bnb'
    AND minute >= TIMESTAMP '2020-08-01 00:00:00'
  GROUP BY 1
),

price_daily AS (
  SELECT
    COALESCE(p.price_day, f.price_day) AS price_day,
    COALESCE(p.ada_usd, f.ada_usd) AS ada_usd
  FROM price_daily_preferred p
  FULL OUTER JOIN price_daily_fallback f
    ON p.price_day = f.price_day
),

epoch_prices AS (
  SELECT
    en.epoch,
    AVG(pd.ada_usd) AS ada_usd,
    COUNT(pd.price_day) AS price_days
  FROM epoch_network en
  LEFT JOIN price_daily pd
    ON pd.price_day >= CAST(from_unixtime(en.start_time) AS DATE)
   AND pd.price_day < CAST(from_unixtime(en.end_time) AS DATE)
  GROUP BY 1
),

pool_group_map AS (
  SELECT
    pool_hash,
    operator_group
  FROM dune.cerkoryn.dataset_cardano_pool_groups_map
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

total_stake AS (
  SELECT
    epoch,
    SUM(stake_lovelace) / 1e6 AS total_staked_ada
  FROM pool_stake
  GROUP BY 1
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
    ) / 1e6 AS declared_map_ada
  FROM pledge_efficiency_ranked
  GROUP BY 1
),

metrics AS (
  SELECT
    ts.epoch,
    ts.total_staked_ada,
    ts.total_staked_ada * 0.50 AS ada_needed_to_control_50pct_stake,
    ep.ada_usd,
    ts.total_staked_ada * 0.50 * ep.ada_usd AS usd_needed_to_control_50pct_stake,
    map.declared_map_ada,
    map.declared_map_ada * ep.ada_usd AS usd_value_of_declared_map
  FROM total_stake ts
  LEFT JOIN epoch_prices ep
    ON ts.epoch = ep.epoch
  LEFT JOIN min_aggregate_pledge map
    ON ts.epoch = map.epoch
)

SELECT
  es.epoch,
  m.total_staked_ada,
  m.ada_needed_to_control_50pct_stake,
  m.ada_usd,
  m.usd_needed_to_control_50pct_stake,
  m.declared_map_ada,
  m.usd_value_of_declared_map
FROM epoch_spine es
LEFT JOIN metrics m
  ON es.epoch = m.epoch
ORDER BY 1;
