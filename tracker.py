#!/usr/bin/env python3
"""Simple personal finance tracker: add and list commands."""
import argparse
import sqlite3
import sys
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "fintracker.db"
SCHEMA_PATH = BASE / "schema.sql"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())  # creates tables if missing
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def parse_amount(text):
    """Convert '12.50' to 1250 (cents). Positive only, max 2 decimal places."""
    try:
        d = Decimal(text.strip().replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"Invalid amount: {text}")
    if not d.is_finite() or d <= 0 or d.as_tuple().exponent < -2:
        raise ValueError("Amount must be > 0 with at most 2 decimal places")
    return int(d * 100)


def parse_date(text):
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError(f"Date must be in YYYY-MM-DD format: {text}")


def fmt_money(cents):
    return f"{cents // 100}.{cents % 100:02d}"


def fmt_signed(cents):
    """Format cents with an explicit sign, e.g. -1250 -> '-12.50'."""
    sign = "-" if cents < 0 else "+"
    return f"{sign}{fmt_money(abs(cents))}"


def get_or_create_account(conn, name, currency):
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO accounts (name, currency) VALUES (?, ?)", (name, currency)
    )
    return cur.lastrowid


def get_or_create_category(conn, name, kind):
    row = conn.execute(
        "SELECT id, type FROM categories WHERE name = ?", (name,)
    ).fetchone()
    if row:
        if row["type"] != kind:
            sys.exit(f"Category '{name}' already exists with type {row['type']}")
        return row["id"]
    cur = conn.execute("INSERT INTO categories (name, type) VALUES (?, ?)", (name, kind))
    return cur.lastrowid


def cmd_add(args):
    amount = parse_amount(args.amount)
    day = parse_date(args.date)
    with connect() as conn:
        account_id = get_or_create_account(conn, args.account, args.currency)
        category_id = get_or_create_category(conn, args.category, args.type)
        conn.execute(
            "INSERT INTO transactions (date, amount, account_id, category_id, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (day, amount, account_id, category_id, args.note),
        )
    print(f"Added: {day} {args.type} {fmt_money(amount)} "
          f"[{args.account} / {args.category}]")


def cmd_list(args):
    query = (
        "SELECT t.id, t.date, t.amount, t.note, a.name AS account, "
        "c.name AS category, c.type AS type "
        "FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id "
        "JOIN categories c ON c.id = t.category_id WHERE 1=1"
    )
    params = []
    if args.month:
        query += " AND substr(t.date, 1, 7) = ?"
        params.append(args.month)
    if args.category:
        query += " AND c.name = ?"
        params.append(args.category)
    if args.account:
        query += " AND a.name = ?"
        params.append(args.account)
    query += " ORDER BY t.date DESC, t.id DESC LIMIT ?"
    params.append(args.limit)

    with connect() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        print("No transactions found.")
        return

    total = 0
    print(f"{'ID':>4}  {'Date':<10}  {'Amount':>10}  {'Account':<12} {'Category':<14} Note")
    for r in rows:
        signed = r["amount"] if r["type"] == "income" else -r["amount"]
        total += signed
        sign = "+" if signed > 0 else "-"
        money = f"{sign}{fmt_money(r['amount'])}"
        print(f"{r['id']:>4}  {r['date']:<10}  {money:>10}  "
              f"{r['account']:<12} {r['category']:<14} {r['note'] or ''}")
    sign = "+" if total >= 0 else "-"
    print(f"\nTotal: {sign}{fmt_money(abs(total))}")


def cmd_balance(args):
    query = (
        "SELECT a.name, a.currency, "
        "COALESCE(SUM(CASE WHEN c.type = 'income' THEN t.amount "
        "                  ELSE -t.amount END), 0) AS balance "
        "FROM accounts a "
        "LEFT JOIN transactions t ON t.account_id = a.id "
        "LEFT JOIN categories c ON c.id = t.category_id "
        "GROUP BY a.id ORDER BY a.name"
    )
    with connect() as conn:
        rows = conn.execute(query).fetchall()

    if not rows:
        print("No accounts yet.")
        return

    print(f"{'Account':<14} {'Balance':>12}  Currency")
    for r in rows:
        print(f"{r['name']:<14} {fmt_signed(r['balance']):>12}  {r['currency']}")


def cmd_report(args):
    month = args.month or date.today().strftime("%Y-%m")
    query = (
        "SELECT c.name, c.type, SUM(t.amount) AS total "
        "FROM transactions t JOIN categories c ON c.id = t.category_id "
        "WHERE substr(t.date, 1, 7) = ? "
        "GROUP BY c.id ORDER BY total DESC"
    )
    with connect() as conn:
        rows = conn.execute(query, (month,)).fetchall()

    if not rows:
        print(f"No transactions for {month}.")
        return

    income = sum(r["total"] for r in rows if r["type"] == "income")
    expenses = sum(r["total"] for r in rows if r["type"] == "expense")

    print(f"Report for {month}\n")
    print("Expenses by category:")
    for r in rows:
        if r["type"] != "expense":
            continue
        percent = r["total"] * 100 // expenses if expenses else 0
        print(f"  {r['name']:<16} {fmt_money(r['total']):>10}  {percent:>3}%")
    print(f"\nIncome:   {fmt_money(income):>10}")
    print(f"Expenses: {fmt_money(expenses):>10}")
    print(f"Net:      {fmt_signed(income - expenses):>10}")


