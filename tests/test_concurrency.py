"""Tests for multi-threaded concurrency and transaction safety.

Ensures that concurrent calls to consume() and next_account() against the same SQLite
database maintain strict consistency, prevent race conditions, and correctly track
credit accounting without corruption.
"""

import concurrent.futures
import sqlite3
import pytest
from typing import List

from flow_account.manager import FlowAccountManager, NoAccountsAvailableError
from flow_account.models import AccountStatus


def test_concurrent_consume_shared_manager(manager):
    """Multiple threads consuming credits using a shared manager instance."""
    # Setup 3 accounts with high daily limits to prevent early exhaustion
    manager.add_account("shared1@example.com", daily_limit=1000, priority=1)
    manager.add_account("shared2@example.com", daily_limit=1000, priority=2)
    manager.add_account("shared3@example.com", daily_limit=1000, priority=3)

    num_threads = 8
    consumes_per_thread = 15
    credit_amount = 2

    def worker_task(thread_idx: int) -> int:
        total_consumed = 0
        for i in range(consumes_per_thread):
            acc, rec = manager.consume(credit_amount, note=f"T{thread_idx}-job{i}")
            total_consumed += rec.amount
        return total_consumed

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker_task, t) for t in range(num_threads)]
        thread_totals = [f.result() for f in concurrent.futures.as_completed(futures)]

    expected_total = num_threads * consumes_per_thread * credit_amount
    assert sum(thread_totals) == expected_total

    # Verify database consistency
    accounts = manager.list_accounts()
    total_used_today = sum(a.used_today for a in accounts)
    total_used_month = sum(a.used_month for a in accounts)

    records = manager.history(limit=1000)
    history_total = sum(r.amount for r in records)

    assert total_used_today == expected_total
    assert total_used_month == expected_total
    assert history_total == expected_total
    assert len(records) == num_threads * consumes_per_thread

    # Check per-account consistency: account.used_today == sum of its history records
    for acc in accounts:
        acc_history_sum = sum(r.amount for r in records if r.account_id == acc.id)
        assert acc.used_today == acc_history_sum


def test_concurrent_consume_separate_manager_instances(temp_db_path):
    """Multiple threads each with their own manager instance accessing the same DB file."""
    init_mgr = FlowAccountManager(db_path=temp_db_path)
    init_mgr.add_account("disk1@example.com", daily_limit=1000, priority=1)
    init_mgr.add_account("disk2@example.com", daily_limit=1000, priority=2)
    init_mgr.db.close()

    num_threads = 6
    consumes_per_thread = 10
    credit_amount = 3

    def worker_task(thread_idx: int) -> int:
        thread_mgr = FlowAccountManager(db_path=temp_db_path)
        consumed = 0
        try:
            for i in range(consumes_per_thread):
                _, rec = thread_mgr.consume(credit_amount, note=f"Worker-{thread_idx}-{i}")
                consumed += rec.amount
        finally:
            thread_mgr.db.close()
        return consumed

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker_task, t) for t in range(num_threads)]
        thread_totals = [f.result() for f in concurrent.futures.as_completed(futures)]

    expected_total = num_threads * consumes_per_thread * credit_amount
    assert sum(thread_totals) == expected_total

    verify_mgr = FlowAccountManager(db_path=temp_db_path)
    accounts = verify_mgr.list_accounts()
    records = verify_mgr.history(limit=1000)

    total_used_today = sum(a.used_today for a in accounts)
    history_total = sum(r.amount for r in records)

    assert total_used_today == expected_total
    assert history_total == expected_total

    # Verify SQLite DB file integrity
    with verify_mgr.db.connect() as conn:
        integrity_row = conn.execute("PRAGMA integrity_check;").fetchone()
        assert integrity_row[0] == "ok"
    verify_mgr.db.close()


def test_concurrent_consume_and_auto_rotation_on_exhaustion(manager):
    """Threads consume credits on accounts that reach exhaustion and auto-rotate."""
    # 2 accounts with capacity for 20 credits each
    manager.add_account("exhaust1@example.com", daily_limit=20, priority=1)
    manager.add_account("exhaust2@example.com", daily_limit=20, priority=2)

    num_threads = 4
    attempts_per_thread = 8
    credit_amount = 5  # Total needed to exhaust both: 40 credits (8 calls of 5)

    successful_consumes: List[int] = []

    def worker_task(t_id: int):
        for _ in range(attempts_per_thread):
            try:
                acc, rec = manager.consume(credit_amount, note=f"T{t_id}")
                successful_consumes.append(rec.amount)
            except NoAccountsAvailableError:
                # Expected once all accounts reach exhaustion
                pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker_task, t) for t in range(num_threads)]
        concurrent.futures.wait(futures)

    accounts = manager.list_accounts()
    records = manager.history(limit=1000)

    total_used = sum(a.used_today for a in accounts)
    history_total = sum(r.amount for r in records)
    total_successful = sum(successful_consumes)

    # Invariant: Accounts total used MUST match history total and reported consumed amounts
    assert total_used == history_total
    assert total_used == total_successful

    # Since total capacity was 40, exactly 40 credits should have been consumed
    assert total_used == 40
    # Both accounts must now be EXHAUSTED
    for acc in accounts:
        assert acc.status == AccountStatus.EXHAUSTED
        assert not acc.is_eligible


def test_concurrent_next_account_and_consume(manager):
    """Concurrent mix of next_account() rotations and credit consumptions."""
    manager.add_account("mix1@example.com", daily_limit=500, priority=1)
    manager.add_account("mix2@example.com", daily_limit=500, priority=2)
    manager.add_account("mix3@example.com", daily_limit=500, priority=3)

    num_rotators = 3
    num_consumers = 5
    iterations = 15

    def rotator_task():
        for _ in range(iterations):
            manager.next_account()

    def consumer_task():
        consumed = 0
        for _ in range(iterations):
            acc, rec = manager.consume(2, note="Mixed concurrency")
            consumed += rec.amount
        return consumed

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_rotators + num_consumers) as executor:
        rot_futures = [executor.submit(rotator_task) for _ in range(num_rotators)]
        con_futures = [executor.submit(consumer_task) for _ in range(num_consumers)]

        concurrent.futures.wait(rot_futures + con_futures)
        consumed_amounts = [f.result() for f in con_futures]

    expected_consumed = num_consumers * iterations * 2
    assert sum(consumed_amounts) == expected_consumed

    accounts = manager.list_accounts()
    records = manager.history(limit=1000)

    assert sum(a.used_today for a in accounts) == expected_consumed
    assert sum(r.amount for r in records) == expected_consumed

    # Ensure DB file is healthy
    with manager.db.connect() as conn:
        integrity = conn.execute("PRAGMA integrity_check;").fetchone()
        assert integrity[0] == "ok"
