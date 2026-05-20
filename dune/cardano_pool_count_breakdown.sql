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

inactive_pools AS (
  SELECT DISTINCT pool_hash
  FROM dune.cerkoryn.dataset_cardano_ior_inactive_pools
),

retired_pools AS (
  SELECT
    pool_hash,
    MIN(retiring_epoch) AS retiring_epoch
  FROM dune.cerkoryn.dataset_cardano_pool_retirements
  WHERE pool_hash IS NOT NULL
    AND retiring_epoch IS NOT NULL
  GROUP BY 1
),

raw_pool_stake AS (
  SELECT
    epoch,
    pool_hash,
    SUM(stake_lovelace) AS pool_stake_lovelace
  FROM cardano.epoch_stake
  WHERE stake_lovelace > 0
    AND pool_hash IS NOT NULL
    AND epoch BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
  GROUP BY 1, 2
),

filtered_pool_stake AS (
  SELECT rps.*
  FROM raw_pool_stake rps
  LEFT JOIN inactive_pools ip
    ON rps.pool_hash = ip.pool_hash
  LEFT JOIN retired_pools rp
    ON rps.pool_hash = rp.pool_hash
    AND rps.epoch >= rp.retiring_epoch
  WHERE ip.pool_hash IS NULL
    AND rp.pool_hash IS NULL
),

filtered_block_producers AS (
  SELECT DISTINCT
    b.epoch,
    b.pool_hash
  FROM cardano.block b
  JOIN filtered_pool_stake fps
    ON b.epoch = fps.epoch
    AND b.pool_hash = fps.pool_hash
  WHERE b.epoch BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
    AND b.pool_hash IS NOT NULL
),

counts AS (
  SELECT
    epoch,
    COUNT(*) AS total_active_pools,
    SUM(CASE WHEN pool_stake_lovelace < 3000000000000 THEN 1 ELSE 0 END) AS pools_lt_3m_stake,
    SUM(CASE WHEN pool_stake_lovelace >= 3000000000000 THEN 1 ELSE 0 END) AS pools_gte_3m_stake
  FROM filtered_pool_stake
  GROUP BY 1
),

block_counts AS (
  SELECT
    epoch,
    COUNT(*) AS block_producing_pools
  FROM filtered_block_producers
  GROUP BY 1
),

excluded_counts AS (
  SELECT
    rps.epoch,
    COUNT(DISTINCT CASE WHEN ip.pool_hash IS NOT NULL THEN rps.pool_hash END) AS excluded_ior_inactive_pools,
    COUNT(DISTINCT CASE WHEN rp.pool_hash IS NOT NULL THEN rps.pool_hash END) AS excluded_retired_pools
  FROM raw_pool_stake rps
  LEFT JOIN inactive_pools ip
    ON rps.pool_hash = ip.pool_hash
  LEFT JOIN retired_pools rp
    ON rps.pool_hash = rp.pool_hash
    AND rps.epoch >= rp.retiring_epoch
  GROUP BY 1
)

SELECT
  es.epoch,
  c.total_active_pools,
  c.pools_lt_3m_stake,
  c.pools_gte_3m_stake,
  CASE
    WHEN c.total_active_pools IS NULL THEN NULL
    ELSE COALESCE(bc.block_producing_pools, 0)
  END AS block_producing_pools,
  ec.excluded_ior_inactive_pools,
  ec.excluded_retired_pools
FROM epoch_spine es
LEFT JOIN counts c
  ON es.epoch = c.epoch
LEFT JOIN block_counts bc
  ON es.epoch = bc.epoch
LEFT JOIN excluded_counts ec
  ON es.epoch = ec.epoch
ORDER BY 1;
