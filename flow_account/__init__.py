"""Flow Credit Rotator.

Multi-account credit rotation and quota manager for Google Flow automation agents.
"""

__version__ = "1.0.0"

from flow_account.models import (
    Account,
    AccountStatus,
    Mode,
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

__all__ = [
    "__version__",
    "FlowAccountManager",
    "Account",
    "AccountStatus",
    "Mode",
    "Strategy",
    "SystemState",
    "UsageRecord",
    "validate_credential_ref",
    "FlowAccountError",
    "NoAccountsAvailableError",
    "AccountNotFoundError",
    "InvalidOperationError",
]