REQUIRED_COLUMNS = {"date", "amount", "account", "category"}


def cmd_delete(args):
    with connect() as conn:
        row = conn.execute(
            "SELECT t.id, t.date, t.amount, t.note, a.name AS account, "
            "c.name AS category, c.type AS type "
            "FROM transactions t "
            "JOIN accounts a ON a.id = t.account_id "
            "JOIN categories c ON c.id = t.category_id "
            "WHERE t.id = ?",
            (args.id,),
        ).fetchone()
        if not row:
            sys.exit(f"Transaction {args.id} not found")

        print(f"{row['id']}: {row['date']} {row['type']} {fmt_money(row['amount'])} "
              f"[{row['account']} / {row['category']}] {row['note'] or ''}")
        if not args.yes:
            answer = input("Delete this transaction? [y/N] ").strip().lower()
            if answer != "y":
                print("Cancelled.")
                return
        conn.execute("DELETE FROM transactions WHERE id = ?", (args.id,))
    print("Deleted.")


def cmd_import(args):
    path = Path(args.file)
    if not path.is_file():
        sys.exit(f"File not found: {path}")

    records, errors = [], []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            sys.exit(f"Missing columns: {', '.join(sorted(missing))}")
        for line_no, rec in enumerate(reader, start=2):  # line 1 is the header
            try:
                kind = (rec.get("type") or "expense").strip().lower()
                if kind not in ("income", "expense"):
                    raise ValueError(f"Invalid type: {kind}")
                account = (rec["account"] or "").strip()
                category = (rec["category"] or "").strip()
                if not account or not category:
                    raise ValueError("Account and category are required")
                records.append({
                    "date": parse_date(rec["date"] or ""),
                    "amount": parse_amount(rec["amount"] or ""),
                    "account": account,
                    "category": category,
                    "type": kind,
                    "currency": (rec.get("currency") or "USD").strip(),
                    "note": (rec.get("note") or "").strip() or None,
                })
            except ValueError as e:
                errors.append(f"line {line_no}: {e}")

    if errors:
        print("Import aborted, nothing was saved. Fix these rows:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    if args.dry_run:
        print(f"Dry run OK: {len(records)} transactions would be imported.")
        return

    with connect() as conn:  # one transaction: all rows or none
        for r in records:
            account_id = get_or_create_account(conn, r["account"], r["currency"])
            category_id = get_or_create_category(conn, r["category"], r["type"])
            conn.execute(
                "INSERT INTO transactions (date, amount, account_id, category_id, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (r["date"], r["amount"], account_id, category_id, r["note"]),
            )
    print(f"Imported {len(records)} transactions from {path.name}.")


def main():
    parser = argparse.ArgumentParser(description="Personal finance tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add a transaction")
    p_add.add_argument("amount", help="amount, e.g. 12.50")
    p_add.add_argument("-a", "--account", required=True, help="account, e.g. Cash")
    p_add.add_argument("-c", "--category", required=True, help="category, e.g. Food")
    p_add.add_argument("-t", "--type", choices=["income", "expense"], default="expense")
    p_add.add_argument("-d", "--date", default=date.today().isoformat(),
                       help="YYYY-MM-DD (default: today)")
    p_add.add_argument("-n", "--note", default=None)
    p_add.add_argument("--currency", default="USD", help="currency for a new account")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="list transactions")
    p_list.add_argument("-m", "--month", help="filter by month, e.g. 2026-11")
    p_list.add_argument("-c", "--category")
    p_list.add_argument("-a", "--account")
    p_list.add_argument("-l", "--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)


    p_balance = sub.add_parser("balance", help="show balance per account")
    p_balance.set_defaults(func=cmd_balance)

    p_report = sub.add_parser("report", help="monthly report by category")
    p_report.add_argument("-m", "--month", help="YYYY-MM (default: current month)")
    p_report.set_defaults(func=cmd_report)


    p_delete = sub.add_parser("delete", help="delete a transaction by ID")
    p_delete.add_argument("id", type=int)
    p_delete.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    p_delete.set_defaults(func=cmd_delete)

    p_import = sub.add_parser("import", help="import transactions from a CSV file")
    p_import.add_argument("file")
    p_import.add_argument("--dry-run", action="store_true",
                          help="validate the file without saving anything")
    p_import.set_defaults(func=cmd_import)


    args = parser.parse_args()
    try:
        args.func(args)
    except ValueError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
