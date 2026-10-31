"""Selection strategies for Flow Credit Rotator.

Implements Sequential (with Priority alias), Round-Robin, Lowest-Usage,
and Random algorithms with deterministic tie-breaking and capacity-aware filtering.
"""

from abc import ABC, abstractmethod
import random
from typing import Dict, List, Optional, Union

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


def has_account_capacity(
    account: Account,
    required_cost: int = 0,
    reserved_map: Optional[Dict[int, int]] = None,
) -> bool:
    """Determine if an account is eligible and has capacity for required_cost plus reservations."""
    pending = reserved_map.get(account.id, 0) if reserved_map and account.id is not None else 0
    if required_cost > 0:
        return account.has_capacity(cost=required_cost, pending_reserved=pending)
    if not account.is_eligible:
        return False
    if pending > 0:
        return account.has_capacity(cost=0, pending_reserved=pending) and (account.available_credits - pending > 0)
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
        required_cost: int = 0,
        reserved_map: Optional[Dict[int, int]] = None,
    ) -> Optional[Account]:
        """Select an account from the eligible list with capacity for required_cost.

        :param eligible_accounts: List of currently eligible accounts.
        :param current_account_id: ID of the currently selected account, if any.
        :param force_advance: Whether to explicitly advance to the next account.
        :param current_account: Currently selected Account entity, if available.
        :param required_cost: Pre-check cost in credits required for the operation.
        :param reserved_map: Mapping of account_id to active pending reserved credits.
        :return: Selected Account, or None if no accounts are eligible with capacity.
        """
        pass


class SequentialStrategy(BaseStrategy):
    """Sequential Strategy: cycles accounts in order of priority and registration.

    Follows deterministic sequence (priority, id):
    1 -> 2 -> 3 -> ...
    - Keeps using current account while it has capacity for required_cost.
    - If current account lacks capacity or reaches limit, advances to next account with capacity.
    - Wraps around to the first eligible account with capacity.
    - Accounts that are DISABLED, NEEDS_HUMAN, ARCHIVED, or lack capacity are skipped.
    """

    name = Strategy.SEQUENTIAL.value

    def select(
        self,
        eligible_accounts: List[Account],
        current_account_id: Optional[int] = None,
        force_advance: bool = False,
        current_account: Optional[Account] = None,
        required_cost: int = 0,
        reserved_map: Optional[Dict[int, int]] = None,
    ) -> Optional[Account]:
        if not eligible_accounts:
            return None

        # Filter strictly by capacity for required_cost
        candidates = [
            acc for acc in eligible_accounts
            if has_account_capacity(acc, required_cost=required_cost, reserved_map=reserved_map)
        ]
        if not candidates:
            return None

        # Deterministic ordering by priority, then ID
        ordered = sorted(
            candidates,
            key=lambda a: (a.priority, a.id or 0),
        )

        # If no current account is set, start at the first candidate with capacity
        if current_account_id is None:
            return ordered[0]

        # Check if current account is in candidates
        curr_in_candidates = None
        for i, acc in enumerate(ordered):
            if acc.id == current_account_id:
                curr_in_candidates = (i, acc)
                break

        if curr_in_candidates is not None:
            idx, acc = curr_in_candidates
            if not force_advance:
                # Account still has capacity: keep using it
                return acc
            else:
                # Explicit rotation request: advance to next in sequence
                next_idx = (idx + 1) % len(ordered)
                return ordered[next_idx]

        # Current account lacks capacity, is exhausted, or not in candidate list
        # Find the next candidate with capacity after current_account in sequence
        if current_account is not None:
            curr_ref = (current_account.priority, current_account.id or 0)
            after = [acc for acc in ordered if (acc.priority, acc.id or 0) > curr_ref]
        else:
            after = [acc for acc in ordered if (acc.id or 0) > current_account_id]

        if after:
            return after[0]

        # Reached the end: wrap back to the first candidate with capacity
        return ordered[0]


class PriorityStrategy(SequentialStrategy):
    """Backward-compatible alias for SequentialStrategy."""

    name = Strategy.PRIORITY.value


class RoundRobinStrategy(BaseStrategy):
    """Round-Robin Strategy: cycles sequentially across eligible accounts with capacity.

    Sorted by ID for a stable, predictable sequence.
    """

    name = Strategy.ROUND_ROBIN.value

    def select(
        self,
        eligible_accounts: List[Account],
        current_account_id: Optional[int] = None,
        force_advance: bool = False,
        current_account: Optional[Account] = None,
        required_cost: int = 0,
        reserved_map: Optional[Dict[int, int]] = None,
    ) -> Optional[Account]:
        if not eligible_accounts:
            return None

        candidates = [
            acc for acc in eligible_accounts
            if has_account_capacity(acc, required_cost=required_cost, reserved_map=reserved_map)
        ]
        if not candidates:
            return None

        # Deterministic ordering by ID
        ordered = sorted(candidates, key=lambda a: (a.id or 0))

        if current_account_id is None:
            return ordered[0]

        # Find current index
        current_idx = None
        for i, acc in enumerate(ordered):
            if acc.id == current_account_id:
                current_idx = i
                break

        if current_idx is None:
            if current_account_id is not None:
                after = [acc for acc in ordered if (acc.id or 0) > current_account_id]
                if after:
                    return after[0]
            return ordered[0]

        if force_advance:
            next_idx = (current_idx + 1) % len(ordered)
            return ordered[next_idx]

        # If not force_advance and current account has capacity, keep it
        return ordered[current_idx]


class LowestUsageStrategy(BaseStrategy):
    """Lowest-Usage Strategy: chooses account with lowest used_today (including reservations).

    Tie-breaking:
    1. used_today + reserved (ascending)
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
        required_cost: int = 0,
        reserved_map: Optional[Dict[int, int]] = None,
    ) -> Optional[Account]:
        if not eligible_accounts:
            return None

        candidates = [
            acc for acc in eligible_accounts
            if has_account_capacity(acc, required_cost=required_cost, reserved_map=reserved_map)
        ]
        if not candidates:
            return None

        sorted_accs = sorted(
            candidates,
            key=lambda a: (
                a.used_today + (reserved_map.get(a.id, 0) if reserved_map and a.id else 0),
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
    """Random Strategy: selects uniformly at random among eligible accounts with capacity.

    Only accounts that are strictly eligible and have sufficient capacity for
    required_cost are candidates for selection.
    """

    name = Strategy.RANDOM.value

    def select(
        self,
        eligible_accounts: List[Account],
        current_account_id: Optional[int] = None,
        force_advance: bool = False,
        current_account: Optional[Account] = None,
        required_cost: int = 0,
        reserved_map: Optional[Dict[int, int]] = None,
    ) -> Optional[Account]:
        if not eligible_accounts:
            return None

        # Filter strictly eligible with capacity
        candidates = [
            acc for acc in eligible_accounts
            if has_account_capacity(acc, required_cost=required_cost, reserved_map=reserved_map)
        ]
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
