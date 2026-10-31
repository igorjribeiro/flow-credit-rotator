"""Command-line interface for Flow Credit Rotator.

Provides CLI commands for account rotation, credit management, pre-checks,
reservations, central XLSX synchronization, credential isolation, and monitoring.
"""

import argparse
import getpass
import json
import sys
from typing import Any, List, Optional

from flow_account.credentials import has_credential, set_credential
from flow_account.manager import (
    AccountNotFoundError,
    FlowAccountError,
    FlowAccountManager,
    InvalidOperationError,
    NoAccountsAvailableError,
)
from flow_account.models import Account, AccountStatus, validate_credential_ref


def _format_table(headers: List[str], rows: List[List[Any]]) -> str:
    """Format tabular data into an aligned ASCII text table."""
    if not rows:
        return "No records found."
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))

    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "  ".join("-" * col_widths[i] for i in range(len(headers)))
    row_lines = [
        "  ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row))
        for row in rows
    ]
    return f"{header_line}\n{sep_line}\n" + "\n".join(row_lines)


def _account_row(acc: Account, is_current: bool = False) -> List[Any]:
    """Helper to convert Account to a display row."""
    cur_flag = "* " if is_current else "  "
    email_display = f"{cur_flag}{acc.email}"
    daily_str = f"{acc.used_today}/{acc.daily_limit}"
    monthly_str = f"{acc.used_month}/{acc.monthly_limit}" if acc.monthly_limit else f"{acc.used_month}/-"
    elig = "YES" if acc.is_eligible else "NO"
    cred_ref = acc.credential_ref
    if not validate_credential_ref(cred_ref):
        cred_ref = "[INVALID CREDENTIAL REF]"
    return [
        acc.id,
        email_display,
        acc.status.value,
        daily_str,
        monthly_str,
        acc.available_credits,
        acc.priority,
        elig,
        cred_ref or "-",
    ]


