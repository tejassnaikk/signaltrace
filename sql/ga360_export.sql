-- Reconstructed from the loader's expected schema; the original export query was not saved.
--
-- Source: bigquery-public-data.google_analytics_sample.ga_sessions_*
-- (Google Merchandise Store, 2016-08-01 to 2017-08-01). Two result sets, each exported as CSV.
--
-- Result 1 -> data/raw/ga_sessions_daily.csv  (read by ingest/load_ga.py)
-- Result 2 -> data/raw/order_concentration.csv (table `order_concentration`)
--
-- Checked against ingest/load_ga.py, column by column (read_csv_auto, header=true):
--   date          CAST(date AS DATE)         -> DATE   here: PARSE_DATE, exported as YYYY-MM-DD
--   channel       read as-is (VARCHAR)       -> channelGrouping, e.g. 'Organic Search', '(Other)'
--   device        read as-is (VARCHAR)       -> device.deviceCategory: desktop / mobile / tablet
--   country       read as-is (VARCHAR)       -> geoNetwork.country, 10 named countries + 'Other'
--   sessions      CAST(sessions AS BIGINT)   -> SUM(totals.visits)
--   transactions  CAST(transactions AS BIGINT) -> SUM(totals.transactions), NULL as 0
--   revenue       CAST(revenue AS DOUBLE)    -> SUM(totals.totalTransactionRevenue) / 1e6, NULL as 0
-- load_ga.py selects exactly these 7 columns; extra columns would be ignored, missing ones fail.
--
-- Facts checked against the existing data/signaltrace.duckdb (read-only):
--   * fact_sessions has 11 distinct countries: Brazil, Canada, Germany, India, Japan, Thailand,
--     Turkey, United Kingdom, United States, Vietnam, and 'Other' (everything else).
--   * date range is 2016-08-01 to 2017-07-31. The 2017-08-01 stub day (2,556 sessions) was deleted
--     from both tables after loading (notes/baseline.md), so it is excluded here. load_ga.py
--     prints "expected calendar : 366"; with this range it will report 365 days.
--   * 2016-08-03 has traffic and zero transactions in the data (a data gap, notes/baseline.md).
--
-- NOT verifiable without BigQuery access (assumptions, marked below):
--   * how the original grouped countries (the list below is the 10 names present in the loaded table);
--   * how the original deduplicated orders for Result 2. In the loaded table order_count is
--     <= transactions on every day (201 days lower, 163 equal), e.g. 26 vs 80 on 2017-03-04,
--     which is consistent with counting distinct transactionId. total_revenue matches
--     metrics_daily.revenue to 1e-11 in the loaded table; this reconstruction may differ on days
--     with repeated transaction ids.
--   * no loader for order_concentration is in this repo; load the CSV into DuckDB as a table with
--     the columns below (date DATE, order_count BIGINT, total_revenue DOUBLE, max_order DOUBLE,
--     median_order DOUBLE, top_order_share DOUBLE).

-- ======================= Result 1: ga_sessions_daily.csv =======================
SELECT
  PARSE_DATE('%Y%m%d', date)                                   AS date,
  channelGrouping                                              AS channel,
  device.deviceCategory                                        AS device,
  CASE WHEN geoNetwork.country IN (
         'United States', 'India', 'Canada', 'United Kingdom', 'Germany',
         'Japan', 'Brazil', 'Thailand', 'Vietnam', 'Turkey')
       THEN geoNetwork.country ELSE 'Other' END                AS country,   -- assumption
  SUM(totals.visits)                                           AS sessions,
  SUM(IFNULL(totals.transactions, 0))                          AS transactions,
  SUM(IFNULL(totals.totalTransactionRevenue, 0)) / 1e6         AS revenue
FROM `bigquery-public-data.google_analytics_sample.ga_sessions_*`
WHERE _TABLE_SUFFIX BETWEEN '20160801' AND '20170731'
GROUP BY date, channel, device, country
ORDER BY date, channel, device, country;

-- ================= Result 2: order_concentration.csv =================
-- One row per day with at least one order (2016-08-03 has none, so 364 rows).
-- Columns as in the loaded table: date, order_count, total_revenue, max_order, median_order,
-- top_order_share (= max_order / total_revenue, rounded to 4 places in the loaded table).
WITH orders AS (                                               -- assumption: distinct transactionId
  SELECT
    PARSE_DATE('%Y%m%d', date)                                 AS date,
    h.transaction.transactionId                                AS transaction_id,
    MAX(h.transaction.transactionRevenue) / 1e6                AS revenue
  FROM `bigquery-public-data.google_analytics_sample.ga_sessions_*`,
       UNNEST(hits) AS h
  WHERE _TABLE_SUFFIX BETWEEN '20160801' AND '20170731'
    AND h.transaction.transactionId IS NOT NULL
    AND h.transaction.transactionRevenue > 0
  GROUP BY date, transaction_id
)
SELECT
  date,
  COUNT(*)                                                     AS order_count,
  SUM(revenue)                                                 AS total_revenue,
  MAX(revenue)                                                 AS max_order,
  APPROX_QUANTILES(revenue, 2)[OFFSET(1)]                      AS median_order,
  ROUND(MAX(revenue) / SUM(revenue), 4)                        AS top_order_share
FROM orders
GROUP BY date
ORDER BY date;
