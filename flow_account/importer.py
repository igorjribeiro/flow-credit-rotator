"""Account importer supporting CSV, XLSX, and central XLSX synchronization.

Enforces strict security policies: rejects raw credentials or secret columns,
validating input against schema requirements. Secrets are never accepted.
"""

import csv
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import openpyxl

from flow_account.config import (
    DEFAULT_DAILY_LIMIT,
    VALID_MODES,
    VALID_STRATEGIES,
    get_default_daily_limit,
    get_default_timezone_name,
)
from flow_account.models import (
    Account,
    AccountStatus,
    Mode,
    Strategy,
    validate_credential_ref,
)

FORBIDDEN_COLUMNS = {
    "password",
    "pass",
    "senha",
    "secret",
    "pwd",
    "key",
    "token",
    "secret_key",
    "api_key",
}

VALID_COLUMNS = {
    "email",
    "daily_limit",
    "monthly_limit",
    "priority",
    "order",
    "status",
    "enabled",
    "credential_ref",
    "notes",
}

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SecurityImportError(ValueError):
    """Raised when an import file contains forbidden or insecure columns."""
    pass


def validate_column_names(headers: List[str]) -> Dict[str, int]:
    """Check for forbidden secret columns and map recognized column indices.

    :raises SecurityImportError: if any forbidden column header is detected.
    :return: Mapping of normalized column name to header index.
    """
    column_map = {}
    normalized_headers = [
        str(h).strip().lower().replace(" ", "_").replace("-", "_") if h is not None else ""
        for h in headers
    ]

    for idx, col in enumerate(normalized_headers):
        if not col:
            continue
        # Check forbidden columns
        if col in FORBIDDEN_COLUMNS or any(f in col for f in ["password", "senha", "secret"]):
            raise SecurityImportError(
                f"Security rejection: Forbidden column '{col}' detected. "
                "Raw passwords and secrets are strictly prohibited. Use 'credential_ref' instead."
            )
        if col in VALID_COLUMNS:
            column_map[col] = idx

    if "email" not in column_map:
        raise ValueError("Missing required column 'email' in import file.")

    return column_map