def build_parser() -> argparse.ArgumentParser:
    """Construct the command line argument parser."""
    parser = argparse.ArgumentParser(
        prog="flow-account",
        description="Flow Credit Rotator: Multi-account rotation, quota tracking, pre-checks, and credential safety.",
    )
    parser.add_argument("--db", help="Path to SQLite database file override", default=None)
    parser.add_argument("--json", action="store_true", help="Output responses as JSON")

    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # status
    subparsers.add_parser("status", help="Show system status and account statistics")

    # list
    subparsers.add_parser("list", help="List all accounts with quota and eligibility")

    # current
    subparsers.add_parser("current", help="Display the currently active account")

    # next
    subparsers.add_parser("next", help="Rotate to the next eligible account")

    # can-consume
    can_p = subparsers.add_parser("can-consume", help="Pre-check capacity for an operation without reserving")
    can_p.add_argument("amount", type=int, help="Credit amount to check")

    # reserve
    res_p = subparsers.add_parser("reserve", help="Pre-check capacity and create a credit reservation")
    res_p.add_argument("amount", type=int, help="Estimated credit cost to reserve")
    res_p.add_argument("--note", default="", help="Optional audit note or generation job ID")
    res_p.add_argument("--ttl", type=int, default=1800, help="Reservation TTL in seconds (default: 1800)")

    # reserve-confirm / commit
    rc_p = subparsers.add_parser("reserve-confirm", aliases=["commit"], help="Confirm reservation and deduct actual credits")
    rc_p.add_argument("reservation_id", help="Reservation ID (e.g. res_...)")
    rc_p.add_argument("--amount", type=int, default=None, help="Actual credits consumed (defaults to reserved amount)")
    rc_p.add_argument("--note", default="", help="Optional audit note")

    # reserve-cancel / cancel
    rx_p = subparsers.add_parser("reserve-cancel", aliases=["cancel"], help="Cancel a pending reservation without deducting credits")
    rx_p.add_argument("reservation_id", help="Reservation ID")
    rx_p.add_argument("--reason", default="", help="Optional cancellation reason")

    # reservations
    r_list_p = subparsers.add_parser("reservations", help="List recent credit reservations")
    r_list_p.add_argument("--status", choices=["PENDING", "COMMITTED", "CANCELLED", "EXPIRED", "pending", "committed", "cancelled", "expired"], default=None, help="Filter by status")
    r_list_p.add_argument("--limit", type=int, default=20, help="Max records to display")

    # sync
    sync_p = subparsers.add_parser("sync", help="Synchronize accounts and settings from central XLSX spreadsheet")
    sync_p.add_argument("file", nargs="?", default="config/flow_accounts.xlsx", help="Path to config XLSX (default: config/flow_accounts.xlsx)")

    # credential
    cred_p = subparsers.add_parser("credential", help="Manage secure credentials for accounts")
    cred_subs = cred_p.add_subparsers(dest="cred_action", help="Credential action (set, test)")
    cset_p = cred_subs.add_parser("set", help="Securely set a credential in keyring/vault")
    cset_p.add_argument("account", help="Account email or reference key")
    ctest_p = cred_subs.add_parser("test", help="Test if a credential exists")
    ctest_p.add_argument("account", help="Account email or reference key")

    # mode
    mode_p = subparsers.add_parser("mode", help="Get or set rotation mode (AUTO or MANUAL)")
    mode_p.add_argument("mode", nargs="?", choices=["auto", "manual", "AUTO", "MANUAL"], help="Mode to set")

    # select
    sel_p = subparsers.add_parser("select", help="Manually select an active account by ID or email")
    sel_p.add_argument("account", help="Account ID or email")

    # set-default-limit
    sdl_p = subparsers.add_parser("set-default-limit", help="Set system default daily limit")
    sdl_p.add_argument("limit", type=int, help="Default daily credit limit (e.g. 85)")

    # set-limit
    sl_p = subparsers.add_parser("set-limit", help="Set limits for an account")
    sl_p.add_argument("account", help="Account ID or email")
    sl_p.add_argument("daily", type=int, help="Daily credit limit")
    sl_p.add_argument("--monthly", type=int, default=None, help="Optional monthly credit limit")

    # reset-daily
    subparsers.add_parser("reset-daily", help="Manually trigger daily quota reset")

    # reset-monthly
    subparsers.add_parser("reset-monthly", help="Manually trigger monthly quota reset")

    # import
    imp_p = subparsers.add_parser("import", help="Import accounts from CSV or XLSX")
    imp_p.add_argument("file", help="Path to .csv or .xlsx spreadsheet")

    # enable
    en_p = subparsers.add_parser("enable", help="Enable an account")
    en_p.add_argument("account", help="Account ID or email")

    # disable
    dis_p = subparsers.add_parser("disable", help="Disable an account")
    dis_p.add_argument("account", help="Account ID or email")

    # human-required
    hr_p = subparsers.add_parser("human-required", help="Mark account as requiring human verification")
    hr_p.add_argument("account", help="Account ID or email")
    hr_p.add_argument("reason", nargs="?", default="", help="Optional verification reason (e.g. CAPTCHA, SMS)")

    # human-cleared
    hc_p = subparsers.add_parser("human-cleared", help="Clear human verification flag from account")
    hc_p.add_argument("account", help="Account ID or email")

    # consume
    con_p = subparsers.add_parser("consume", help="Consume credits from current account")
    con_p.add_argument("amount", type=int, help="Number of credits to consume")
    con_p.add_argument("--note", default="", help="Optional audit note or job ID")

    # add
    add_p = subparsers.add_parser("add", help="Add a new Google Flow account")
    add_p.add_argument("email", help="Account email address")
    add_p.add_argument("--daily", type=int, default=None, help="Daily credit limit")
    add_p.add_argument("--monthly", type=int, default=None, help="Monthly credit limit")
    add_p.add_argument("--priority", type=int, default=1, help="Priority rank (1=highest)")
    add_p.add_argument("--credential-ref", default="", help="Credential reference (e.g. KEYRING:flow01, ENV:FLOW_01)")
    add_p.add_argument("--notes", default="", help="Optional account notes")

    # strategy
    strat_cmd = subparsers.add_parser("strategy", help="Get or set account selection strategy")
    strat_cmd.add_argument(
        "strategy",
        nargs="?",
        choices=["sequential", "priority", "round-robin", "lowest-usage", "random"],
        help="Strategy algorithm (sequential, priority, round-robin, lowest-usage, random)",
    )

    # set-strategy
    strat_p = subparsers.add_parser("set-strategy", help="Set rotation strategy")
    strat_p.add_argument(
        "strategy",
        choices=["sequential", "priority", "round-robin", "lowest-usage", "random"],
        help="Strategy algorithm",
    )

    # history
    hist_p = subparsers.add_parser("history", help="Show credit usage audit history")
    hist_p.add_argument("--limit", type=int, default=20, help="Max records to display (default: 20)")
    hist_p.add_argument("--account", default=None, help="Optional filter by account ID or email")

    return parser


