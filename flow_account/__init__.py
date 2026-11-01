"""Flow Credit Rotator.

Multi-account credit rotation and quota manager for Google Flow automation agents.
"""

__version__ = "1.2.1"

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
from flow_account.manager import (
    FlowAccountManager,
    FlowAccountError,
    NoAccountsAvailableError,
    AccountNotFoundError,
    InvalidOperationError,
)
from flow_account.credentials import (
    get_credential,
    set_credential,
    has_credential,
)

__all__ = [
    "__version__",
    "FlowAccountManager",
    "Account",
    "AccountStatus",
    "Mode",
    "Reservation",
    "ReservationStatus",
    "Strategy",
    "SystemState",
    "UsageRecord",
    "validate_credential_ref",
    "FlowAccountError",
    "NoAccountsAvailableError",
    "AccountNotFoundError",
    "InvalidOperationError",
    "get_credential",
    "set_credential",
    "has_credential",
]
