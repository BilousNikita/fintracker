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
        warning = (budget_warning(conn, category_id, day[:7], args.category)
                   if args.type == "expense" else None)
    print(f"Added: {day} {args.type} {fmt_money(amount)} "
          f"[{args.account} / {args.category}]")
    if warning:
        print(warning)


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


def existing_counts(conn):
    """Count stored transactions by (date, amount, account, category, note)."""
    rows = conn.execute(
        "SELECT t.date, t.amount, a.name, c.name, COALESCE(t.note, ''), COUNT(*) "
        "FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id "
        "JOIN categories c ON c.id = t.category_id "
        "GROUP BY t.date, t.amount, a.name, c.name, COALESCE(t.note, '')"
    ).fetchall()
    return {tuple(r)[:5]: r[5] for r in rows}


def split_duplicates(records, existing):
    """Split records into (new, skipped).

    Identical rows are matched one-to-one with what is already stored, so
    legitimate repeats (two identical coffees in a day) are kept.
    """
    remaining = dict(existing)
    new, skipped = [], []
    for r in records:
        key = (r["date"], r["amount"], r["account"], r["category"], r["note"] or "")
        if remaining.get(key, 0) > 0:
            remaining[key] -= 1
            skipped.append(r)
        else:
            new.append(r)
    return new, skipped


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

    with connect() as conn:  # one transaction: all rows or none
        if args.allow_duplicates:
            new, skipped = records, []
        else:
            new, skipped = split_duplicates(records, existing_counts(conn))

        if args.dry_run:
            print(f"Dry run OK: {len(new)} transactions would be imported.")
            if skipped:
                print(f"{len(skipped)} duplicates would be skipped.")
            return

        for r in new:
            account_id = get_or_create_account(conn, r["account"], r["currency"])
            category_id = get_or_create_category(conn, r["category"], r["type"])
            conn.execute(
                "INSERT INTO transactions (date, amount, account_id, category_id, note) "
                "VALUES (?, ?, ?, ?, ?)",
                (r["date"], r["amount"], account_id, category_id, r["note"]),
            )
    print(f"Imported {len(new)} transactions from {path.name}.")
    if skipped:
        print(f"Skipped {len(skipped)} duplicates "
              f"(use --allow-duplicates to import them anyway).")


def cmd_export(args):
    query = (
        "SELECT t.date, t.amount, a.name AS account, c.name AS category, "
        "c.type AS type, a.currency AS currency, t.note AS note "
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
    query += " ORDER BY t.date, t.id"

    with connect() as conn:
        rows = conn.execute(query, params).fetchall()

    def write(f):
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["date", "amount", "account", "category",
                         "type", "currency", "note"])
        for r in rows:
            writer.writerow([r["date"], fmt_money(r["amount"]), r["account"],
                             r["category"], r["type"], r["currency"], r["note"] or ""])

    if args.file == "-":
        write(sys.stdout)
        return
    path = Path(args.file)
    if path.exists() and not args.force:
        sys.exit(f"{path} already exists (use --force to overwrite)")
    with path.open("w", newline="", encoding="utf-8") as f:
        write(f)
    print(f"Exported {len(rows)} transactions to {path}")


WARN_PERCENT = 80


def month_spent(conn, category_id, month):
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transactions "
        "WHERE category_id = ? AND substr(date, 1, 7) = ?",
        (category_id, month),
    ).fetchone()
    return row[0]


def budget_state(spent, limit):
    if spent > limit:
        return "OVER"
    if spent * 100 >= limit * WARN_PERCENT:
        return "WARNING"
    return "OK"


def budget_warning(conn, category_id, month, name):
    """Return a warning string if the category budget is nearly used up."""
    row = conn.execute(
        "SELECT monthly_limit FROM budgets WHERE category_id = ?", (category_id,)
    ).fetchone()
    if not row:
        return None
    limit = row["monthly_limit"]
    spent = month_spent(conn, category_id, month)
    state = budget_state(spent, limit)
    if state == "OK":
        return None
    verb = "exceeded" if state == "OVER" else f"at {spent * 100 // limit}%"
    return (f"Warning: {name} budget {verb}: "
            f"{fmt_money(spent)} of {fmt_money(limit)} for {month}")


def cmd_budget_set(args):
    limit = parse_amount(args.amount)
    with connect() as conn:
        row = conn.execute(
            "SELECT id, type FROM categories WHERE name = ?", (args.category,)
        ).fetchone()
        if not row:
            sys.exit(f"Category '{args.category}' not found")
        if row["type"] != "expense":
            sys.exit("Budgets can only be set for expense categories")
        conn.execute(
            "INSERT INTO budgets (category_id, monthly_limit) VALUES (?, ?) "
            "ON CONFLICT(category_id) DO UPDATE SET monthly_limit = excluded.monthly_limit",
            (row["id"], limit),
        )
    print(f"Budget for {args.category}: {fmt_money(limit)} per month")


def cmd_budget_status(args):
    month = args.month or date.today().strftime("%Y-%m")
    with connect() as conn:
        budgets = conn.execute(
            "SELECT c.id, c.name, b.monthly_limit FROM budgets b "
            "JOIN categories c ON c.id = b.category_id ORDER BY c.name"
        ).fetchall()
        if not budgets:
            print("No budgets set. Use: budget set CATEGORY AMOUNT")
            return
        print(f"Budgets for {month}\n")
        print(f"{'Category':<16} {'Limit':>10} {'Spent':>10} {'Left':>11} {'Used':>5}  Status")
        for b in budgets:
            limit = b["monthly_limit"]
            spent = month_spent(conn, b["id"], month)
            left = limit - spent
            left_text = fmt_money(left) if left >= 0 else "-" + fmt_money(-left)
            percent = spent * 100 // limit
            print(f"{b['name']:<16} {fmt_money(limit):>10} {fmt_money(spent):>10} "
                  f"{left_text:>11} {percent:>4}%  {budget_state(spent, limit)}")


