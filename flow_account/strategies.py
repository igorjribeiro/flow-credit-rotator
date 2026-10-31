"""Selection strategies for Google Flow Account Manager.

Implements Sequential (with Priority alias), Round-Robin, Lowest-Usage,
and Random algorithms with deterministic tie-breaking.
"""

from abc import ABC, abstractmethod
import random
from typing import List, Optional, Union

from flow_account.models import Account, AccountStatus, Strategy


def is_account_eligible(account: Account) -> bool:
    """Determine if an account meets eligibility criteria for selection."""
    if account.status != AccountStatus.ACTIVE:
        return False
    if account.used_today >= account.daily_limit:
        return False
    if account.monthly_limit is not None and account.monthly_limit > 0:
        if account.used_month >= account.monthly_limit:
            return False
    return True


class BaseStrategy(ABC):
    """Abstract base class for account selection strategies."""

    name: str = "base"

    @abstractmethod
    def select(
        self,
        eligible_accounts: List[Account],
        current_account_id: Optional[int] = None,
        force_advance: bool = False,
        current_account: Optional[Account] = None,
    ) -> Optional[Account]:
        """Select an account from the eligible list.

        :param eligible_accounts: List of currently eligible accounts.
        :param current_account_id: ID of the currently selected account, if any.
        :param force_advance: Whether to explicitly advance to the next account.
        :param current_account: Currently selected Account entity, if available.
        :return: Selected Account, or None if no accounts are eligible.
        """
        pass


class SequentialStrategy(BaseStrategy):
    """Sequential Strategy: cycles accounts in order of priority and registration.

    Follows the deterministic sequence defined at registration/import (priority, id):
    1 -> 2 -> 3 -> ...
    - Keeps using Account 1 while it is available and eligible.
    - If Account 1 reaches daily/monthly limit or becomes unavailable (DISABLED, NEEDS_HUMAN),
      advances to Account 2.
    - If Account 2 reaches limit, advances to Account 3.
    - On reaching the end of the sequence, wraps back to the first eligible account.
    - Accounts that are DISABLED, NEEDS_HUMAN, or exhausted are skipped during auto-rotation.
    """

    name = Strategy.SEQUENTIAL.value

    def select(
        self,
        eligible_accounts: List[Account],
        current_account_id: Optional[int] = None,
        force_advance: bool = False,
        current_account: Optional[Account] = None,
    ) -> Optional[Account]:
        if not eligible_accounts:
            return None

        # Deterministic ordering by priority, then ID
        ordered = sorted(
            eligible_accounts,
            key=lambda a: (a.priority, a.id or 0),
        )

        # If no current account is set, start at the first eligible account
        if current_account_id is None:
            return ordered[0]

        # Check if current account is in the eligible list
        curr_in_eligible = None
        for i, acc in enumerate(ordered):
            if acc.id == current_account_id:
                curr_in_eligible = (i, acc)
                break

        if curr_in_eligible is not None:
            idx, acc = curr_in_eligible
            if not force_advance:
                # Account is still eligible: keep using it ("Conta 1 disponível -> usa conta 1")
                return acc
            else:
                # Explicit rotation request (e.g. next_account()): advance to next in sequence
                next_idx = (idx + 1) % len(ordered)
                return ordered[next_idx]

        # Current account is NOT eligible (exhausted, disabled, needs_human, etc.)
        # Find the next eligible account after current_account in the sequence
        if current_account is not None:
            curr_ref = (current_account.priority, current_account.id or 0)
            after = [acc for acc in ordered if (acc.priority, acc.id or 0) > curr_ref]
        else:
            after = [acc for acc in ordered if (acc.id or 0) > current_account_id]

        if after:
            return after[0]

        # Reached the end of the sequence: wrap back to the first eligible account
        return ordered[0]


class PriorityStrategy(SequentialStrategy):
    """Backward-compatible alias for SequentialStrategy."""

    name = Strategy.PRIORITY.value


