"""Reset and rollover management for Flow Credit Rotator.

Handles daily and monthly credit rollovers according to configured timezone (America/Bahia).
Maintains immutable audit history while resetting counters and reactivating exhausted accounts.
"""

from datetime import datetime
import sqlite3
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from flow_account.config import get_timezone
from flow_account.models import AccountStatus


def get_current_date_and_month(tz_name: str) -> Tuple[str, str, datetime]:
    """Get current date (YYYY-MM-DD), month (YYYY-MM), and aware datetime in timezone."""
    tz = get_timezone(tz_name)
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m"), now


def perform_daily_reset(conn: sqlite3.Connection, current_date: str) -> int:
    """Reset daily counters and reactivate accounts exhausted by daily limits."""
    # Reset used_today to 0 for all accounts
    cursor = conn.execute("UPDATE accounts SET used_today = 0;")

    # Reactivate EXHAUSTED accounts, provided they have not reached monthly limit
    conn.execute(
        """
        UPDATE accounts
        SET status = ?
        WHERE status = ?
          AND (monthly_limit IS NULL OR monthly_limit <= 0 OR used_month < monthly_limit);
        """,
        (AccountStatus.ACTIVE.value, AccountStatus.EXHAUSTED.value),
    )

    # Update system_state
    conn.execute("UPDATE system_state SET last_reset_date = ? WHERE id = 1;", (current_date,))
    return cursor.rowcount


def perform_monthly_reset(conn: sqlite3.Connection, current_month: str) -> int:
    """Reset monthly counters and re-evaluate exhausted accounts."""
    # Reset used_month to 0 for all accounts
    cursor = conn.execute("UPDATE accounts SET used_month = 0;")

    # Reactivate EXHAUSTED accounts that were blocked by monthly limit if under daily limit
    conn.execute(
        """
        UPDATE accounts
        SET status = ?
        WHERE status = ?
          AND used_today < daily_limit;
        """,
        (AccountStatus.ACTIVE.value, AccountStatus.EXHAUSTED.value),
    )

    # Update system_state
    conn.execute("UPDATE system_state SET last_reset_month = ? WHERE id = 1;", (current_month,))
    return cursor.rowcount


def check_and_apply_reset(conn: sqlite3.Connection) -> dict:
    """Check if daily or monthly reset is due and apply within transaction.

    Returns dict detailing actions taken.
    """
    row = conn.execute(
        "SELECT timezone, last_reset_date, last_reset_month FROM system_state WHERE id = 1;"
    ).fetchone()

    if not row:
        return {"daily_reset": False, "monthly_reset": False}

    tz_name = row["timezone"]
    last_reset_date = row["last_reset_date"]
    last_reset_month = row["last_reset_month"]

    current_date, current_month, _ = get_current_date_and_month(tz_name)

    actions = {"daily_reset": False, "monthly_reset": False}

    # Month rollover check
    if last_reset_month is None:
        conn.execute("UPDATE system_state SET last_reset_month = ? WHERE id = 1;", (current_month,))
    elif current_month > last_reset_month:
        perform_monthly_reset(conn, current_month)
        actions["monthly_reset"] = True

    # Day rollover check
    if last_reset_date is None:
        conn.execute("UPDATE system_state SET last_reset_date = ? WHERE id = 1;", (current_date,))
    elif current_date > last_reset_date:
        perform_daily_reset(conn, current_date)
        actions["daily_reset"] = True

    return actions


def reset_daily(conn: sqlite3.Connection, force: bool = True) -> dict:
    """Explicitly reset daily usage counters."""
    row = conn.execute("SELECT timezone FROM system_state WHERE id = 1;").fetchone()
    tz_name = row["timezone"] if row else "America/Bahia"
    current_date, _, _ = get_current_date_and_month(tz_name)
    count = perform_daily_reset(conn, current_date)
    return {"status": "ok", "reset_type": "daily", "accounts_affected": count, "date": current_date}


def reset_monthly(conn: sqlite3.Connection, force: bool = True) -> dict:
    """Explicitly reset monthly usage counters."""
    row = conn.execute("SELECT timezone FROM system_state WHERE id = 1;").fetchone()
    tz_name = row["timezone"] if row else "America/Bahia"
    _, current_month, _ = get_current_date_and_month(tz_name)
    count = perform_monthly_reset(conn, current_month)
    return {"status": "ok", "reset_type": "monthly", "accounts_affected": count, "month": current_month}
