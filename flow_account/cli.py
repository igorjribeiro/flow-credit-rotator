"""Command-line interface for Google Flow Account Manager.

Provides CLI commands for account rotation, credit management, imports,
monitoring, and human-in-the-loop workflows.
"""

import argparse
import json
import sys
from typing import Any, List, Optional

from flow_account.manager import (
    FlowAccountManager,
    FlowAccountError,
    NoAccountsAvailableError,
    AccountNotFoundError,
    InvalidOperationError,
)
from flow_account.models import Account, AccountStatus


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
    return [
        acc.id,
        email_display,
        acc.status.value,
        daily_str,
        monthly_str,
        acc.available_credits,
        acc.priority,
        elig,
        acc.credential_ref or "-",
    ]


def build_parser() -> argparse.ArgumentParser:
    """Construct the command line argument parser."""
    parser = argparse.ArgumentParser(
        prog="flow-account",
        description="Google Flow Account Manager: Multi-account rotation, quota tracking, and credential isolation.",
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
    add_p.add_argument("--credential-ref", default="", help="Credential reference (e.g. ENV:FLOW_01)")
    add_p.add_argument("--notes", default="", help="Optional account notes")

    # set-strategy
    strat_p = subparsers.add_parser("set-strategy", help="Set rotation strategy")
    strat_p.add_argument(
        "strategy",
        choices=["priority", "round-robin", "lowest-usage"],
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
    parsed = parser.parse_args(args)

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
                print("=== GOOGLE FLOW ACCOUNT MANAGER ===")
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
                headers = ["ID", "Email", "Status", "Daily (Used/Max)", "Monthly (Used/Max)", "Avail", "Prio", "Eligible", "Credential Ref"]
                rows = [_account_row(a, is_current=(a.id == curr_id)) for a in accounts]
                print(_format_table(headers, rows))
                if curr_id:
                    print("\n* indicates current active account")
            return 0

        elif parsed.command == "current":
            curr = manager.current_account()
            if parsed.json:
                print(json.dumps(curr.to_dict() if curr else None, indent=2))
            else:
                if curr:
                    print(f"Current Account: #{curr.id} {curr.email}")
                    print(f"  Status:            {curr.status.value}")
                    print(f"  Used Today:        {curr.used_today} / {curr.daily_limit}")
                    if curr.monthly_limit:
                        print(f"  Used Month:        {curr.used_month} / {curr.monthly_limit}")
                    print(f"  Available Credits: {curr.available_credits}")
                    print(f"  Credential Ref:    {curr.credential_ref or '(none)'}")
                else:
                    print("No active/eligible account currently selected.")
            return 0

        elif parsed.command == "next":
            acc = manager.next_account()
            if parsed.json:
                print(json.dumps(acc.to_dict() if acc else None, indent=2))
            else:
                print(f"Rotated to account: #{acc.id} {acc.email} (Priority: {acc.priority}, Avail: {acc.available_credits})")
            return 0

        elif parsed.command == "mode":
            if parsed.mode:
                new_mode = manager.set_mode(parsed.mode)
                if parsed.json:
                    print(json.dumps({"mode": new_mode.value}))
                else:
                    print(f"Mode set to: {new_mode.value}")
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
            acc = manager.set_limit(parsed.account, parsed.daily, parsed.monthly)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                m_str = f", monthly_limit={acc.monthly_limit}" if acc.monthly_limit else ""
                print(f"Updated limits for #{acc.id} {acc.email}: daily_limit={acc.daily_limit}{m_str}")
            return 0

        elif parsed.command == "reset-daily":
            res = manager.reset_daily()
            if parsed.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Daily reset completed. Reset counters on {res['accounts_affected']} accounts.")
            return 0

        elif parsed.command == "reset-monthly":
            res = manager.reset_monthly()
            if parsed.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Monthly reset completed. Reset monthly counters on {res['accounts_affected']} accounts.")
            return 0

        elif parsed.command == "import":
            res = manager.import_accounts(parsed.file)
            if parsed.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Import completed:")
                print(f"  Imported (new): {res['imported']}")
                print(f"  Updated:        {res['updated']}")
                if res["errors"]:
                    print("  Errors/Warnings:")
                    for err in res["errors"]:
                        print(f"    - {err}")
            return 0

        elif parsed.command == "enable":
            acc = manager.enable(parsed.account)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                print(f"Enabled account #{acc.id} {acc.email} (Status: {acc.status.value})")
            return 0

        elif parsed.command == "disable":
            acc = manager.disable(parsed.account)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                print(f"Disabled account #{acc.id} {acc.email} (Status: {acc.status.value})")
            return 0

        elif parsed.command == "human-required":
            acc = manager.human_required(parsed.account, parsed.reason)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                print(f"Account #{acc.id} {acc.email} marked as NEEDS_HUMAN.")
                if parsed.reason:
                    print(f"  Reason: {parsed.reason}")
            return 0

        elif parsed.command == "human-cleared":
            acc = manager.human_cleared(parsed.account)
            if parsed.json:
                print(json.dumps(acc.to_dict(), indent=2))
            else:
                print(f"Account #{acc.id} {acc.email} cleared from human verification. (Status: {acc.status.value})")
            return 0

        elif parsed.command == "consume":
            acc, rec = manager.consume(parsed.amount, parsed.note)
            if parsed.json:
                print(json.dumps({"account": acc.to_dict(), "usage": rec.to_dict()}, indent=2))
            else:
                print(f"Consumed {rec.amount} credits on account #{acc.id} ({acc.email}).")
                print(f"  Used Today: {acc.used_today}/{acc.daily_limit} | Remaining Today: {acc.available_credits}")
                print(f"  Status: {acc.status.value}")
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
                print(f"Added account #{acc.id} {acc.email} (Daily Limit: {acc.daily_limit}, Priority: {acc.priority})")
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
                # Find account ID if email or id given
                target = manager.select_account(parsed.account)
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
