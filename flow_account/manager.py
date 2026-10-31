"""Manager core for Flow Credit Rotator.

Orchestrates account rotation, credit tracking, limits, persistence, and audit logging.
"""

from datetime import datetime, timedelta
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from flow_account.config import get_timezone
from flow_account.db import Database
from flow_account.importer import read_accounts_from_file, read_central_config_xlsx
from flow_account.models import (
    Account,
    AccountStatus,
    Mode,
    Reservation,
    ReservationStatus,
    Strategy,
    SystemState,
    UsageRecord,
    validate_credential_ref,
)
from flow_account.reset import (
    check_and_apply_reset,
    get_current_date_and_month,
    reset_daily as db_reset_daily,
    reset_monthly as db_reset_monthly,
)
from flow_account.strategies import get_strategy, has_account_capacity


class FlowAccountError(Exception):
    """Base exception for Flow Credit Rotator."""
    pass


class NoAccountsAvailableError(FlowAccountError):
    """Raised when no accounts are eligible for credit consumption or rotation."""
    pass


class AccountNotFoundError(FlowAccountError):
    """Raised when an account cannot be located by ID or email."""
    pass


class InvalidOperationError(FlowAccountError):
    """Raised when an invalid operation is attempted in the current state/mode."""
    pass


class FlowAccountManager:
    """Core manager handling account lifecycle, rotation, and credit usage."""

    def __init__(self, db_path: Optional[str] = None):
        try:
            self.db = Database(db_path)
        except sqlite3.DatabaseError as e:
            raise FlowAccountError(f"Database error: {e}") from e

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _now_iso(self, tz_name: str) -> str:
        """Return current timestamp in ISO format with timezone."""
        tz = get_timezone(tz_name)
        return datetime.now(tz).isoformat()

    def _row_to_account(self, row: sqlite3.Row) -> Account:
        """Convert a database row into an Account dataclass."""
        return Account(
            id=row["id"],
            email=row["email"],
            credential_ref=row["credential_ref"],
            daily_limit=row["daily_limit"],
            monthly_limit=row["monthly_limit"],
            used_today=row["used_today"],
            used_month=row["used_month"],
            priority=row["priority"],
            status=AccountStatus(row["status"]),
            last_used_at=row["last_used_at"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _get_system_state(self, conn: sqlite3.Connection) -> SystemState:
        """Fetch the current SystemState from DB."""
        row = conn.execute("SELECT * FROM system_state WHERE id = 1;").fetchone()
        if not row:
            raise FlowAccountError("Corrupted database state: system_state row missing.")
        return SystemState(
            mode=Mode(row["mode"]),
            strategy=Strategy(row["strategy"]),
            current_account_id=row["current_account_id"],
            default_daily_limit=row["default_daily_limit"],
            last_reset_date=row["last_reset_date"],
            last_reset_month=row["last_reset_month"],
            timezone=row["timezone"],
        )

    def _save_system_state(self, conn: sqlite3.Connection, state: SystemState) -> None:
        """Persist SystemState to DB."""
        conn.execute(
            """
            UPDATE system_state
            SET mode = ?, strategy = ?, current_account_id = ?,
                default_daily_limit = ?, last_reset_date = ?,
                last_reset_month = ?, timezone = ?
            WHERE id = 1;
            """,
            (
                state.mode.value if isinstance(state.mode, Mode) else state.mode,
                state.strategy.value if isinstance(state.strategy, Strategy) else state.strategy,
                state.current_account_id,
                state.default_daily_limit,
                state.last_reset_date,
                state.last_reset_month,
                state.timezone,
            ),
        )

    def _find_account(
        self, conn: sqlite3.Connection, account_id_or_email: Union[int, str]
    ) -> Optional[Account]:
        """Find an account by ID or email address."""
        if isinstance(account_id_or_email, int) or (
            isinstance(account_id_or_email, str) and account_id_or_email.isdigit()
        ):
            acc_id = int(account_id_or_email)
            row = conn.execute("SELECT * FROM accounts WHERE id = ?;", (acc_id,)).fetchone()
        else:
            email = str(account_id_or_email).strip().lower()
            row = conn.execute("SELECT * FROM accounts WHERE LOWER(email) = ?;", (email,)).fetchone()
        return self._row_to_account(row) if row else None

    def _get_all_accounts(self, conn: sqlite3.Connection) -> List[Account]:
        """Fetch all accounts ordered by priority and ID."""
        rows = conn.execute("SELECT * FROM accounts ORDER BY priority ASC, id ASC;").fetchall()
        return [self._row_to_account(r) for r in rows]

    def _get_eligible_accounts(self, conn: sqlite3.Connection) -> List[Account]:
        """Fetch all accounts that satisfy eligibility criteria."""
        all_accs = self._get_all_accounts(conn)
        return [acc for acc in all_accs if acc.is_eligible]

    def _row_to_reservation(self, row: sqlite3.Row) -> Reservation:
        """Convert a database row into a Reservation dataclass."""
        return Reservation(
            id=row["id"],
            account_id=row["account_id"],
            estimated_cost=row["estimated_cost"],
            actual_amount=row["actual_amount"],
            status=ReservationStatus(row["status"]),
            note=row["note"] or "",
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            committed_at=row["committed_at"],
            cancelled_at=row["cancelled_at"],
        )

    def _get_active_reservations_map(
        self, conn: sqlite3.Connection, now_iso: str
    ) -> Dict[int, int]:
        """Auto-expire elapsed reservations and return a mapping of account_id to active pending reserved credits."""
        conn.execute(
            "UPDATE reservations SET status = 'EXPIRED' WHERE status = 'PENDING' AND expires_at < ?;",
            (now_iso,),
        )
        cursor = conn.execute(
            """
            SELECT account_id, SUM(estimated_cost) as reserved_total
            FROM reservations
            WHERE status = 'PENDING'
            GROUP BY account_id;
            """
        )
        return {r["account_id"]: int(r["reserved_total"]) for r in cursor.fetchall()}

    def _select_next_eligible(
        self,
        conn: sqlite3.Connection,
        strategy_name: Union[Strategy, str],
        current_id: Optional[int],
        force_advance: bool = False,
        required_cost: int = 0,
        reserved_map: Optional[Dict[int, int]] = None,
    ) -> Optional[Account]:
        """Select next eligible account based on configured strategy and required capacity."""
        eligible = self._get_eligible_accounts(conn)
        if not eligible:
            return None
        strategy = get_strategy(strategy_name)
        curr_acc = self._find_account(conn, current_id) if current_id is not None else None
        return strategy.select(
            eligible,
            current_account_id=current_id,
            force_advance=force_advance,
            current_account=curr_acc,
            required_cost=required_cost,
            reserved_map=reserved_map,
        )

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return a complete status report of the account manager."""
        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)
            accounts = self._get_all_accounts(conn)

            # Resolve current account if in AUTO mode and needs rotation
            curr_acc: Optional[Account] = None
            if state.current_account_id:
                curr_acc = self._find_account(conn, state.current_account_id)

            if state.mode == Mode.AUTO:
                if curr_acc is None or not curr_acc.is_eligible:
                    next_acc = self._select_next_eligible(
                        conn, state.strategy, state.current_account_id, force_advance=False
                    )
                    if next_acc:
                        state.current_account_id = next_acc.id
                        self._save_system_state(conn, state)
                        curr_acc = next_acc
                    else:
                        state.current_account_id = None
                        self._save_system_state(conn, state)
                        curr_acc = None

            total = len(accounts)
            active = sum(1 for a in accounts if a.status == AccountStatus.ACTIVE)
            exhausted = sum(1 for a in accounts if a.status == AccountStatus.EXHAUSTED)
            disabled = sum(1 for a in accounts if a.status == AccountStatus.DISABLED)
            needs_human = sum(1 for a in accounts if a.status == AccountStatus.NEEDS_HUMAN)
            eligible = sum(1 for a in accounts if a.is_eligible)
            total_used_today = sum(a.used_today for a in accounts)
            total_available = sum(a.available_credits for a in accounts if a.is_eligible)

            return {
                "system": state.to_dict(),
                "current_account": curr_acc.to_dict() if curr_acc else None,
                "summary": {
                    "total_accounts": total,
                    "active_accounts": active,
                    "exhausted_accounts": exhausted,
                    "disabled_accounts": disabled,
                    "needs_human_accounts": needs_human,
                    "eligible_accounts": eligible,
                    "total_used_today": total_used_today,
                    "total_available_credits": total_available,
                },
            }

    def list_accounts(self) -> List[Account]:
        """List all accounts with current eligibility status."""
        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            return self._get_all_accounts(conn)

    def current_account(self) -> Optional[Account]:
        """Retrieve the currently active account.

        In AUTO mode: if current account is missing or ineligible, automatically
        rotates to the next eligible account.
        In MANUAL mode: returns current account without rotating.
        """
        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)

            curr_acc: Optional[Account] = None
            if state.current_account_id:
                curr_acc = self._find_account(conn, state.current_account_id)

            if state.mode == Mode.AUTO:
                if curr_acc is None or not curr_acc.is_eligible:
                    next_acc = self._select_next_eligible(
                        conn, state.strategy, state.current_account_id, force_advance=False
                    )
                    if next_acc:
                        state.current_account_id = next_acc.id
                        self._save_system_state(conn, state)
                        return next_acc
                    else:
                        state.current_account_id = None
                        self._save_system_state(conn, state)
                        return None
                return curr_acc
            else:
                return curr_acc

    def next_account(self) -> Optional[Account]:
        """Manually or algorithmically advance to the next eligible account."""
        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)
            next_acc = self._select_next_eligible(
                conn, state.strategy, state.current_account_id, force_advance=True
            )
            if not next_acc:
                raise NoAccountsAvailableError(
                    "NO_ACCOUNTS_AVAILABLE: No eligible accounts available to rotate to."
                )
            state.current_account_id = next_acc.id
            self._save_system_state(conn, state)
            return next_acc

    def get_account(self, account_id_or_email: Union[int, str]) -> Optional[Account]:
        """Look up an account without modifying system state or active selection."""
        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            return self._find_account(conn, account_id_or_email)

    def select_account(self, account_id_or_email: Union[int, str]) -> Account:
        """Select a specific account as current."""
        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)
            acc = self._find_account(conn, account_id_or_email)
            if not acc:
                raise AccountNotFoundError(f"Account '{account_id_or_email}' not found.")
            state.current_account_id = acc.id
            self._save_system_state(conn, state)
            return acc

    def consume(self, amount: int, note: str = "") -> Tuple[Account, UsageRecord]:
        """Consume credits from the currently active account.

        In AUTO mode: automatically rotates if current account is missing or exhausted.
        In MANUAL mode: fails if current account is not set or not eligible.
        Updates counters, logs audit record, and handles auto-exhaustion rotation.
        """
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("Credit amount to consume must be a positive integer.")

        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)

            # Determine target account
            acc: Optional[Account] = None
            if state.current_account_id:
                acc = self._find_account(conn, state.current_account_id)

            if state.mode == Mode.AUTO:
                if acc is None or not acc.is_eligible:
                    acc = self._select_next_eligible(
                        conn, state.strategy, state.current_account_id, force_advance=False
                    )
                    if acc is None:
                        state.current_account_id = None
                        self._save_system_state(conn, state)
                        raise NoAccountsAvailableError(
                            "NO_ACCOUNTS_AVAILABLE: No eligible accounts available to consume credits."
                        )
                    state.current_account_id = acc.id
                    self._save_system_state(conn, state)
            else:
                # MANUAL mode
                if acc is None:
                    raise InvalidOperationError("MANUAL mode: No account currently selected.")
                if not acc.is_eligible:
                    raise InvalidOperationError(
                        f"MANUAL mode: Selected account '{acc.email}' is not eligible "
                        f"(status={acc.status.value}, used_today={acc.used_today}/{acc.daily_limit})."
                    )

            # Consume credits
            now_iso = self._now_iso(state.timezone)
            used_today_after = acc.used_today + amount
            used_month_after = acc.used_month + amount

            new_status = acc.status
            # Check limits
            if used_today_after >= acc.daily_limit:
                new_status = AccountStatus.EXHAUSTED
            elif acc.monthly_limit is not None and acc.monthly_limit > 0 and used_month_after >= acc.monthly_limit:
                new_status = AccountStatus.EXHAUSTED

            # Update account in database
            conn.execute(
                """
                UPDATE accounts
                SET used_today = ?, used_month = ?, status = ?,
                    last_used_at = ?, updated_at = ?
                WHERE id = ?;
                """,
                (used_today_after, used_month_after, new_status.value, now_iso, now_iso, acc.id),
            )

            # Insert usage history record
            cursor = conn.execute(
                """
                INSERT INTO usage_history (
                    account_id, amount, used_today_after, used_month_after, mode, note, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    acc.id,
                    amount,
                    used_today_after,
                    used_month_after,
                    state.mode.value,
                    note,
                    now_iso,
                ),
            )
            history_id = cursor.lastrowid

            # Create updated account model
            updated_account = Account(
                id=acc.id,
                email=acc.email,
                credential_ref=acc.credential_ref,
                daily_limit=acc.daily_limit,
                monthly_limit=acc.monthly_limit,
                used_today=used_today_after,
                used_month=used_month_after,
                priority=acc.priority,
                status=new_status,
                last_used_at=now_iso,
                notes=acc.notes,
                created_at=acc.created_at,
                updated_at=now_iso,
            )

            usage_record = UsageRecord(
                id=history_id,
                account_id=acc.id,  # type: ignore
                amount=amount,
                used_today_after=used_today_after,
                used_month_after=used_month_after,
                mode=state.mode.value,
                note=note,
                timestamp=now_iso,
            )

            # In AUTO mode, if account became EXHAUSTED, rotate to next eligible immediately
            if state.mode == Mode.AUTO and new_status == AccountStatus.EXHAUSTED:
                next_acc = self._select_next_eligible(
                    conn, state.strategy, current_id=acc.id, force_advance=False
                )
                state.current_account_id = next_acc.id if next_acc else None
                self._save_system_state(conn, state)

            return updated_account, usage_record

    def can_consume(self, cost: int) -> Tuple[bool, Optional[Account], str]:
        """Pre-check whether an account has capacity for `cost` credits.

        Does not create a reservation or mutate credit counters.
        In AUTO mode: checks active account; if insufficient, checks if another account can take it.
        In MANUAL mode: checks currently pinned account only.
        :return: (can_execute, selected_account, explanation)
        """
        if not isinstance(cost, int) or cost <= 0:
            raise ValueError("Credit cost must be a positive integer.")

        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)
            reserved_map = self._get_active_reservations_map(conn, now_iso)

            curr_acc: Optional[Account] = None
            if state.current_account_id:
                curr_acc = self._find_account(conn, state.current_account_id)

            if state.mode == Mode.MANUAL:
                if curr_acc is None:
                    return False, None, "MANUAL mode: No account currently selected."
                if not curr_acc.is_eligible:
                    return False, curr_acc, f"MANUAL mode: Account '{curr_acc.email}' is not active/eligible."
                if not curr_acc.has_capacity(cost, reserved_map.get(curr_acc.id or 0, 0)):
                    avail = curr_acc.available_credits - reserved_map.get(curr_acc.id or 0, 0)
                    return False, curr_acc, f"MANUAL mode: Account '{curr_acc.email}' lacks capacity (available: {avail}, required: {cost})."
                return True, curr_acc, f"Account '{curr_acc.email}' has sufficient capacity for {cost} credits."

            # AUTO mode
            if curr_acc and curr_acc.is_eligible and curr_acc.has_capacity(cost, reserved_map.get(curr_acc.id or 0, 0)):
                return True, curr_acc, f"Active account '{curr_acc.email}' has sufficient capacity for {cost} credits."

            # Current account cannot take it, check if another eligible account can
            candidate = self._select_next_eligible(
                conn, state.strategy, state.current_account_id, force_advance=False,
                required_cost=cost, reserved_map=reserved_map,
            )
            if candidate:
                return True, candidate, f"AUTO mode: Account '{candidate.email}' selected with sufficient capacity for {cost} credits."

            return False, None, f"NO_ACCOUNTS_AVAILABLE: No accounts in pool have sufficient capacity for {cost} credits."

    def reserve(self, cost: int, note: str = "", ttl_seconds: int = 1800) -> Tuple[Account, Reservation]:
        """Pre-check capacity and create a PENDING credit reservation.

        In AUTO mode: automatically rotates if current account lacks capacity.
        In MANUAL mode: verifies current account capacity.
        Reservation locks estimated credits against concurrency race conditions.
        :return: (selected_account, reservation)
        """
        if not isinstance(cost, int) or cost <= 0:
            raise ValueError("Credit cost to reserve must be a positive integer.")

        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)
            reserved_map = self._get_active_reservations_map(conn, now_iso)

            curr_acc: Optional[Account] = None
            if state.current_account_id:
                curr_acc = self._find_account(conn, state.current_account_id)

            target_acc: Optional[Account] = None

            if state.mode == Mode.MANUAL:
                if curr_acc is None:
                    raise InvalidOperationError("MANUAL mode: No account currently selected.")
                if not curr_acc.is_eligible:
                    raise InvalidOperationError(f"MANUAL mode: Account '{curr_acc.email}' is not active/eligible.")
                if not curr_acc.has_capacity(cost, reserved_map.get(curr_acc.id or 0, 0)):
                    avail = curr_acc.available_credits - reserved_map.get(curr_acc.id or 0, 0)
                    raise InvalidOperationError(
                        f"MANUAL mode: Account '{curr_acc.email}' has insufficient capacity "
                        f"(available: {avail}, required: {cost})."
                    )
                target_acc = curr_acc
            else:
                # AUTO mode
                if curr_acc and curr_acc.is_eligible and curr_acc.has_capacity(cost, reserved_map.get(curr_acc.id or 0, 0)):
                    target_acc = curr_acc
                else:
                    target_acc = self._select_next_eligible(
                        conn, state.strategy, state.current_account_id, force_advance=False,
                        required_cost=cost, reserved_map=reserved_map,
                    )
                    if target_acc is None:
                        raise NoAccountsAvailableError(
                            f"NO_ACCOUNTS_AVAILABLE: No accounts have sufficient capacity for {cost} credits."
                        )
                    state.current_account_id = target_acc.id
                    self._save_system_state(conn, state)

            res_id = f"res_{uuid.uuid4().hex[:12]}"
            dt_now = datetime.fromisoformat(now_iso)
            expires_at = (dt_now + timedelta(seconds=ttl_seconds)).isoformat()

            conn.execute(
                """
                INSERT INTO reservations (
                    id, account_id, estimated_cost, status, note, created_at, expires_at
                ) VALUES (?, ?, ?, 'PENDING', ?, ?, ?);
                """,
                (res_id, target_acc.id, cost, note, now_iso, expires_at),
            )

            reservation = Reservation(
                id=res_id,
                account_id=target_acc.id,  # type: ignore
                estimated_cost=cost,
                actual_amount=None,
                status=ReservationStatus.PENDING,
                note=note,
                created_at=now_iso,
                expires_at=expires_at,
                committed_at=None,
                cancelled_at=None,
                account_email=target_acc.email,
            )
            return target_acc, reservation

    def confirm_reservation(
        self, reservation_id: str, actual_amount: Optional[int] = None, note: str = ""
    ) -> Tuple[Account, UsageRecord]:
        """Confirm (commit) a pending reservation, debiting actual credits to usage history."""
        if not reservation_id or not isinstance(reservation_id, str):
            raise ValueError("A valid reservation_id string is required.")

        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)

            row = conn.execute("SELECT * FROM reservations WHERE id = ?;", (reservation_id.strip(),)).fetchone()
            if not row:
                raise ValueError(f"Reservation '{reservation_id}' not found.")

            res = self._row_to_reservation(row)
            if res.status != ReservationStatus.PENDING:
                raise InvalidOperationError(f"Reservation '{reservation_id}' is already {res.status.value}.")

            if res.expires_at < now_iso:
                conn.execute("UPDATE reservations SET status = 'EXPIRED' WHERE id = ?;", (res.id,))
                raise InvalidOperationError(f"Reservation '{reservation_id}' has expired.")

            charge_amount = actual_amount if actual_amount is not None else res.estimated_cost
            if not isinstance(charge_amount, int) or charge_amount < 0:
                raise ValueError("Actual amount to charge must be a non-negative integer.")

            acc = self._find_account(conn, res.account_id)
            if not acc:
                raise AccountNotFoundError(f"Account associated with reservation #{res.account_id} not found.")

            used_today_after = acc.used_today + charge_amount
            used_month_after = acc.used_month + charge_amount

            new_status = acc.status
            if used_today_after >= acc.daily_limit:
                new_status = AccountStatus.EXHAUSTED
            elif acc.monthly_limit is not None and acc.monthly_limit > 0 and used_month_after >= acc.monthly_limit:
                new_status = AccountStatus.EXHAUSTED

            conn.execute(
                """
                UPDATE accounts
                SET used_today = ?, used_month = ?, status = ?,
                    last_used_at = ?, updated_at = ?
                WHERE id = ?;
                """,
                (used_today_after, used_month_after, new_status.value, now_iso, now_iso, acc.id),
            )

            effective_note = note or res.note or f"Confirmed reservation {res.id}"
            cursor = conn.execute(
                """
                INSERT INTO usage_history (
                    account_id, amount, used_today_after, used_month_after, mode, note, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    acc.id,
                    charge_amount,
                    used_today_after,
                    used_month_after,
                    state.mode.value,
                    effective_note,
                    now_iso,
                ),
            )
            history_id = cursor.lastrowid

            conn.execute(
                """
                UPDATE reservations
                SET status = 'COMMITTED', actual_amount = ?, committed_at = ?, note = ?
                WHERE id = ?;
                """,
                (charge_amount, now_iso, effective_note, res.id),
            )

            updated_account = Account(
                id=acc.id,
                email=acc.email,
                credential_ref=acc.credential_ref,
                daily_limit=acc.daily_limit,
                monthly_limit=acc.monthly_limit,
                used_today=used_today_after,
                used_month=used_month_after,
                priority=acc.priority,
                status=new_status,
                last_used_at=now_iso,
                notes=acc.notes,
                created_at=acc.created_at,
                updated_at=now_iso,
            )

            usage_record = UsageRecord(
                id=history_id,
                account_id=acc.id,  # type: ignore
                amount=charge_amount,
                used_today_after=used_today_after,
                used_month_after=used_month_after,
                mode=state.mode.value,
                note=effective_note,
                timestamp=now_iso,
            )

            # In AUTO mode, if account became EXHAUSTED, rotate to next eligible immediately
            if state.mode == Mode.AUTO and new_status == AccountStatus.EXHAUSTED:
                next_acc = self._select_next_eligible(
                    conn, state.strategy, current_id=acc.id, force_advance=False
                )
                state.current_account_id = next_acc.id if next_acc else None
                self._save_system_state(conn, state)

            return updated_account, usage_record

    def cancel_reservation(self, reservation_id: str, reason: str = "") -> Reservation:
        """Cancel a pending reservation, releasing reserved capacity without deducting credits."""
        if not reservation_id or not isinstance(reservation_id, str):
            raise ValueError("A valid reservation_id string is required.")

        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)

            row = conn.execute("SELECT * FROM reservations WHERE id = ?;", (reservation_id.strip(),)).fetchone()
            if not row:
                raise ValueError(f"Reservation '{reservation_id}' not found.")

            res = self._row_to_reservation(row)
            if res.status != ReservationStatus.PENDING:
                raise InvalidOperationError(f"Reservation '{reservation_id}' is already {res.status.value}.")

            note = reason or res.note
            conn.execute(
                """
                UPDATE reservations
                SET status = 'CANCELLED', cancelled_at = ?, note = ?
                WHERE id = ?;
                """,
                (now_iso, note, res.id),
            )
            res.status = ReservationStatus.CANCELLED
            res.cancelled_at = now_iso
            res.note = note
            return res

    def list_reservations(self, status: Optional[str] = None, limit: int = 50) -> List[Reservation]:
        """List recent reservations, optionally filtered by status."""
        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)
            self._get_active_reservations_map(conn, now_iso)

            query = "SELECT r.*, a.email as account_email FROM reservations r JOIN accounts a ON r.account_id = a.id"
            params = []
            if status:
                query += " WHERE r.status = ?"
                params.append(status.strip().upper())
            query += " ORDER BY r.created_at DESC LIMIT ?;"
            params.append(max(1, limit))

            rows = conn.execute(query, tuple(params)).fetchall()
            reservations = []
            for r in rows:
                res = self._row_to_reservation(r)
                res.account_email = r["account_email"]
                reservations.append(res)
            return reservations

    def sync(self, xlsx_path: str = "config/flow_accounts.xlsx") -> Dict[str, Any]:
        """Synchronize accounts and system settings from central XLSX configuration."""
        records, settings_dict, errors = read_central_config_xlsx(xlsx_path)

        with self.db.transaction() as conn:
            check_and_apply_reset(conn)
            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)

            # 1. Update settings if specified in SETTINGS sheet
            if "strategy" in settings_dict:
                state.strategy = Strategy(settings_dict["strategy"])
            if "default_daily_limit" in settings_dict:
                state.default_daily_limit = settings_dict["default_daily_limit"]
            if "timezone" in settings_dict:
                state.timezone = settings_dict["timezone"]
            if "mode" in settings_dict:
                state.mode = Mode(settings_dict["mode"])
            self._save_system_state(conn, state)

            # 2. Synchronize accounts
            existing_rows = conn.execute("SELECT * FROM accounts;").fetchall()
            existing_map = {r["email"].lower(): self._row_to_account(r) for r in existing_rows}
            seen_emails = set()

            added = 0
            updated = 0
            disabled = 0

            for rec in records:
                email = rec["email"].lower()
                seen_emails.add(email)
                enabled = rec.get("enabled", True)

                if email in existing_map:
                    current_acc = existing_map[email]
                    # Determine new status:
                    if not enabled:
                        new_status = AccountStatus.DISABLED.value
                    elif enabled and current_acc.status == AccountStatus.DISABLED:
                        new_status = AccountStatus.ACTIVE.value
                    else:
                        new_status = current_acc.status.value

                    conn.execute(
                        """
                        UPDATE accounts
                        SET priority = ?, daily_limit = ?, monthly_limit = ?,
                            credential_ref = ?, notes = ?, status = ?, updated_at = ?
                        WHERE id = ?;
                        """,
                        (
                            rec["priority"],
                            rec["daily_limit"],
                            rec["monthly_limit"],
                            rec["credential_ref"],
                            rec["notes"],
                            new_status,
                            now_iso,
                            current_acc.id,
                        ),
                    )
                    updated += 1
                else:
                    initial_status = AccountStatus.ACTIVE.value if enabled else AccountStatus.DISABLED.value
                    conn.execute(
                        """
                        INSERT INTO accounts (
                            email, credential_ref, daily_limit, monthly_limit,
                            used_today, used_month, priority, status,
                            last_used_at, notes, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 0, 0, ?, ?, NULL, ?, ?, ?);
                        """,
                        (
                            rec["email"],
                            rec["credential_ref"],
                            rec["daily_limit"],
                            rec["monthly_limit"],
                            rec["priority"],
                            initial_status,
                            rec["notes"],
                            now_iso,
                            now_iso,
                        ),
                    )
                    added += 1

            # 3. Archive / disable accounts that disappeared from XLSX without destroying history
            for email, current_acc in existing_map.items():
                if email not in seen_emails:
                    if current_acc.status not in (AccountStatus.DISABLED, AccountStatus.ARCHIVED):
                        conn.execute(
                            "UPDATE accounts SET status = 'DISABLED', updated_at = ? WHERE id = ?;",
                            (now_iso, current_acc.id),
                        )
                        disabled += 1

            return {
                "added": added,
                "updated": updated,
                "disabled": disabled,
                "total_in_sheet": len(seen_emails),
                "settings": settings_dict,
                "errors": errors,
            }

    def set_mode(self, mode: Union[Mode, str]) -> Mode:
        """Set operation mode (AUTO or MANUAL)."""
        val = mode.value if isinstance(mode, Mode) else str(mode).strip().upper()
        if val not in {m.value for m in Mode}:
            raise ValueError(f"Invalid mode '{mode}'. Must be AUTO or MANUAL.")
        with self.db.transaction() as conn:
            state = self._get_system_state(conn)
            state.mode = Mode(val)
            self._save_system_state(conn, state)
            return state.mode

    def set_strategy(self, strategy: Union[Strategy, str]) -> Strategy:
        """Set account selection strategy."""
        val = strategy.value if isinstance(strategy, Strategy) else str(strategy).strip().lower()
        if val not in {s.value for s in Strategy}:
            raise ValueError(
                f"Invalid strategy '{strategy}'. Valid: sequential, priority, round-robin, lowest-usage, random."
            )
        with self.db.transaction() as conn:
            state = self._get_system_state(conn)
            state.strategy = Strategy(val)
            self._save_system_state(conn, state)
            return state.strategy

    def set_default_limit(self, limit: int) -> int:
        """Set the system-wide default daily limit for newly created accounts."""
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("Default daily limit must be a positive integer.")
        with self.db.transaction() as conn:
            state = self._get_system_state(conn)
            state.default_daily_limit = limit
            self._save_system_state(conn, state)
            return limit

    def set_limit(
        self,
        account_id_or_email: Union[int, str],
        daily_limit: int,
        monthly_limit: Union[int, None, object] = ...,
        clear_monthly: bool = False,
    ) -> Account:
        """Update credit limits for a specific account.

        Preserves an existing monthly limit unless explicitly cleared or replaced.
        """
        if not isinstance(daily_limit, int) or daily_limit <= 0:
            raise ValueError("Daily limit must be a positive integer.")

        with self.db.transaction() as conn:
            acc = self._find_account(conn, account_id_or_email)
            if not acc:
                raise AccountNotFoundError(f"Account '{account_id_or_email}' not found.")

            if clear_monthly:
                effective_monthly = None
            elif monthly_limit is ...:
                effective_monthly = acc.monthly_limit
            elif monthly_limit is None or monthly_limit == 0:
                effective_monthly = None
            elif isinstance(monthly_limit, int) and monthly_limit > 0:
                effective_monthly = monthly_limit
            else:
                raise ValueError("Monthly limit must be a positive integer or None.")

            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)

            new_status = acc.status
            if acc.status == AccountStatus.EXHAUSTED:
                under_daily = acc.used_today < daily_limit
                under_monthly = (
                    effective_monthly is None
                    or effective_monthly <= 0
                    or acc.used_month < effective_monthly
                )
                if under_daily and under_monthly:
                    new_status = AccountStatus.ACTIVE
            elif acc.status == AccountStatus.ACTIVE:
                reached_daily = acc.used_today >= daily_limit
                reached_monthly = (
                    effective_monthly is not None
                    and effective_monthly > 0
                    and acc.used_month >= effective_monthly
                )
                if reached_daily or reached_monthly:
                    new_status = AccountStatus.EXHAUSTED

            conn.execute(
                """
                UPDATE accounts
                SET daily_limit = ?, monthly_limit = ?, status = ?, updated_at = ?
                WHERE id = ?;
                """,
                (daily_limit, effective_monthly, new_status.value, now_iso, acc.id),
            )
            return self._find_account(conn, acc.id)  # type: ignore

    def enable(self, account_id_or_email: Union[int, str]) -> Account:
        """Enable an account.

        Sets status to ACTIVE (or EXHAUSTED if it has already reached daily/monthly limits).
        """
        with self.db.transaction() as conn:
            acc = self._find_account(conn, account_id_or_email)
            if not acc:
                raise AccountNotFoundError(f"Account '{account_id_or_email}' not found.")

            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)

            new_status = AccountStatus.ACTIVE
            if acc.used_today >= acc.daily_limit:
                new_status = AccountStatus.EXHAUSTED
            elif acc.monthly_limit is not None and acc.monthly_limit > 0 and acc.used_month >= acc.monthly_limit:
                new_status = AccountStatus.EXHAUSTED

            conn.execute(
                "UPDATE accounts SET status = ?, updated_at = ? WHERE id = ?;",
                (new_status.value, now_iso, acc.id),
            )
            return self._find_account(conn, acc.id)  # type: ignore

    def disable(self, account_id_or_email: Union[int, str]) -> Account:
        """Disable an account, preventing automation from selecting it.

        If disabled account was currently active in AUTO mode, rotates to next eligible.
        """
        with self.db.transaction() as conn:
            acc = self._find_account(conn, account_id_or_email)
            if not acc:
                raise AccountNotFoundError(f"Account '{account_id_or_email}' not found.")

            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)

            conn.execute(
                "UPDATE accounts SET status = ?, updated_at = ? WHERE id = ?;",
                (AccountStatus.DISABLED.value, now_iso, acc.id),
            )

            # Auto-rotate if current account in AUTO
            if state.mode == Mode.AUTO and state.current_account_id == acc.id:
                next_acc = self._select_next_eligible(
                    conn, state.strategy, current_id=acc.id, force_advance=False
                )
                state.current_account_id = next_acc.id if next_acc else None
                self._save_system_state(conn, state)

            return self._find_account(conn, acc.id)  # type: ignore

    def human_required(self, account_id_or_email: Union[int, str], reason: str = "") -> Account:
        """Mark an account as requiring human intervention (CAPTCHA, verification, etc).

        Immediately suspends automation on this account.
        In AUTO mode, rotates away to the next eligible account.
        """
        with self.db.transaction() as conn:
            acc = self._find_account(conn, account_id_or_email)
            if not acc:
                raise AccountNotFoundError(f"Account '{account_id_or_email}' not found.")

            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)

            note_entry = f"[HUMAN_REQUIRED {now_iso[:10]}] {reason}".strip()
            updated_notes = (acc.notes + "\n" + note_entry).strip() if acc.notes else note_entry

            conn.execute(
                "UPDATE accounts SET status = ?, notes = ?, updated_at = ? WHERE id = ?;",
                (AccountStatus.NEEDS_HUMAN.value, updated_notes, now_iso, acc.id),
            )

            # Rotate away in AUTO mode
            if state.mode == Mode.AUTO and state.current_account_id == acc.id:
                next_acc = self._select_next_eligible(
                    conn, state.strategy, current_id=acc.id, force_advance=False
                )
                state.current_account_id = next_acc.id if next_acc else None
                self._save_system_state(conn, state)

            return self._find_account(conn, acc.id)  # type: ignore

    def human_cleared(self, account_id_or_email: Union[int, str]) -> Account:
        """Clear human verification flag after verification has been completed.

        Restores status to ACTIVE (or EXHAUSTED if limits already exceeded).
        """
        with self.db.transaction() as conn:
            acc = self._find_account(conn, account_id_or_email)
            if not acc:
                raise AccountNotFoundError(f"Account '{account_id_or_email}' not found.")

            state = self._get_system_state(conn)
            now_iso = self._now_iso(state.timezone)

            new_status = AccountStatus.ACTIVE
            if acc.used_today >= acc.daily_limit:
                new_status = AccountStatus.EXHAUSTED
            elif acc.monthly_limit is not None and acc.monthly_limit > 0 and acc.used_month >= acc.monthly_limit:
                new_status = AccountStatus.EXHAUSTED

            note_entry = f"[HUMAN_CLEARED {now_iso[:10]}] Verification resolved."
            updated_notes = (acc.notes + "\n" + note_entry).strip() if acc.notes else note_entry

            conn.execute(
                "UPDATE accounts SET status = ?, notes = ?, updated_at = ? WHERE id = ?;",
                (new_status.value, updated_notes, now_iso, acc.id),
            )
            return self._find_account(conn, acc.id)  # type: ignore

    def add_account(
        self,
        email: str,
        daily_limit: Optional[int] = None,
        monthly_limit: Optional[int] = None,
        priority: int = 1,
        credential_ref: str = "",
        notes: str = "",
        status: AccountStatus = AccountStatus.ACTIVE,
    ) -> Account:
        """Add a single new account to the manager."""
        clean_email = email.strip().lower()
        if not clean_email or "@" not in clean_email:
            raise ValueError(f"Invalid email address: '{email}'")

        clean_credential_ref = credential_ref.strip()
        if not validate_credential_ref(clean_credential_ref):
            raise ValueError(
                f"Invalid credential_ref '{credential_ref}'. "
                "Must be an indirect reference like ENV:VAR, FILE:PATH, VAULT:KEY, OP:ITEM, or empty."
            )

        with self.db.transaction() as conn:
            existing = self._find_account(conn, clean_email)
            if existing:
                raise ValueError(f"Account with email '{clean_email}' already exists.")

            state = self._get_system_state(conn)
            eff_daily_limit = daily_limit if daily_limit is not None and daily_limit > 0 else state.default_daily_limit
            now_iso = self._now_iso(state.timezone)

            cursor = conn.execute(
                """
                INSERT INTO accounts (
                    email, credential_ref, daily_limit, monthly_limit,
                    used_today, used_month, priority, status,
                    last_used_at, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, 0, ?, ?, NULL, ?, ?, ?);
                """,
                (
                    clean_email,
                    credential_ref.strip(),
                    eff_daily_limit,
                    monthly_limit,
                    max(1, priority),
                    status.value if isinstance(status, AccountStatus) else status,
                    notes.strip(),
                    now_iso,
                    now_iso,
                ),
            )
            new_id = cursor.lastrowid
            return self._find_account(conn, new_id)  # type: ignore

    def import_accounts(self, filepath: str) -> Dict[str, Any]:
        """Import or update accounts from a CSV or XLSX file."""
        with self.db.transaction() as conn:
            state = self._get_system_state(conn)
            records, errors = read_accounts_from_file(filepath, state.default_daily_limit)

            imported = 0
            updated = 0
            skipped = 0

            for rec in records:
                email = rec["email"]
                existing = self._find_account(conn, email)
                now_iso = self._now_iso(state.timezone)

                if existing:
                    # Update existing account properties
                    effective_status = rec["status"] if rec.get("status") is not None else existing.status.value
                    conn.execute(
                        """
                        UPDATE accounts
                        SET daily_limit = ?, monthly_limit = ?, priority = ?,
                            status = ?, credential_ref = ?, notes = ?, updated_at = ?
                        WHERE id = ?;
                        """,
                        (
                            rec["daily_limit"],
                            rec["monthly_limit"],
                            rec["priority"],
                            effective_status,
                            rec["credential_ref"] or existing.credential_ref,
                            rec["notes"] or existing.notes,
                            now_iso,
                            existing.id,
                        ),
                    )
                    updated += 1
                else:
                    initial_status = rec["status"] if rec.get("status") is not None else AccountStatus.ACTIVE.value
                    conn.execute(
                        """
                        INSERT INTO accounts (
                            email, credential_ref, daily_limit, monthly_limit,
                            used_today, used_month, priority, status,
                            last_used_at, notes, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 0, 0, ?, ?, NULL, ?, ?, ?);
                        """,
                        (
                            email,
                            rec["credential_ref"],
                            rec["daily_limit"],
                            rec["monthly_limit"],
                            rec["priority"],
                            initial_status,
                            rec["notes"],
                            now_iso,
                            now_iso,
                        ),
                    )
                    imported += 1

            return {
                "imported": imported,
                "updated": updated,
                "skipped": skipped,
                "errors": errors,
            }

    def history(self, limit: int = 50, account_id: Optional[int] = None) -> List[UsageRecord]:
        """Retrieve usage history audit log."""
        with self.db.connect() as conn:
            if account_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM usage_history
                    WHERE account_id = ?
                    ORDER BY id DESC
                    LIMIT ?;
                    """,
                    (account_id, max(1, limit)),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM usage_history
                    ORDER BY id DESC
                    LIMIT ?;
                    """,
                    (max(1, limit),),
                )
            rows = cursor.fetchall()
            return [
                UsageRecord(
                    id=r["id"],
                    account_id=r["account_id"],
                    amount=r["amount"],
                    used_today_after=r["used_today_after"],
                    used_month_after=r["used_month_after"],
                    mode=r["mode"],
                    note=r["note"],
                    timestamp=r["timestamp"],
                )
                for r in rows
            ]

    def reset_daily(self) -> Dict[str, Any]:
        """Manually trigger daily reset."""
        with self.db.transaction() as conn:
            return db_reset_daily(conn, force=True)

    def reset_monthly(self) -> Dict[str, Any]:
        """Manually trigger monthly reset."""
        with self.db.transaction() as conn:
            return db_reset_monthly(conn, force=True)
