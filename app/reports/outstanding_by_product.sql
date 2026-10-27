-- Principal still owed on active loans (disbursed and not yet closed), by product.
--
-- repaid      : principal already repaid on each loan, from paid instalments only
-- outstanding : each active loan's principal minus what has been repaid
-- by_product  : loan counts and amounts per product
--
-- Window functions place each product against the whole book:
--   share_pct : this product's outstanding as a percentage of the total (SUM() OVER ())
--   rank      : largest outstanding first (RANK)
WITH repaid AS (
    SELECT loan_id, sum(principal_component) AS principal_repaid
    FROM repayments
    WHERE status = 'paid'
    GROUP BY loan_id
),
outstanding AS (
    SELECT
        applications.product,
        loans.principal - coalesce(repaid.principal_repaid, 0) AS amount
    FROM loans
    JOIN applications ON applications.id = loans.application_id
    LEFT JOIN repaid ON repaid.loan_id = loans.id
    WHERE applications.status = 'disbursed'
),
by_product AS (
    SELECT product, count(*) AS active_loans, sum(amount) AS outstanding
    FROM outstanding
    GROUP BY product
)
SELECT
    product,
    active_loans,
    outstanding,
    round(100 * outstanding / nullif(sum(outstanding) OVER (), 0), 2) AS share_pct,
    rank() OVER (ORDER BY outstanding DESC) AS rank
FROM by_product
ORDER BY rank, product
