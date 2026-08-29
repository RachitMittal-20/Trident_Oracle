"""Read models for /v1/analytics/* -- every number here is a SQL aggregate
(count/avg/percentile_cont/sum), never a Python-side reduction over fetched
rows, per CLAUDE.md's "Aggregate in SQL, not in Python."

Two things worth knowing up front about what's actually measurable from
this schema, since a couple of the numbers below are honest proxies rather
than purpose-built columns:

- Per-stage processing latency has one exact source (match_runs.duration_ms,
  measured by the matching engine itself) and two proxies: extraction uses
  jobs.locked_at -> jobs.updated_at (the window between a worker claiming
  the row and marking it done -- there's no dedicated started_at/
  completed_at pair on jobs), and notification uses the purpose-built
  notification_deliveries.created_at -> sent_at, which *is* an exact
  per-delivery measurement.
- "Mean time to decision" is time from invoice intake (invoices.created_at)
  to the audit_log 'approval_decided' entry match_view.py's _settle_invoice
  writes -- audit_log is the only place a decision timestamp is recorded at
  all (approval_requests.decided_at exists too, but multiple rows per
  invoice under dual approval means audit_log's one settlement-time entry
  is the cleaner single source of truth).
"""

from typing import Any

import psycopg
from psycopg.rows import dict_row

_CONFIDENCE_BUCKETS = 10


def _decimal_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _period_days(days: int) -> int:
    # A day count outside a sane demo range is almost certainly a mistake
    # in the caller, not a real request for a 10-year window -- fail loudly
    # rather than run an unbounded aggregate.
    if days < 1 or days > 3650:
        raise ValueError(f"days must be between 1 and 3650, got {days}")
    return days


def get_summary(conn: psycopg.Connection[Any], *, days: int = 30) -> dict[str, Any]:
    days = _period_days(days)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            with bounds as (
                select now() - make_interval(days => %(days)s) as period_start,
                       now() - make_interval(days => %(days)s * 2) as prev_period_start
            )
            select
                count(*) filter (where i.created_at >= b.period_start) as invoices_current,
                count(*) filter (
                    where i.created_at >= b.prev_period_start and i.created_at < b.period_start
                ) as invoices_previous,
                count(*) filter (
                    where i.created_at >= b.period_start and i.status = 'AUTO_POSTED'
                ) as auto_posted_current,
                count(*) filter (
                    where i.created_at >= b.period_start
                        and i.status in ('AUTO_POSTED', 'POSTED', 'REJECTED')
                ) as settled_current,
                avg(i.overall_confidence) filter (
                    where i.created_at >= b.period_start and i.overall_confidence is not null
                ) as mean_confidence,
                round(
                    100.0 * count(*) filter (
                        where i.created_at >= b.period_start and i.status = 'AUTO_POSTED'
                    )
                    / nullif(
                        count(*) filter (
                            where i.created_at >= b.period_start
                                and i.status in ('AUTO_POSTED', 'POSTED', 'REJECTED')
                        ),
                        0
                    ),
                    2
                ) as auto_post_rate_pct
            from invoices i, bounds b
            """,
            {"days": days},
        )
        core = cur.fetchone()
        assert core is not None

        cur.execute(
            """
            select e.severity, count(*) as n
            from match_exceptions e
            where e.created_at >= now() - make_interval(days => %(days)s)
            group by e.severity
            """,
            {"days": days},
        )
        severity_rows = cur.fetchall()

        cur.execute(
            """
            select coalesce(sum(t.total), 0) as value_at_risk
            from (
                select i.id, i.total
                from invoices i
                where i.total is not null
                    and exists (
                        select 1 from match_exceptions e
                        where e.invoice_id = i.id and e.status = 'open' and e.severity = 'block'
                    )
            ) t
            """
        )
        value_at_risk_row = cur.fetchone()
        assert value_at_risk_row is not None

        cur.execute(
            """
            select avg(extract(epoch from (a.created_at - i.created_at))) as mean_seconds
            from audit_log a
            join invoices i on i.id = a.entity_id
            where a.action = 'approval_decided' and a.entity_type = 'invoice'
                and a.created_at >= now() - make_interval(days => %(days)s)
            """,
            {"days": days},
        )
        decision_row = cur.fetchone()
        assert decision_row is not None

    invoices_current = core["invoices_current"]
    invoices_previous = core["invoices_previous"]

    return {
        "period_days": days,
        "invoices_processed": invoices_current,
        "invoices_processed_delta": invoices_current - invoices_previous,
        "auto_post_rate_pct": _decimal_str(core["auto_post_rate_pct"]),
        "mean_extraction_confidence": _decimal_str(core["mean_confidence"]),
        "exceptions_by_severity": {row["severity"]: row["n"] for row in severity_rows},
        "value_at_risk": str(value_at_risk_row["value_at_risk"]),
        "mean_seconds_to_decision": (
            float(decision_row["mean_seconds"])
            if decision_row["mean_seconds"] is not None
            else None
        ),
    }


def get_volume_over_time(conn: psycopg.Connection[Any], *, days: int = 30) -> list[dict[str, Any]]:
    days = _period_days(days)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select
                date_trunc('day', created_at)::date as day,
                case
                    when status = 'AUTO_POSTED' then 'auto_posted'
                    when status in ('APPROVED', 'POSTED') then 'approved'
                    when status = 'REJECTED' then 'rejected'
                    when status = 'EXTRACTION_FAILED' then 'failed'
                    else 'pending'
                end as outcome,
                count(*) as n
            from invoices
            where created_at >= now() - make_interval(days => %(days)s)
            group by 1, 2
            order by 1
            """,
            {"days": days},
        )
        rows = cur.fetchall()
    return [
        {"day": row["day"].isoformat(), "outcome": row["outcome"], "count": row["n"]}
        for row in rows
    ]


