"""Data models and enums for Google Flow Account Manager."""

from dataclasses import dataclass, asdict
from enum import Enum
import re
from typing import Optional, Dict, Any


VALID_CREDENTIAL_REF_PREFIXES = frozenset(
    {
        "ENV",
        "FILE",
        "VAULT",
        "KEYRING",
        "OP",
        "BWS",
        # Backward-compatible aliases already documented by the project.
        "KEYCHAIN",
        "AWS_SECRETS_MANAGER",
        "GCP_SECRET_MANAGER",
    }
)


def validate_credential_ref(ref: str) -> bool:
    """Return whether *ref* is an indirect credential reference or empty.

    Credential values themselves must never be stored in ``credential_ref``.
    References use a recognized, uppercase scheme followed by a non-empty
    locator, for example ``ENV:FLOW_ACCOUNT_01`` or ``VAULT:flow/account``.
    """
    if not isinstance(ref, str):
        return False

    candidate = ref.strip()
    if not candidate:
        return True
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        return False

    prefix, separator, locator = candidate.partition(":")
    if separator != ":" or prefix not in VALID_CREDENTIAL_REF_PREFIXES:
        return False
    if not locator or locator != locator.strip():
        return False

    if prefix == "ENV":
        return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", locator))
    return True


class AccountStatus(str, Enum):
    """Lifecycle status of a managed Google Flow account."""
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    EXHAUSTED = "EXHAUSTED"
    NEEDS_HUMAN = "NEEDS_HUMAN"


class Mode(str, Enum):
    """Operation mode for account selection and rotation."""
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class Strategy(str, Enum):
    """Selection strategy for eligible accounts."""
    PRIORITY = "priority"
    ROUND_ROBIN = "round-robin"
    LOWEST_USAGE = "lowest-usage"


@dataclass
class Account:
    """Represents a Google Flow account entity."""
    id: Optional[int]
    email: str
    credential_ref: str = ""
    daily_limit: int = 85
    monthly_limit: Optional[int] = None
    used_today: int = 0
    used_month: int = 0
    priority: int = 1
    status: AccountStatus = AccountStatus.ACTIVE
    last_used_at: Optional[str] = None
    notes: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def available_credits(self) -> int:
        """Calculate available credits considering daily and monthly limits."""
        daily_avail = max(0, self.daily_limit - self.used_today)
        if self.monthly_limit is not None and self.monthly_limit > 0:
            monthly_avail = max(0, self.monthly_limit - self.used_month)
            return min(daily_avail, monthly_avail)
        return daily_avail

    @property
    def is_eligible(self) -> bool:
        """Check if account is eligible for credit consumption and selection."""
        if self.status != AccountStatus.ACTIVE:
            return False
        if self.used_today >= self.daily_limit:
            return False
        if self.monthly_limit is not None and self.monthly_limit > 0:
            if self.used_month >= self.monthly_limit:
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert account to a serializable dictionary."""
        d = asdict(self)
        if not validate_credential_ref(self.credential_ref):
            d["credential_ref"] = "[INVALID CREDENTIAL REF]"
        d["status"] = self.status.value if isinstance(self.status, AccountStatus) else self.status
        d["available_credits"] = self.available_credits
        d["is_eligible"] = self.is_eligible
        return d


@dataclass
class UsageRecord:
    """Historical audit record for credit consumption."""
    id: Optional[int]
    account_id: int
    amount: int
    used_today_after: int
    used_month_after: int
    mode: str
    note: str = ""
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert usage record to a dictionary."""
        return asdict(self)


@dataclass
class SystemState:
    """Global system configuration and runtime state."""
    mode: Mode = Mode.AUTO
    strategy: Strategy = Strategy.PRIORITY
    current_account_id: Optional[int] = None
    default_daily_limit: int = 85
    last_reset_date: Optional[str] = None
    last_reset_month: Optional[str] = None
    timezone: str = "America/Bahia"

    def to_dict(self) -> Dict[str, Any]:
        """Convert system state to a serializable dictionary."""
        return {
            "mode": self.mode.value if isinstance(self.mode, Mode) else self.mode,
            "strategy": self.strategy.value if isinstance(self.strategy, Strategy) else self.strategy,
            "current_account_id": self.current_account_id,
            "default_daily_limit": self.default_daily_limit,
            "last_reset_date": self.last_reset_date,
            "last_reset_month": self.last_reset_month,
            "timezone": self.timezone,
        }