def main(args: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    try:
        parsed = parser.parse_args(args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    if not parsed.command:
        parser.print_help()
        return 0

    try:
        manager = FlowAccountManager(db_path=parsed.db)

        if parsed.command == "status":
            st = manager.status()
            if parsed.json:
                print(json.dumps(st, indent=2))
            else:
                sys_info = st["system"]
                curr = st["current_account"]
                summary = st["summary"]
                print("=== FLOW CREDIT ROTATOR ===")
                print(f"Mode:               {sys_info['mode']}")
                print(f"Strategy:           {sys_info['strategy']}")
                print(f"Timezone:           {sys_info['timezone']}")
                print(f"Default Daily Limit:{sys_info['default_daily_limit']}")
                print(f"Last Daily Reset:   {sys_info['last_reset_date'] or 'Never'}")
                print(f"Last Monthly Reset: {sys_info['last_reset_month'] or 'Never'}")
                if curr:
                    print(f"Current Account:    #{curr['id']} {curr['email']} (Status: {curr['status']}, Credits: {curr['available_credits']})")
                else:
                    print("Current Account:    None (No eligible accounts available)")
                print("\n=== SUMMARY ===")
                print(f"Total Accounts:     {summary['total_accounts']}")
                print(f"Active:             {summary['active_accounts']}")
                print(f"Eligible:           {summary['eligible_accounts']}")
                print(f"Exhausted:          {summary['exhausted_accounts']}")
                print(f"Needs Human:        {summary['needs_human_accounts']}")
                print(f"Disabled:           {summary['disabled_accounts']}")
                print(f"Used Today:         {summary['total_used_today']}")
                print(f"Total Available:    {summary['total_available_credits']}")
            return 0

        elif parsed.command == "list":
            accounts = manager.list_accounts()
            status_info = manager.status()
            curr_id = status_info["system"].get("current_account_id")
            if parsed.json:
                print(json.dumps([a.to_dict() for a in accounts], indent=2))
            else:
                headers = ["ID", "Email", "Status", "Daily (Used/Limit)", "Monthly (Used/Limit)", "Avail", "Pri", "Elig", "Credential Ref"]
                rows = [_account_row(a, is_current=(a.id == curr_id)) for a in accounts]
                print(_format_table(headers, rows))
            return 0

        elif parsed.command == "current":
            acc = manager.current_account()
            if acc:
                if parsed.json:
                    print(json.dumps(acc.to_dict(), indent=2))
                else:
                    print(f"Current active account: #{acc.id} {acc.email}")
                    print(f"Status:            {acc.status.value}")
                    print(f"Available credits: {acc.available_credits}")
                    print(f"Daily usage:       {acc.used_today}/{acc.daily_limit}")
                    monthly_str = f"{acc.used_month}/{acc.monthly_limit}" if acc.monthly_limit else f"{acc.used_month}/-"
                    print(f"Monthly usage:     {monthly_str}")
                    print(f"Priority:          {acc.priority}")
                    cred_display = acc.credential_ref if validate_credential_ref(acc.credential_ref) else "[INVALID]"
                    print(f"Credential Ref:    {cred_display or 'None'}")
            else:
                if parsed.json:
                    print(json.dumps(None))
                else:
                    print("No active or eligible account is currently selected.")
            return 0

        elif parsed.command == "next":
            acc = manager.next_account()
            if acc:
                if parsed.json:
                    print(json.dumps(acc.to_dict(), indent=2))
                else:
                    print(f"Advanced to next eligible account: #{acc.id} {acc.email} (Available: {acc.available_credits})")
            else:
                if parsed.json:
                    print(json.dumps(None))
                else:
                    print("No other eligible accounts available to advance to.")
            return 0

        elif parsed.command == "can-consume":
            can_exec, acc, msg = manager.can_consume(parsed.amount)
            if parsed.json:
                print(json.dumps({
                    "can_consume": can_exec,
                    "account": acc.to_dict() if acc else None,
                    "message": msg,
                }, indent=2))
            else:
                status_str = "✓ APPROVED" if can_exec else "✗ REJECTED"
                print(f"{status_str}: {msg}")
                if acc:
                    print(f"Target Account: #{acc.id} {acc.email} (Available credits: {acc.available_credits})")
            return 0 if can_exec else 1

        elif parsed.command == "reserve":
            acc, res = manager.reserve(parsed.amount, note=parsed.note, ttl_seconds=parsed.ttl)
            if parsed.json:
                print(json.dumps({
                    "reservation": res.to_dict(),
                    "account": acc.to_dict(),
                }, indent=2))
            else:
                print("✓ Credit Reservation Created")
                print(f"Reservation ID:   {res.id}")
                print(f"Authorized Email: {acc.email} (#{acc.id})")
                print(f"Reserved Credits: {res.estimated_cost}")
                print(f"Expires At:       {res.expires_at}")
                if res.note:
                    print(f"Note:             {res.note}")
            return 0

        elif parsed.command in ("reserve-confirm", "commit"):
            acc, usage = manager.confirm_reservation(parsed.reservation_id, actual_amount=parsed.amount, note=parsed.note)
            if parsed.json:
                print(json.dumps({
                    "account": acc.to_dict(),
                    "usage_record": usage.to_dict(),
                }, indent=2))
            else:
                print(f"✓ Reservation '{parsed.reservation_id}' Confirmed")
                print(f"Account:         {acc.email} (#{acc.id})")
                print(f"Charged Credits: {usage.amount}")
                print(f"Remaining Today: {acc.available_credits}")
                print(f"Account Status:  {acc.status.value}")
            return 0

        elif parsed.command in ("reserve-cancel", "cancel"):
            res = manager.cancel_reservation(parsed.reservation_id, reason=parsed.reason)
            if parsed.json:
                print(json.dumps(res.to_dict(), indent=2))
            else:
                print(f"✓ Reservation '{res.id}' Cancelled")
                print("Zero credits were charged. Reserved capacity has been released.")
            return 0

        elif parsed.command == "reservations":
            res_list = manager.list_reservations(status=parsed.status, limit=parsed.limit)
            if parsed.json:
                print(json.dumps([r.to_dict() for r in res_list], indent=2))
            else:
                headers = ["ID", "Account", "Cost", "Status", "Created At", "Expires At", "Note"]
                rows = [
                    [r.id, r.account_email or f"#{r.account_id}", r.estimated_cost, r.status.value, r.created_at[:19], r.expires_at[:19], r.note or "-"]
                    for r in res_list
                ]
                print(_format_table(headers, rows))
            return 0

        elif parsed.command == "sync":
            res = manager.sync(parsed.file)
            if parsed.json:
                print(json.dumps(res, indent=2))
            else:
                print("=== FLOW ACCOUNTS SYNCHRONIZATION ===")
                print(f"Configuration file: {parsed.file}")
                print(f"Accounts added:     {res['added']}")
                print(f"Accounts updated:   {res['updated']}")
                print(f"Accounts disabled:  {res['disabled']}")
                print(f"Total in sheet:     {res['total_in_sheet']}")
                if res.get("settings"):
                    print("Updated settings:")
                    for k, v in res["settings"].items():
                        print(f"  - {k}: {v}")
                if res.get("errors"):
                    print("Warnings / Errors:")
                    for e in res["errors"]:
                        print(f"  ! {e}")
            return 0

        elif parsed.command == "credential":
            if not parsed.cred_action:
                print("Use 'flow-account credential set <ref>' or 'flow-account credential test <ref>'.", file=sys.stderr)
                return 1

            if parsed.cred_action == "set":
                # Prompt securely without echo
                pwd = getpass.getpass(f"Enter secret/credential for '{parsed.account}' (input hidden): ")
                if not pwd:
                    print("Error: Empty secret provided. Aborted.", file=sys.stderr)
                    return 1
                ref_str = set_credential(parsed.account, pwd)
                if parsed.json:
                    print(json.dumps({"status": "stored", "credential_ref": ref_str}))
                else:
                    print(f"✓ Credential securely stored under reference '{ref_str}'")
                    print("Plaintext password will never appear in SQLite, XLSX spreadsheets, or logs.")
                return 0

            elif parsed.cred_action == "test":
                exists = has_credential(parsed.account)
                if parsed.json:
                    print(json.dumps({"account": parsed.account, "exists": exists}))
                else:
                    if exists:
                        print(f"✓ Credential for '{parsed.account}' is configured and accessible.")
                    else:
                        print(f"✗ Credential for '{parsed.account}' was NOT found.")
                return 0 if exists else 1

        elif parsed.command == "mode":
            if parsed.mode:
                new_mode = manager.set_mode(parsed.mode)
                if parsed.json:
                    print(json.dumps({"mode": new_mode.value}))
                else:
                    print(f"Operation mode updated to: {new_mode.value}")
            else:
                st = manager.status()
                curr_mode = st["system"]["mode"]
                if parsed.json:
                    print(json.dumps({"mode": curr_mode}))
                else:
                    print(f"Current mode: {curr_mode}")
            return 0

        elif parsed.command == "select":
            acc = manager.select_account(parsed.account)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                print(f"Selected account: #{acc.id} {acc.email}")
            return 0

        elif parsed.command == "set-default-limit":
            val = manager.set_default_limit(parsed.limit)
            if parsed.json:
                print(json.dumps({"default_daily_limit": val}))
            else:
                print(f"System default daily limit set to: {val}")
            return 0

        elif parsed.command == "set-limit":
            acc = manager.set_limit(parsed.account, daily_limit=parsed.daily, monthly_limit=parsed.monthly)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                monthly_disp = acc.monthly_limit if acc.monthly_limit else "None"
                print(f"Updated limits for #{acc.id} {acc.email}: Daily={acc.daily_limit}, Monthly={monthly_disp}")
            return 0

        elif parsed.command == "reset-daily":
            res = manager.reset_daily()
            if parsed.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"✓ Daily reset executed. Accounts reset: {res['accounts_reset']}")
            return 0

        elif parsed.command == "reset-monthly":
            res = manager.reset_monthly()
            if parsed.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"✓ Monthly reset executed. Accounts reset: {res['accounts_reset']}")
            return 0

        elif parsed.command == "import":
            res = manager.import_accounts(parsed.file)
            if parsed.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Import results for {parsed.file}:")
                print(f"  Imported (new): {res['imported']}")
                print(f"  Updated:        {res['updated']}")
                print(f"  Skipped/Empty:  {res['skipped']}")
                if res["errors"]:
                    print(f"  Errors encountered ({len(res['errors'])}):")
                    for err in res["errors"]:
                        print(f"    - {err}")
            return 0

        elif parsed.command == "enable":
            acc = manager.enable(parsed.account)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                print(f"Account #{acc.id} {acc.email} enabled (Status: {acc.status.value})")
            return 0

        elif parsed.command == "disable":
            acc = manager.disable(parsed.account)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                print(f"Account #{acc.id} {acc.email} disabled (Status: {acc.status.value})")
            return 0

        elif parsed.command == "human-required":
            acc = manager.human_required(parsed.account, reason=parsed.reason)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                print(f"Account #{acc.id} {acc.email} flagged as NEEDS_HUMAN.")
                if parsed.reason:
                    print(f"Reason: {parsed.reason}")
            return 0

        elif parsed.command == "human-cleared":
            acc = manager.human_cleared(parsed.account)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                print(f"Human verification flag cleared for #{acc.id} {acc.email} (Status: {acc.status.value})")
            return 0

        elif parsed.command == "consume":
            acc, usage = manager.consume(parsed.amount, note=parsed.note)
            if parsed.json:
                print(json.dumps({
                    "account": acc.to_dict(),
                    "usage_record": usage.to_dict(),
                }, indent=2))
            else:
                print(f"Consumed {parsed.amount} credits from #{acc.id} {acc.email}")
                print(f"Remaining credits today: {acc.available_credits}")
                print(f"Daily usage:             {acc.used_today}/{acc.daily_limit}")
                print(f"Account status:          {acc.status.value}")
            return 0

        elif parsed.command == "add":
            acc = manager.add_account(
                email=parsed.email,
                daily_limit=parsed.daily,
                monthly_limit=parsed.monthly,
                priority=parsed.priority,
                credential_ref=parsed.credential_ref,
                notes=parsed.notes,
            )
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                print(f"Account created: #{acc.id} {acc.email} (Daily: {acc.daily_limit}, Priority: {acc.priority})")
            return 0

        elif parsed.command == "strategy":
            if parsed.strategy:
                new_strat = manager.set_strategy(parsed.strategy)
                if parsed.json:
                    print(json.dumps({"strategy": new_strat.value}))
                else:
                    print(f"Rotation strategy set to: {new_strat.value}")
            else:
                st = manager.status()
                curr_strat = st["system"]["strategy"]
                if parsed.json:
                    print(json.dumps({"strategy": curr_strat}))
                else:
                    print(f"Current strategy: {curr_strat}")
            return 0

        elif parsed.command == "set-strategy":
            new_strat = manager.set_strategy(parsed.strategy)
            if parsed.json:
                print(json.dumps({"strategy": new_strat.value}))
            else:
                print(f"Rotation strategy set to: {new_strat.value}")
            return 0

        elif parsed.command == "history":
            acc_id = None
            if parsed.account:
                target = manager.get_account(parsed.account)
                if not target:
                    raise AccountNotFoundError(f"Account '{parsed.account}' not found.")
                acc_id = target.id
            records = manager.history(limit=parsed.limit, account_id=acc_id)
            if parsed.json:
                print(json.dumps([r.to_dict() for r in records], indent=2))
            else:
                headers = ["ID", "Account ID", "Amount", "Used Today", "Used Month", "Mode", "Timestamp", "Note"]
                rows = [
                    [r.id, r.account_id, r.amount, r.used_today_after, r.used_month_after, r.mode, r.timestamp[:19], r.note or "-"]
                    for r in records
                ]
                print(_format_table(headers, rows))
            return 0

    except FlowAccountError as e:
        if parsed.json:
            print(json.dumps({"error": type(e).__name__, "message": str(e)}), file=sys.stderr)
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        if parsed.json:
            print(json.dumps({"error": "ValueError", "message": str(e)}), file=sys.stderr)
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        if parsed.json:
            print(json.dumps({"error": type(e).__name__, "message": str(e)}), file=sys.stderr)
        else:
            print(f"Unexpected error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
