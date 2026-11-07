"""One worked, verified example challenge per category.

Small local models (e.g. 7B) follow a concrete example far better than abstract rules, so the
generator shows the matching example to Ollama models. Each example's reference solution is
checked against the database by the test suite (tests/test_ai.py), so it stays correct.
"""
from __future__ import annotations

import json

EXAMPLES: dict[str, dict] = {
    "Advanced SQL": {
        "title": "How concentrated is revenue? Customers-to-50% by territory",
        "category": "Advanced SQL",
        "difficulty": "Staff",
        "estimated_minutes": 30,
        "topics": ["Window functions", "Handling data skew"],
        "scenario": "The CFO suspects that a few reseller 'whales' carry several territories. For each territory, "
                    "she wants to know how many customers it takes to reach half of 2024 revenue. Cancelled orders "
                    "(status 6) must not count. A naive top-N cut-off is misleading because some territories are "
                    "dominated by a few resellers and others by thousands of online buyers.",
        "tasks": [
            "Use 2024 orders only, excluding cancelled orders.",
            "Rank customers within each territory by revenue, breaking ties by the lower customer_id.",
            "Return the smallest number of customers whose cumulative revenue reaches at least 50% of the territory's revenue.",
            "Also return active_customers and the top customer's share of territory revenue in percent, rounded to 2 decimals.",
            "Order by customers_for_half_revenue, then territory.",
        ],
        "deliverable": "One SQL query.",
        "tables_used": ["sales.sales_order_header", "sales.sales_territory"],
        "expected_columns": ["territory", "customers_for_half_revenue", "active_customers", "top_customer_share_pct"],
        "order_matters": True,
        "hints": [
            "Aggregate to one row per (territory, customer) before using any window function.",
            "A running SUM ordered by revenue DESC, customer_id shows where each customer starts in the cumulative share.",
            "Count customers whose running total before them is still below half: running - revenue < 0.5 * total.",
        ],
        "reference_solution": (
            "WITH cust AS (\n"
            "    SELECT h.territory_id, h.customer_id, sum(h.total_due) AS revenue\n"
            "    FROM sales.sales_order_header h\n"
            "    WHERE h.status <> 6\n"
            "      AND h.order_date >= TIMESTAMP '2024-01-01' AND h.order_date < TIMESTAMP '2025-01-01'\n"
            "    GROUP BY 1, 2\n"
            "), ranked AS (\n"
            "    SELECT c.*,\n"
            "           sum(revenue) OVER (PARTITION BY territory_id) AS territory_revenue,\n"
            "           sum(revenue) OVER (PARTITION BY territory_id ORDER BY revenue DESC, customer_id\n"
            "                              ROWS UNBOUNDED PRECEDING) AS running_revenue\n"
            "    FROM cust c\n"
            ")\n"
            "SELECT t.name AS territory,\n"
            "       count(*) FILTER (WHERE running_revenue - revenue < 0.5 * territory_revenue) AS customers_for_half_revenue,\n"
            "       count(*) AS active_customers,\n"
            "       round(100.0 * max(revenue) / max(territory_revenue), 2) AS top_customer_share_pct\n"
            "FROM ranked r JOIN sales.sales_territory t ON t.territory_id = r.territory_id\n"
            "GROUP BY t.name\n"
            "ORDER BY customers_for_half_revenue, territory"
        ),
        "solution_explanation": "Aggregating first keeps the window small. The running sum ordered by revenue, then "
                                "customer_id, is deterministic; a customer is needed while the revenue accumulated "
                                "before them is still under half.",
        "additional_artifacts": "",
        "follow_ups": ["How would you compute this daily over a rolling 365-day window?",
                       "Which index or sort order helps on Postgres, and why?"],
    },
    "Python / Pandas": {
        "title": "Spot customers drifting away using order gaps",
        "category": "Python / Pandas",
        "difficulty": "Staff",
        "estimated_minutes": 35,
        "topics": ["Vectorization (no loops/apply)", "Groupby transforms at scale"],
        "scenario": "Retention wants an early-warning list. A customer is 'at risk' when the gap before their latest "
                    "order is more than three times their usual (median) gap between orders. With a million orders, "
                    "a Python loop or groupby.apply per customer is too slow: the solution must be fully vectorized.",
        "tasks": [
            "Use non-cancelled orders (status <> 6) only.",
            "For each customer, compute the gap in days between consecutive orders.",
            "Keep customers with at least 3 orders; report n_orders, median_gap, last_gap.",
            "Flag at_risk when last_gap > 3 * median_gap.",
            "Sort by customer_id. No Python loops and no .apply().",
        ],
        "deliverable": "A Python script that assigns a DataFrame to `result`.",
        "tables_used": ["sales.sales_order_header"],
        "expected_columns": ["customer_id", "n_orders", "median_gap", "last_gap", "at_risk"],
        "order_matters": True,
        "hints": [
            "Sort by customer and date once; then everything can be done with groupby methods.",
            "groupby(...).diff() gives the gap to the previous order within each customer.",
            "Use one groupby().agg() with named aggregations for size, median and last.",
        ],
        "reference_solution": (
            "orders = sql(\"SELECT customer_id, order_date FROM sales.sales_order_header WHERE status <> 6\")\n"
            "orders = orders.sort_values([\"customer_id\", \"order_date\"])\n"
            "orders[\"gap_days\"] = orders.groupby(\"customer_id\")[\"order_date\"].diff().dt.days\n"
            "stats = orders.groupby(\"customer_id\").agg(\n"
            "    n_orders=(\"order_date\", \"size\"),\n"
            "    median_gap=(\"gap_days\", \"median\"),\n"
            "    last_gap=(\"gap_days\", \"last\"),\n"
            ")\n"
            "stats = stats[stats[\"n_orders\"] >= 3]\n"
            "stats[\"at_risk\"] = stats[\"last_gap\"] > 3 * stats[\"median_gap\"]\n"
            "result = stats.reset_index().sort_values(\"customer_id\", ignore_index=True)"
        ),
        "solution_explanation": "One sort plus groupby.diff computes every gap in vectorized C code; named aggregations "
                                "summarise each customer in a single pass, and the flag is a column comparison.",
        "additional_artifacts": "",
        "follow_ups": ["How would you reduce memory if the table had 500M rows?",
                       "Why can groupby('last') differ from taking the final row when there are NaNs?"],
    },
    "Analytics Engineering / dbt": {
        "title": "Build the order-line sales fact at the right grain",
        "category": "Analytics Engineering / dbt",
        "difficulty": "Staff",
        "estimated_minutes": 40,
        "topics": ["Fact table grain", "Data tests & contracts"],
        "scenario": "Analysts join headers and lines ad hoc and double-count freight and tax. Build fct_order_lines "
                    "at one row per order line, carrying the header attributes analysts need, excluding cancelled "
                    "orders, and with tests that would catch a grain violation.",
        "tasks": [
            "One row per sales_order_detail line; exclude orders with status 6.",
            "Include order_date as a DATE, customer_id, territory_id, is_online, product_id, quantity and net line amount.",
            "Do not include header-level money columns (tax, freight, total_due), which would double count.",
            "Add schema tests: unique and not_null on the line key, and a relationship to the customer source.",
        ],
        "deliverable": "One dbt model plus a schema.yml in additional_artifacts.",
        "tables_used": ["sales.sales_order_detail", "sales.sales_order_header"],
        "expected_columns": ["sales_order_id", "sales_order_detail_id", "order_date", "customer_id", "territory_id",
                             "is_online", "product_id", "quantity", "line_amount"],
        "order_matters": False,
        "hints": [
            "Start from the detail table so the grain is the line.",
            "Header columns that are per-order (tax, freight) don't belong on a line-grain fact.",
            "Test uniqueness on sales_order_detail_id to guard the grain.",
        ],
        "reference_solution": (
            "{{ config(materialized='table') }}\n"
            "SELECT d.sales_order_id,\n"
            "       d.sales_order_detail_id,\n"
            "       CAST(h.order_date AS DATE) AS order_date,\n"
            "       h.customer_id,\n"
            "       h.territory_id,\n"
            "       h.online_order_flag AS is_online,\n"
            "       d.product_id,\n"
            "       d.order_qty AS quantity,\n"
            "       d.line_total AS line_amount\n"
            "FROM {{ source('sales', 'sales_order_detail') }} d\n"
            "JOIN {{ source('sales', 'sales_order_header') }} h ON h.sales_order_id = d.sales_order_id\n"
            "WHERE h.status <> 6"
        ),
        "solution_explanation": "Driving from the detail table fixes the grain; only line-level measures are kept so "
                                "sums never double count header amounts.",
        "additional_artifacts": (
            "schema.yml\n\n```yaml\nmodels:\n  - name: fct_order_lines\n    columns:\n"
            "      - name: sales_order_detail_id\n        tests: [unique, not_null]\n"
            "      - name: customer_id\n        tests:\n          - relationships:\n"
            "              to: source('sales', 'customer')\n              field: customer_id\n```"
        ),
        "follow_ups": ["How would you make this model incremental, and what is the risk with late-arriving orders?",
                       "Where would you allocate freight to lines, if analysts needed it?"],
    },
}


def example_json(category: str) -> str | None:
    ex = EXAMPLES.get(category)
    return json.dumps(ex, indent=1) if ex else None
