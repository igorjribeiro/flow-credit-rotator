"""Account importer supporting CSV and XLSX formats.

Enforces strict security policies: rejects raw credentials or secret columns,
validating input against schema requirements.
"""

import csv
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openpyxl

from flow_account.models import AccountStatus

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
    "status",
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
    normalized_headers = [str(h).strip().lower().replace(" ", "_").replace("-", "_") if h is not None else "" for h in headers]

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

    # Priority
    priority_val = get_val("priority")
    priority = 1
    if priority_val is not None:
        try:
            priority = int(float(str(priority_val)))
            if priority < 1:
                priority = 1
        except (ValueError, TypeError):
            return None, f"Row {row_num}: Invalid priority '{priority_val}'"

    # Status
    status_val = get_val("status")
    status = AccountStatus.ACTIVE.value
    if status_val is not None:
        status_clean = str(status_val).strip().upper()
        if status_clean in {s.value for s in AccountStatus}:
            status = status_clean
        else:
            return None, f"Row {row_num}: Invalid status '{status_val}'. Valid: ACTIVE, DISABLED, EXHAUSTED, NEEDS_HUMAN"

    # Credential reference
    credential_ref = str(get_val("credential_ref") or "").strip()

    # Notes
    notes = str(get_val("notes") or "").strip()

    return {
        "email": email,
        "daily_limit": daily_limit,
        "monthly_limit": monthly_limit,
        "priority": priority,
        "status": status,
        "credential_ref": credential_ref,
        "notes": notes,
    }, None


def read_accounts_from_file(filepath: str, default_daily_limit: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Read and validate rows from CSV or XLSX file.

    :return: (valid_records, error_list)
    """
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
                    continue  # skip completely blank rows
                parsed, err = parse_account_row(row, col_map, default_daily_limit, row_idx)
                if err:
                    errors.append(err)
                elif parsed:
                    valid_records.append(parsed)

    elif ext == ".xlsx":
        wb = openpyxl.load_workbook(filename=str(path), read_only=True, data_only=True)
        sheet = wb.active
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
