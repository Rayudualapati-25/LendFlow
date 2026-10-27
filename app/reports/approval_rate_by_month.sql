-- Approval rate per calendar month in India (Asia/Kolkata), oldest month first.
--
-- monthly  : decisions per month, split by outcome (an aggregate with FILTER)
-- calendar : every month from the first decision to the last (generate_series), so a
--            month without decisions still appears instead of silently stretching the
--            rolling window over a gap
-- filled   : the calendar joined to the counts, with zero where nothing was decided
--
-- Window functions then compare each month with its neighbours:
--   approval_rate_3m         : approved / decided over this month and the two before it
--   change_vs_previous_month : this month's rate minus the previous month's (LAG)
WITH monthly AS (
    SELECT
        date_trunc('month', decided_at AT TIME ZONE 'Asia/Kolkata')::date AS month,
        count(*) AS decided,
        count(*) FILTER (WHERE outcome = 'approved') AS approved
    FROM decisions
    GROUP BY 1
),
calendar AS (
    SELECT month::date AS month
    FROM
        (SELECT min(month) AS first_month, max(month) AS last_month FROM monthly) AS bounds,
        generate_series(bounds.first_month, bounds.last_month, interval '1 month') AS month
),
filled AS (
    SELECT
        calendar.month,
        coalesce(monthly.decided, 0) AS decided,
        coalesce(monthly.approved, 0) AS approved,
        round(monthly.approved::numeric / monthly.decided, 4) AS approval_rate
    FROM calendar
    LEFT JOIN monthly USING (month)
)
SELECT
    month,
    decided,
    approved,
    decided - approved AS rejected,
    approval_rate,
    round(
        (sum(approved) OVER last_3_months)::numeric
        / nullif(sum(decided) OVER last_3_months, 0),
        4
    ) AS approval_rate_3m,
    approval_rate - lag(approval_rate) OVER (ORDER BY month) AS change_vs_previous_month
FROM filled
WINDOW last_3_months AS (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
ORDER BY month
