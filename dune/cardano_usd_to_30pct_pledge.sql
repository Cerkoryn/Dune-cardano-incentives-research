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
    CAST(end_time AS BIGINT) AS end_time,
    CAST(optimal_pool_count AS INTEGER) AS k,
    CAST(circulation_lovelace AS DECIMAL(38, 0)) AS circulation_lovelace
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

usd_pledge AS (
  SELECT
    en.epoch,
    en.k,
    CAST(en.circulation_lovelace AS DOUBLE) / 1e6 AS circulating_supply_ada,
    (CAST(en.circulation_lovelace AS DOUBLE) / 1e6) / en.k AS saturation_ada,
    ((CAST(en.circulation_lovelace AS DOUBLE) / 1e6) / en.k) * 0.30 AS pledge_30pct_ada,
    ep.ada_usd,
    ((CAST(en.circulation_lovelace AS DOUBLE) / 1e6) / en.k) * 0.30 * ep.ada_usd AS usd_to_30pct_pledge
  FROM epoch_network en
  JOIN epoch_prices ep
    ON en.epoch = ep.epoch
  WHERE ep.ada_usd IS NOT NULL
),

pool_stake AS (
  SELECT
    epoch,
    SUM(stake_lovelace) AS total_active_stake_lovelace
  FROM cardano.epoch_stake
  WHERE epoch BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
    AND stake_lovelace > 0
    AND pool_hash IS NOT NULL
  GROUP BY 1
),

gross_rewards AS (
  SELECT
    epoch,
    MAX(pool_rewards_pot) AS gross_pool_rewards_lovelace
  FROM cardano.adapot
  WHERE epoch BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
  GROUP BY 1
),

gross_roi AS (
  SELECT
    ps.epoch,
    CAST(ps.total_active_stake_lovelace AS DOUBLE) / 1e6 AS total_active_stake_ada,
    CAST(gr.gross_pool_rewards_lovelace AS DOUBLE) / 1e6 AS gross_pool_rewards_ada,
    CAST(gr.gross_pool_rewards_lovelace AS DOUBLE)
      / CAST(ps.total_active_stake_lovelace AS DOUBLE) AS gross_per_epoch_roi,
    CAST(gr.gross_pool_rewards_lovelace AS DOUBLE)
      / CAST(ps.total_active_stake_lovelace AS DOUBLE) * 73 * 100 AS gross_annualized_roi_pct
  FROM pool_stake ps
  JOIN gross_rewards gr
    ON ps.epoch = gr.epoch
  WHERE ps.total_active_stake_lovelace > 0
    AND gr.gross_pool_rewards_lovelace > 0
),

metrics AS (
  SELECT
    up.epoch,
    up.k,
    up.circulating_supply_ada,
    up.saturation_ada,
    up.pledge_30pct_ada,
    up.ada_usd,
    up.usd_to_30pct_pledge,
    gr.total_active_stake_ada,
    gr.gross_pool_rewards_ada,
    gr.gross_per_epoch_roi,
    gr.gross_annualized_roi_pct
  FROM usd_pledge up
  JOIN gross_roi gr
    ON up.epoch = gr.epoch
)

SELECT
  es.epoch,
  m.k,
  m.circulating_supply_ada,
  m.saturation_ada,
  m.pledge_30pct_ada,
  m.ada_usd,
  m.usd_to_30pct_pledge,
  m.total_active_stake_ada,
  m.gross_pool_rewards_ada,
  m.gross_per_epoch_roi,
  m.gross_annualized_roi_pct
FROM epoch_spine es
LEFT JOIN metrics m
  ON es.epoch = m.epoch
ORDER BY 1;
