"""Configuration module for Flow Credit Rotator.

Provides default constants, environment variable parsing, and timezone utilities.
Business logic relies on dynamic configuration and database settings, never hardcoded limits.
"""

import os
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Environment variable keys
ENV_DB_PATH = "FLOW_ACCOUNT_DB_PATH"
ENV_DEFAULT_DAILY_LIMIT = "FLOW_ACCOUNT_DEFAULT_DAILY_LIMIT"
ENV_FALLBACK_DAILY_LIMIT = "DEFAULT_DAILY_LIMIT"
ENV_TIMEZONE = "FLOW_ACCOUNT_TIMEZONE"
ENV_STRATEGY = "FLOW_ACCOUNT_STRATEGY"

# Built-in defaults
DEFAULT_DB_PATH = "~/.flow-accounts/flow_accounts.db"
DEFAULT_DAILY_LIMIT = 85
DEFAULT_TIMEZONE_NAME = "America/Bahia"
DEFAULT_STRATEGY_NAME = "sequential"

VALID_STRATEGIES = {"sequential", "priority", "round-robin", "lowest-usage", "random"}
VALID_MODES = {"AUTO", "MANUAL"}


def get_db_path(override_path: Optional[str] = None) -> str:
    """Resolve the SQLite database path.

    Checks:
    1. Direct override argument (used for tests or explicit configuration).
    2. Environment variable FLOW_ACCOUNT_DB_PATH.
    3. Default path '~/.flow-accounts/flow_accounts.db'.
    """
    if override_path:
        return override_path
    env_path = os.getenv(ENV_DB_PATH)
    if env_path:
        return env_path
    return str(Path(os.path.expanduser(DEFAULT_DB_PATH)).resolve())


def get_default_daily_limit() -> int:
    """Get the initial default daily limit from environment or configuration."""
    raw = os.getenv(ENV_DEFAULT_DAILY_LIMIT) or os.getenv(ENV_FALLBACK_DAILY_LIMIT)
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_DAILY_LIMIT


def get_default_timezone_name() -> str:
    """Get the default timezone name from environment or built-in default."""
    tz_name = os.getenv(ENV_TIMEZONE, DEFAULT_TIMEZONE_NAME).strip()
    try:
        ZoneInfo(tz_name)
        return tz_name
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_TIMEZONE_NAME


def get_timezone(tz_name: Optional[str] = None) -> ZoneInfo:
    """Return a ZoneInfo instance for the configured timezone."""
    target = tz_name or get_default_timezone_name()
    try:
        return ZoneInfo(target)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE_NAME)


def get_default_strategy() -> str:
    """Get the default strategy from environment or built-in default."""
    strat = os.getenv(ENV_STRATEGY, DEFAULT_STRATEGY_NAME).strip().lower()
    if strat in VALID_STRATEGIES:
        return strat
    return DEFAULT_STRATEGY_NAME