def get_exceptions_by_type(
    conn: psycopg.Connection[Any], *, days: int = 30
) -> list[dict[str, Any]]:
    days = _period_days(days)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select exception_type, count(*) as n
            from match_exceptions
            where created_at >= now() - make_interval(days => %(days)s)
            group by exception_type
            order by n desc
            """,
            {"days": days},
        )
        rows = cur.fetchall()
    return [{"exception_type": row["exception_type"], "count": row["n"]} for row in rows]


def get_confidence_distribution(
    conn: psycopg.Connection[Any], *, days: int = 30
) -> list[dict[str, Any]]:
    days = _period_days(days)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select width_bucket(overall_confidence, 0, 1, %(buckets)s) as bucket, count(*) as n
            from invoices
            where overall_confidence is not null
                and created_at >= now() - make_interval(days => %(days)s)
            group by 1
            """,
            {"days": days, "buckets": _CONFIDENCE_BUCKETS},
        )
        rows = cur.fetchall()

    counts = dict.fromkeys(range(1, _CONFIDENCE_BUCKETS + 1), 0)
    for row in rows:
        # width_bucket returns bucket 0 for a value below the range (can't
        # happen -- confidence is check-constrained to [0, 1]) and
        # nbuckets + 1 for a value at/above the top edge (confidence = 1.0
        # exactly can land here); clamp both into the last real bucket
        # rather than dropping those invoices from the histogram.
        bucket = min(max(row["bucket"], 1), _CONFIDENCE_BUCKETS)
        counts[bucket] += row["n"]

    return [
        {
            "bucket_start": round((bucket - 1) / _CONFIDENCE_BUCKETS, 2),
            "bucket_end": round(bucket / _CONFIDENCE_BUCKETS, 2),
            "count": counts[bucket],
        }
        for bucket in range(1, _CONFIDENCE_BUCKETS + 1)
    ]


def _percentiles(
    conn: psycopg.Connection[Any], sql: str, params: dict[str, Any]
) -> dict[str, float | None]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    assert row is not None
    return {
        "p50": float(row["p50"]) if row["p50"] is not None else None,
        "p95": float(row["p95"]) if row["p95"] is not None else None,
        "p99": float(row["p99"]) if row["p99"] is not None else None,
    }


def get_latency(
    conn: psycopg.Connection[Any], *, days: int = 30
) -> dict[str, dict[str, float | None]]:
    days = _period_days(days)
    params = {"days": days}

    extraction = _percentiles(
        conn,
        """
        select
            percentile_cont(0.5) within group (
                order by extract(epoch from (updated_at - locked_at)) * 1000
            ) as p50,
            percentile_cont(0.95) within group (
                order by extract(epoch from (updated_at - locked_at)) * 1000
            ) as p95,
            percentile_cont(0.99) within group (
                order by extract(epoch from (updated_at - locked_at)) * 1000
            ) as p99
        from jobs
        where job_type = 'extract' and status = 'done' and locked_at is not null
            and updated_at >= now() - make_interval(days => %(days)s)
        """,
        params,
    )
    matching = _percentiles(
        conn,
        """
        select
            percentile_cont(0.5) within group (order by duration_ms) as p50,
            percentile_cont(0.95) within group (order by duration_ms) as p95,
            percentile_cont(0.99) within group (order by duration_ms) as p99
        from match_runs
        where executed_at >= now() - make_interval(days => %(days)s)
        """,
        params,
    )
    notification = _percentiles(
        conn,
        """
        select
            percentile_cont(0.5) within group (
                order by extract(epoch from (sent_at - created_at)) * 1000
            ) as p50,
            percentile_cont(0.95) within group (
                order by extract(epoch from (sent_at - created_at)) * 1000
            ) as p95,
            percentile_cont(0.99) within group (
                order by extract(epoch from (sent_at - created_at)) * 1000
            ) as p99
        from notification_deliveries
        where status = 'sent' and sent_at is not null
            and created_at >= now() - make_interval(days => %(days)s)
        """,
        params,
    )
    return {"extraction": extraction, "matching": matching, "notification": notification}