def parse_account_row(
    row: List[Any],
    col_map: Dict[str, int],
    default_daily_limit: int,
    row_num: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse and validate an individual spreadsheet/CSV row.

    :return: (parsed_account_dict, error_message)
    """
    def get_val(col_name: str) -> Optional[Any]:
        idx = col_map.get(col_name)
        if idx is not None and idx < len(row):
            val = row[idx]
            if val is not None:
                s = str(val).strip()
                return s if s != "" else None
        return None

    email_val = get_val("email")
    if not email_val:
        return None, f"Row {row_num}: Empty email address"

    email = str(email_val).strip().lower()
    if not EMAIL_REGEX.match(email):
        return None, f"Row {row_num}: Invalid email format '{email}'"

    # Daily limit
    daily_limit_val = get_val("daily_limit")
    if daily_limit_val is not None:
        try:
            daily_limit = int(float(str(daily_limit_val)))
            if daily_limit < 0:
                return None, f"Row {row_num}: daily_limit cannot be negative"
        except (ValueError, TypeError):
            return None, f"Row {row_num}: Invalid daily_limit '{daily_limit_val}'"
    else:
        daily_limit = default_daily_limit

    # Monthly limit
    monthly_limit_val = get_val("monthly_limit")
    monthly_limit: Optional[int] = None
    if monthly_limit_val is not None:
        try:
            monthly_limit = int(float(str(monthly_limit_val)))
            if monthly_limit <= 0:
                monthly_limit = None
        except (ValueError, TypeError):
            return None, f"Row {row_num}: Invalid monthly_limit '{monthly_limit_val}'"

    # Priority / Order
    priority_val = get_val("order") or get_val("priority")
    priority = 1
    if priority_val is not None:
        try:
            priority = int(float(str(priority_val)))
            if priority < 1:
                priority = 1
        except (ValueError, TypeError):
            return None, f"Row {row_num}: Invalid priority/order '{priority_val}'"

    # Enabled / Status
    enabled_val = get_val("enabled")
    status_val = get_val("status")
    status = None
    enabled_flag = True

    if enabled_val is not None:
        clean_en = str(enabled_val).strip().upper()
        if clean_en in {"NO", "FALSE", "0", "N", "DISABLED"}:
            enabled_flag = False
            status = AccountStatus.DISABLED.value
        elif clean_en in {"YES", "TRUE", "1", "Y", "ACTIVE"}:
            enabled_flag = True
            status = AccountStatus.ACTIVE.value

    if status_val is not None and status is None:
        status_clean = str(status_val).strip().upper()
        if status_clean in {s.value for s in AccountStatus}:
            status = status_clean
        else:
            return None, f"Row {row_num}: Invalid status '{status_val}'"

    # Credential reference
    credential_ref = str(get_val("credential_ref") or "").strip()
    if not validate_credential_ref(credential_ref):
        return None, f"Row {row_num}: Invalid credential_ref; use an indirect reference such as KEYRING:name or ENV:NAME."

    # Notes
    notes = str(get_val("notes") or "").strip()

    return {
        "email": email,
        "daily_limit": daily_limit,
        "monthly_limit": monthly_limit,
        "priority": priority,
        "status": status,
        "enabled": enabled_flag,
        "credential_ref": credential_ref,
        "notes": notes,
    }, None


def read_accounts_from_file(filepath: str, default_daily_limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read and validate rows from CSV or XLSX file."""
    path = Path(filepath).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {filepath}")

    ext = path.suffix.lower()
    valid_records: List[Dict[str, Any]] = []
    errors: List[str] = []

    if ext == ".csv":
        with open(path, mode="r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                headers = next(reader)
            except StopIteration:
                raise ValueError("Empty CSV file.")
            col_map = validate_column_names(headers)

            for row_idx, row in enumerate(reader, start=2):
                if not any(bool(cell and str(cell).strip()) for cell in row):
                    continue
                parsed, err = parse_account_row(row, col_map, default_daily_limit, row_idx)
                if err:
                    errors.append(err)
                elif parsed:
                    valid_records.append(parsed)

    elif ext == ".xlsx":
        wb = openpyxl.load_workbook(filename=str(path), read_only=True, data_only=True)
        # Select ACCOUNTS sheet if present, else active sheet
        sheet = wb["ACCOUNTS"] if "ACCOUNTS" in wb.sheetnames else wb.active
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            first_row = next(rows_iter)
        except StopIteration:
            wb.close()
            raise ValueError("Empty Excel spreadsheet.")

        headers = [c for c in first_row] if first_row else []
        col_map = validate_column_names(headers)

        for row_idx, row in enumerate(rows_iter, start=2):
            if not row or not any(c is not None and str(c).strip() != "" for c in row):
                continue
            parsed, err = parse_account_row(list(row), col_map, default_daily_limit, row_idx)
            if err:
                errors.append(err)
            elif parsed:
                valid_records.append(parsed)
        wb.close()
    else:
        raise ValueError(f"Unsupported file format '{ext}'. Must be .csv or .xlsx")

    return valid_records, errors


def read_central_config_xlsx(filepath: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    """Read central configuration spreadsheet containing ACCOUNTS and SETTINGS sheets.

    :return: (accounts_list, settings_dict, errors)
    """
    path = Path(filepath).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Configuration spreadsheet not found: {filepath}")

    wb = openpyxl.load_workbook(filename=str(path), read_only=True, data_only=True)
    accounts_records: List[Dict[str, Any]] = []
    settings_dict: Dict[str, Any] = {}
    errors: List[str] = []

    # 1. Read SETTINGS sheet if present
    if "SETTINGS" in wb.sheetnames:
        settings_sheet = wb["SETTINGS"]
        for row in settings_sheet.iter_rows(values_only=True):
            if not row or len(row) < 2:
                continue
            k_raw, v_raw = row[0], row[1]
            if k_raw is None or v_raw is None:
                continue
            key = str(k_raw).strip().lower().replace(" ", "_").replace("-", "_")
            val = str(v_raw).strip()
            if not key or key == "setting":
                continue

            if key in {"rotation_strategy", "strategy"}:
                strat = val.lower()
                if strat in VALID_STRATEGIES:
                    settings_dict["strategy"] = strat
            elif key in {"default_daily_limit", "daily_limit"}:
                try:
                    limit_val = int(float(val))
                    if limit_val > 0:
                        settings_dict["default_daily_limit"] = limit_val
                except (ValueError, TypeError):
                    pass
            elif key in {"default_monthly_limit", "monthly_limit"}:
                try:
                    limit_val = int(float(val))
                    if limit_val > 0:
                        settings_dict["default_monthly_limit"] = limit_val
                except (ValueError, TypeError):
                    pass
            elif key in {"timezone", "tz"}:
                settings_dict["timezone"] = val
            elif key in {"auto_rotate", "auto_mode", "mode"}:
                if val.upper() in {"YES", "TRUE", "1", "AUTO"}:
                    settings_dict["mode"] = Mode.AUTO.value
                elif val.upper() in {"NO", "FALSE", "0", "MANUAL"}:
                    settings_dict["mode"] = Mode.MANUAL.value

    # 2. Read ACCOUNTS sheet
    sheet = wb["ACCOUNTS"] if "ACCOUNTS" in wb.sheetnames else wb.active
    if sheet is None:
        wb.close()
        raise ValueError("Could not find ACCOUNTS sheet in configuration spreadsheet.")
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        first_row = next(rows_iter)
    except StopIteration:
        wb.close()
        raise ValueError("Empty ACCOUNTS sheet in configuration spreadsheet.")

    headers = [str(c) if c is not None else "" for c in first_row] if first_row else []
    col_map = validate_column_names(headers)
    default_daily = settings_dict.get("default_daily_limit", get_default_daily_limit())

    for row_idx, row in enumerate(rows_iter, start=2):
        if not row or not any(c is not None and str(c).strip() != "" for c in row):
            continue
        parsed, err = parse_account_row(list(row), col_map, default_daily, row_idx)
        if err:
            errors.append(err)
        elif parsed:
            accounts_records.append(parsed)

    wb.close()
    return accounts_records, settings_dict, errors


def create_central_config_template_xlsx(filepath: str) -> None:
    """Generate a clean, professional template XLSX with ACCOUNTS and SETTINGS sheets."""
    wb = openpyxl.Workbook()

    # ACCOUNTS sheet
    ws_acc = wb.active
    if ws_acc is None:
        ws_acc = wb.create_sheet(title="ACCOUNTS")
    else:
        ws_acc.title = "ACCOUNTS"

    acc_headers = ["order", "email", "daily_limit", "monthly_limit", "enabled", "credential_ref", "notes"]
    ws_acc.append(acc_headers)

    sample_accounts = [
        [1, "flow_test01@example.com", 85, 1500, "YES", "KEYRING:flow_test01@example.com", "Primary example account"],
        [2, "flow_test02@example.com", 85, 1500, "YES", "KEYRING:flow_test02@example.com", "Secondary example account"],
        [3, "flow_test03@example.com", 85, 1500, "YES", "KEYRING:flow_test03@example.com", "Reserve example account"],
    ]
    for row in sample_accounts:
        ws_acc.append(row)

    # SETTINGS sheet
    ws_settings = wb.create_sheet(title="SETTINGS")
    ws_settings.append(["Setting", "Value", "Description"])
    settings_rows = [
        ["rotation_strategy", "sequential", "Selection strategy: sequential | random | round-robin | lowest-usage"],
        ["default_daily_limit", 85, "Default daily credit limit applied to newly registered accounts"],
        ["default_monthly_limit", 1500, "Optional default monthly credit limit"],
        ["timezone", "America/Bahia", "Timezone used for daily and monthly credit rollovers"],
        ["auto_rotate", "YES", "YES for unattended AUTO mode, NO for pinned MANUAL mode"],
    ]
    for row in settings_rows:
        ws_settings.append(row)

    target_path = Path(filepath).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(target_path))
