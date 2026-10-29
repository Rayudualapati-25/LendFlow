-- The loan book at the end of the day :as_of in India, by days past due, in the buckets RBI
-- uses for term loans (RBI/2021-2022/125): current, SMA-0 (1-30 days), SMA-1 (31-60),
-- SMA-2 (61-90) and NPA (more than 90).
--
-- Classification runs at day-end: an instalment still unpaid at the end of its due date is
-- 1 day past due that day, and a loan's days past due count from its oldest unpaid
-- instalment. Payments count from the date they were made in India, so the report shows
-- the book as it stood at that day-end even if the instalment was paid later.
--
-- book        : loans disbursed by the end of the day
-- instalments : their instalments, and whether each had been paid by then
-- prepaid     : principal paid ahead of the schedule by then (a foreclosure pays it all)
-- per_loan    : principal still owed, the oldest overdue due date and the overdue amount;
--               loans that owed nothing by then had been repaid and are left out
-- classified  : each loan's days past due and bucket
-- buckets     : every bucket, so that an empty one is still listed, with zeros
--
-- Window functions compare each bucket with the whole book:
--   share_pct   : the bucket's outstanding principal as a share of the book (SUM() OVER ())
--   at_risk_pct : this bucket and every worse one, a running total from NPA upwards; the
--                 SMA-1 row is the "portfolio at risk over 30 days" (PAR30)
WITH book AS (
    SELECT id, principal
    FROM loans
    WHERE (disbursed_at AT TIME ZONE 'Asia/Kolkata')::date <= :as_of
),
instalments AS (
    SELECT
        repayments.loan_id,
        repayments.due_date,
        repayments.amount_due,
        repayments.principal_component,
        coalesce((repayments.paid_at AT TIME ZONE 'Asia/Kolkata')::date <= :as_of, false) AS paid
    FROM repayments
    JOIN book ON book.id = repayments.loan_id
),
prepaid AS (
    SELECT loan_id, sum(principal) AS principal
    FROM prepayments
    WHERE (received_at AT TIME ZONE 'Asia/Kolkata')::date <= :as_of
    GROUP BY loan_id
),
per_loan AS (
    SELECT
        book.principal
            - coalesce(sum(instalments.principal_component) FILTER (WHERE paid), 0.00)
            - coalesce(prepaid.principal, 0.00)
            AS outstanding,
        min(instalments.due_date) FILTER (WHERE NOT paid AND due_date <= :as_of)
            AS oldest_overdue,
        coalesce(
            sum(instalments.amount_due) FILTER (WHERE NOT paid AND due_date <= :as_of), 0.00
        ) AS overdue
    FROM book
    LEFT JOIN instalments ON instalments.loan_id = book.id
    LEFT JOIN prepaid ON prepaid.loan_id = book.id
    GROUP BY book.id, book.principal, prepaid.principal
),
classified AS (
    SELECT
        outstanding,
        overdue,
        CASE
            WHEN oldest_overdue IS NULL THEN 'current'
            WHEN :as_of - oldest_overdue + 1 <= 30 THEN 'sma_0'
            WHEN :as_of - oldest_overdue + 1 <= 60 THEN 'sma_1'
            WHEN :as_of - oldest_overdue + 1 <= 90 THEN 'sma_2'
            ELSE 'npa'
        END AS bucket
    FROM per_loan
    WHERE outstanding > 0
),
buckets (position, bucket, days_past_due_min, days_past_due_max) AS (
    VALUES
        (1, 'current', 0, 0),
        (2, 'sma_0', 1, 30),
        (3, 'sma_1', 31, 60),
        (4, 'sma_2', 61, 90),
        (5, 'npa', 91, NULL)
),
totals AS (
    SELECT
        buckets.position,
        buckets.bucket,
        buckets.days_past_due_min,
        buckets.days_past_due_max,
        count(classified.bucket) AS loans,
        coalesce(sum(classified.outstanding), 0.00) AS outstanding,
        coalesce(sum(classified.overdue), 0.00) AS overdue
    FROM buckets
    LEFT JOIN classified USING (bucket)
    GROUP BY buckets.position, buckets.bucket, buckets.days_past_due_min, buckets.days_past_due_max
)
SELECT
    bucket,
    days_past_due_min,
    days_past_due_max,
    loans,
    outstanding,
    overdue,
    round(100 * outstanding / nullif(sum(outstanding) OVER (), 0), 2) AS share_pct,
    round(
        100 * sum(outstanding) OVER worst_first / nullif(sum(outstanding) OVER (), 0), 2
    ) AS at_risk_pct
FROM totals
WINDOW worst_first AS (ORDER BY position DESC ROWS UNBOUNDED PRECEDING)
ORDER BY position