class RoundRobinStrategy(BaseStrategy):
    """Round-Robin Strategy: cycles sequentially across eligible accounts.

    Sorted by ID for a stable, predictable sequence.
    """

    name = Strategy.ROUND_ROBIN.value

    def select(
        self,
        eligible_accounts: List[Account],
        current_account_id: Optional[int] = None,
        force_advance: bool = False,
        current_account: Optional[Account] = None,
    ) -> Optional[Account]:
        if not eligible_accounts:
            return None

        # Deterministic ordering by ID
        ordered = sorted(eligible_accounts, key=lambda a: (a.id or 0))

        if current_account_id is None:
            return ordered[0]

        # Find current index
        current_idx = None
        for i, acc in enumerate(ordered):
            if acc.id == current_account_id:
                current_idx = i
                break

        if current_idx is None:
            # Current account was exhausted or not in eligible list
            if current_account_id is not None:
                after = [acc for acc in ordered if (acc.id or 0) > current_account_id]
                if after:
                    return after[0]
            return ordered[0]

        if force_advance:
            next_idx = (current_idx + 1) % len(ordered)
            return ordered[next_idx]

        # If not force_advance and current account is eligible, keep it
        return ordered[current_idx]


class LowestUsageStrategy(BaseStrategy):
    """Lowest-Usage Strategy: chooses account with lowest used_today.

    Tie-breaking:
    1. used_today (ascending)
    2. priority (ascending)
    3. used_month (ascending)
    4. last_used_at (oldest first)
    5. id (ascending)
    """

    name = Strategy.LOWEST_USAGE.value

    def select(
        self,
        eligible_accounts: List[Account],
        current_account_id: Optional[int] = None,
        force_advance: bool = False,
        current_account: Optional[Account] = None,
    ) -> Optional[Account]:
        if not eligible_accounts:
            return None

        sorted_accs = sorted(
            eligible_accounts,
            key=lambda a: (
                a.used_today,
                a.priority,
                a.used_month,
                a.last_used_at if a.last_used_at else "",
                a.id or 0,
            ),
        )

        if not force_advance:
            return sorted_accs[0]

        if current_account_id is not None:
            for i, acc in enumerate(sorted_accs):
                if acc.id == current_account_id:
                    next_idx = (i + 1) % len(sorted_accs)
                    return sorted_accs[next_idx]

        return sorted_accs[0]


class RandomStrategy(BaseStrategy):
    """Random Strategy: selects uniformly at random among eligible accounts.

    Only accounts that are strictly eligible (status ACTIVE, available daily
    and monthly credits) are candidates for selection. Never selects blocked,
    disabled, NEEDS_HUMAN, or zero-credit accounts.
    """

    name = Strategy.RANDOM.value

    def select(
        self,
        eligible_accounts: List[Account],
        current_account_id: Optional[int] = None,
        force_advance: bool = False,
        current_account: Optional[Account] = None,
    ) -> Optional[Account]:
        if not eligible_accounts:
            return None

        # Filter strictly eligible
        candidates = [acc for acc in eligible_accounts if acc.is_eligible]
        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0]

        if force_advance and current_account_id is not None:
            # Prefer a different eligible account if advancing explicitly
            others = [acc for acc in candidates if acc.id != current_account_id]
            if others:
                return random.choice(others)

        return random.choice(candidates)


def get_strategy(strategy_name: Union[Strategy, str]) -> BaseStrategy:
    """Factory to retrieve a strategy instance by name or enum."""
    val = strategy_name.value if isinstance(strategy_name, Strategy) else str(strategy_name).strip().lower()
    if val == Strategy.SEQUENTIAL.value:
        return SequentialStrategy()
    elif val == Strategy.PRIORITY.value:
        return PriorityStrategy()
    elif val == Strategy.ROUND_ROBIN.value:
        return RoundRobinStrategy()
    elif val == Strategy.LOWEST_USAGE.value:
        return LowestUsageStrategy()
    elif val == Strategy.RANDOM.value:
        return RandomStrategy()
    else:
        raise ValueError(
            f"Unknown strategy: '{strategy_name}'. Valid strategies: sequential, priority, round-robin, lowest-usage, random"
        )
