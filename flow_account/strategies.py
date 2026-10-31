"""Selection strategies for Google Flow Account Manager.

Implements Priority, Round-Robin, and Lowest-Usage algorithms with deterministic tie-breaking.
"""

from abc import ABC, abstractmethod
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
    ) -> Optional[Account]:
        """Select an account from the eligible list.

        :param eligible_accounts: List of currently eligible accounts.
        :param current_account_id: ID of the currently selected account, if any.
        :param force_advance: Whether to explicitly advance to the next account.
        :return: Selected Account, or None if no accounts are eligible.
        """
        pass


class PriorityStrategy(BaseStrategy):
    """Priority Strategy: lower priority number first (1 is highest priority).

    Tie-breaking:
    1. priority (ascending)
    2. used_today (ascending)
    3. last_used_at (oldest or never used first; None sorts first)
    4. id (ascending)
    """

    name = Strategy.PRIORITY.value

    def select(
        self,
        eligible_accounts: List[Account],
        current_account_id: Optional[int] = None,
        force_advance: bool = False,
    ) -> Optional[Account]:
        if not eligible_accounts:
            return None

        # Sort accounts by priority criteria
        sorted_accs = sorted(
            eligible_accounts,
            key=lambda a: (
                a.priority,
                a.used_today,
                a.last_used_at if a.last_used_at else "",
                a.id or 0,
            ),
        )

        if not force_advance:
            return sorted_accs[0]

        # If force_advance is True (e.g. next_account called explicitly)
        if current_account_id is not None:
            for i, acc in enumerate(sorted_accs):
                if acc.id == current_account_id:
                    next_idx = (i + 1) % len(sorted_accs)
                    return sorted_accs[next_idx]

        return sorted_accs[0]


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


def get_strategy(strategy_name: Union[Strategy, str]) -> BaseStrategy:
    """Factory to retrieve a strategy instance by name or enum."""
    val = strategy_name.value if isinstance(strategy_name, Strategy) else str(strategy_name).strip().lower()
    if val == Strategy.ROUND_ROBIN.value:
        return RoundRobinStrategy()
    elif val == Strategy.LOWEST_USAGE.value:
        return LowestUsageStrategy()
    elif val == Strategy.PRIORITY.value:
        return PriorityStrategy()
    else:
        raise ValueError(
            f"Unknown strategy: '{strategy_name}'. Valid strategies: priority, round-robin, lowest-usage"
        )