def get_auto_post_trend(conn: psycopg.Connection[Any], *, days: int = 30) -> list[dict[str, Any]]:
    days = _period_days(days)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select
                date_trunc('day', created_at)::date as day,
                count(*) filter (where status = 'AUTO_POSTED') as auto_posted,
                count(*) filter (where status in ('AUTO_POSTED', 'POSTED', 'REJECTED')) as settled,
                round(
                    100.0 * count(*) filter (where status = 'AUTO_POSTED')
                        / nullif(
                            count(*) filter (where status in ('AUTO_POSTED', 'POSTED', 'REJECTED')),
                            0
                        ),
                    2
                ) as rate_pct
            from invoices
            where created_at >= now() - make_interval(days => %(days)s)
            group by 1
            order by 1
            """,
            {"days": days},
        )
        rows = cur.fetchall()
    return [
        {
            "day": row["day"].isoformat(),
            "auto_posted": row["auto_posted"],
            "settled": row["settled"],
            "rate_pct": _decimal_str(row["rate_pct"]),
        }
        for row in rows
    ]


def get_vendors(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            with vendor_invoices as (
                select v.id as vendor_id, v.name as vendor_name, i.id as invoice_id
                from vendors v
                join invoices i on i.vendor_id = v.id
            ),
            vendor_exception_flags as (
                select vi.vendor_id, vi.invoice_id,
                    exists(
                        select 1 from match_exceptions e where e.invoice_id = vi.invoice_id
                    ) as has_exception
                from vendor_invoices vi
            ),
            vendor_price_variance as (
                select i.vendor_id, avg(e.delta_pct) as mean_price_variance_pct
                from match_exceptions e
                join invoices i on i.id = e.invoice_id
                where e.exception_type = 'PRICE_VARIANCE' and i.vendor_id is not null
                group by i.vendor_id
            )
            select
                vi.vendor_id,
                vi.vendor_name,
                count(*) as invoice_count,
                count(*) filter (where vf.has_exception) as invoices_with_exceptions,
                round(
                    100.0 * count(*) filter (where vf.has_exception) / count(*), 2
                ) as exception_rate,
                vpv.mean_price_variance_pct
            from vendor_invoices vi
            join vendor_exception_flags vf on vf.invoice_id = vi.invoice_id
            left join vendor_price_variance vpv on vpv.vendor_id = vi.vendor_id
            group by vi.vendor_id, vi.vendor_name, vpv.mean_price_variance_pct
            order by exception_rate desc, invoice_count desc
            limit 200
            """
        )
        rows = cur.fetchall()
    return [
        {
            "vendor_id": str(row["vendor_id"]),
            "vendor_name": row["vendor_name"],
            "invoice_count": row["invoice_count"],
            "exception_rate_pct": str(row["exception_rate"]),
            "mean_price_variance_pct": _decimal_str(row["mean_price_variance_pct"]),
        }
        for row in rows
    ]


def get_delivery_health(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select
                count(*) as total,
                count(*) filter (where status = 'sent') as sent,
                round(
                    100.0 * count(*) filter (where status = 'sent') / nullif(count(*), 0), 2
                ) as success_rate_pct,
                coalesce(avg(attempts), 0) as mean_attempts,
                coalesce(max(attempts), 0) as max_attempts
            from notification_deliveries
            """
        )
        delivery_row = cur.fetchone()
        assert delivery_row is not None

        cur.execute("select count(*) as n from dead_letters")
        dead_letter_row = cur.fetchone()
        assert dead_letter_row is not None

    return {
        "total_deliveries": delivery_row["total"],
        "sent_deliveries": delivery_row["sent"],
        "success_rate_pct": _decimal_str(delivery_row["success_rate_pct"]),
        "mean_attempts": str(delivery_row["mean_attempts"]),
        "max_attempts": delivery_row["max_attempts"],
        "dead_letter_count": dead_letter_row["n"],
    }
