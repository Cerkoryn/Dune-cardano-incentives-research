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

protocol_params AS (
  SELECT
    CAST(epoch AS INTEGER) AS epoch,
    CAST(k AS DOUBLE) AS k,
    CAST(decentralisation AS DOUBLE) AS decentralisation,
    CAST(min_pool_cost_ada AS DOUBLE) AS min_pool_cost_ada
  FROM dune.cerkoryn.dataset_cardano_epoch_protocol_params
  WHERE CAST(epoch AS INTEGER) BETWEEN (SELECT start_epoch FROM epoch_bounds) AND (SELECT end_epoch FROM epoch_bounds)
)

SELECT
  es.epoch,
  pp.k,
  pp.decentralisation,
  pp.min_pool_cost_ada
FROM epoch_spine es
LEFT JOIN protocol_params pp
  ON es.epoch = pp.epoch
ORDER BY 1;
