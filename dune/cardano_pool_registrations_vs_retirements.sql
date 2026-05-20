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

first_registrations AS (
  SELECT
    pool_hash,
    MIN(registration_epoch) AS registration_epoch
  FROM dune.cerkoryn.dataset_cardano_pool_registrations
  WHERE pool_hash IS NOT NULL
    AND registration_epoch IS NOT NULL
  GROUP BY 1
),

first_retirements AS (
  SELECT
    pool_hash,
    MIN(retiring_epoch) AS retiring_epoch
  FROM dune.cerkoryn.dataset_cardano_pool_retirements
  WHERE pool_hash IS NOT NULL
    AND retiring_epoch IS NOT NULL
  GROUP BY 1
),

registrations_by_epoch AS (
  SELECT
    registration_epoch AS epoch,
    COUNT(*) AS pool_registrations
  FROM first_registrations
  WHERE registration_epoch BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
  GROUP BY 1
),

retirements_by_epoch AS (
  SELECT
    retiring_epoch AS epoch,
    COUNT(*) AS pool_retirements
  FROM first_retirements
  WHERE retiring_epoch BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
  GROUP BY 1
)

SELECT
  es.epoch,
  COALESCE(r.pool_registrations, 0) AS pool_registrations,
  COALESCE(t.pool_retirements, 0) AS pool_retirements,
  COALESCE(r.pool_registrations, 0) - COALESCE(t.pool_retirements, 0) AS net_pool_change
FROM epoch_spine es
LEFT JOIN registrations_by_epoch r
  ON es.epoch = r.epoch
LEFT JOIN retirements_by_epoch t
  ON es.epoch = t.epoch
ORDER BY 1;