def cmd_budget_remove(args):
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM budgets WHERE category_id = "
            "(SELECT id FROM categories WHERE name = ?)",
            (args.category,),
        )
    if cur.rowcount == 0:
        sys.exit(f"No budget set for '{args.category}'")
    print(f"Budget for {args.category} removed")


def cmd_edit(args):
    fields = (args.amount, args.date, args.account, args.category, args.note, args.type)
    if all(f is None for f in fields):
        sys.exit("Nothing to change: pass at least one of "
                 "--amount, --date, --account, --category, --note")
    if args.type and args.category is None:
        sys.exit("--type can only be used together with --category")
    for label, value in (("account", args.account), ("category", args.category)):
        if value is not None and not value.strip():
            sys.exit(f"The {label} name cannot be empty")

    with connect() as conn:
        cur = conn.execute(
            "SELECT t.id, t.date, t.amount, t.note, t.account_id, t.category_id, "
            "c.type AS type FROM transactions t "
            "JOIN categories c ON c.id = t.category_id WHERE t.id = ?",
            (args.id,),
        ).fetchone()
        if not cur:
            sys.exit(f"Transaction {args.id} not found")

        amount = parse_amount(args.amount) if args.amount is not None else cur["amount"]
        day = parse_date(args.date) if args.date is not None else cur["date"]
        note = (args.note.strip() or None) if args.note is not None else cur["note"]

        account_id = cur["account_id"]
        if args.account is not None:
            account_id = get_or_create_account(conn, args.account.strip(), args.currency)

        category_id = cur["category_id"]
        if args.category is not None:
            name = args.category.strip()
            row = conn.execute(
                "SELECT id, type FROM categories WHERE name = ?", (name,)
            ).fetchone()
            if row:
                if args.type and row["type"] != args.type:
                    sys.exit(f"Category '{name}' already exists with type {row['type']}")
                category_id = row["id"]
            else:  # a new category inherits the current type unless --type is given
                category_id = get_or_create_category(conn, name, args.type or cur["type"])

        conn.execute(
            "UPDATE transactions SET date = ?, amount = ?, account_id = ?, "
            "category_id = ?, note = ? WHERE id = ?",
            (day, amount, account_id, category_id, note, args.id),
        )
        new = conn.execute(
            "SELECT a.name AS account, c.name AS category, c.type AS type "
            "FROM accounts a, categories c WHERE a.id = ? AND c.id = ?",
            (account_id, category_id),
        ).fetchone()
        warning = (budget_warning(conn, category_id, day[:7], new["category"])
                   if new["type"] == "expense" else None)

    print(f"Updated {args.id}: {day} {new['type']} {fmt_money(amount)} "
          f"[{new['account']} / {new['category']}] {note or ''}".rstrip())
    if warning:
        print(warning)


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
    p_import.add_argument("--allow-duplicates", action="store_true",
                          help="import rows even if identical ones already exist")
    p_import.set_defaults(func=cmd_import)


    p_export = sub.add_parser("export", help="export transactions to a CSV file")
    p_export.add_argument("file", help="output path, or - for stdout")
    p_export.add_argument("-m", "--month", help="filter by month, e.g. 2026-11")
    p_export.add_argument("-c", "--category")
    p_export.add_argument("-a", "--account")
    p_export.add_argument("--force", action="store_true",
                          help="overwrite an existing file")
    p_export.set_defaults(func=cmd_export)

    p_budget = sub.add_parser("budget", help="manage monthly category budgets")
    budget_sub = p_budget.add_subparsers(dest="budget_command", required=True)

    p_bset = budget_sub.add_parser("set", help="set a monthly limit for an expense category")
    p_bset.add_argument("category")
    p_bset.add_argument("amount", help="monthly limit, e.g. 300")
    p_bset.set_defaults(func=cmd_budget_set)

    p_bstatus = budget_sub.add_parser("status", help="show spending against budgets")
    p_bstatus.add_argument("-m", "--month", help="YYYY-MM (default: current month)")
    p_bstatus.set_defaults(func=cmd_budget_status)

    p_bremove = budget_sub.add_parser("remove", help="remove a budget")
    p_bremove.add_argument("category")
    p_bremove.set_defaults(func=cmd_budget_remove)

    p_edit = sub.add_parser("edit", help="change fields of an existing transaction")
    p_edit.add_argument("id", type=int)
    p_edit.add_argument("--amount", help="new amount, e.g. 15.00")
    p_edit.add_argument("-d", "--date", help="new date, YYYY-MM-DD")
    p_edit.add_argument("-a", "--account", help="move to this account")
    p_edit.add_argument("-c", "--category", help="move to this category")
    p_edit.add_argument("-t", "--type", choices=["income", "expense"],
                        help="type for a new category (use with --category)")
    p_edit.add_argument("-n", "--note", help='new note ("" clears it)')
    p_edit.add_argument("--currency", default="USD", help="currency for a new account")
    p_edit.set_defaults(func=cmd_edit)

    args = parser.parse_args()
    try:
        args.func(args)
    except ValueError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
