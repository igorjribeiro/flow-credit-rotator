"""Google Flow Account Manager.

Manage multiple Google Flow accounts with credit limits, rotation, and credential safety.
"""

__version__ = "0.1.0"

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
