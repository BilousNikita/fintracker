"""Local web interface for fintracker.

Needs Flask:  pip install "fintracker[web]"   then run:  fintracker web
"""
import hmac
import secrets
from contextlib import contextmanager
from datetime import date, datetime

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from jinja2 import DictLoader

from fintracker.cli import (
    budget_state,
    budget_warning,
    connect,
    fmt_money,
    get_or_create_account,
    get_or_create_category,
    month_spent,
    parse_amount,
    parse_date,
)

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
PAGES = ("dashboard", "transactions", "budgets")


# ---------- helpers ----------

@contextmanager
def db():
    """One connection per use: commit on success, roll back on error, always close."""
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def money(cents):
    return ("-" if cents < 0 else "") + fmt_money(abs(cents))


def signed(cents):
    return ("-" if cents < 0 else "+") + fmt_money(abs(cents))


def current_month():
    return date.today().strftime("%Y-%m")


def valid_month(text):
    try:
        datetime.strptime(text or "", "%Y-%m")
    except ValueError:
        return None
    return text


def get_month():
    return valid_month(request.args.get("month")) or current_month()


def shift_month(month, delta):
    year, mon = (int(x) for x in month.split("-"))
    idx = year * 12 + (mon - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def lookups(conn):
    accounts = [r[0] for r in conn.execute("SELECT name FROM accounts ORDER BY name")]
    categories = [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY name")]
    return accounts, categories


def month_totals(conn, month):
    rows = conn.execute(
        "SELECT c.type, SUM(t.amount) FROM transactions t "
        "JOIN categories c ON c.id = t.category_id "
        "WHERE substr(t.date, 1, 7) = ? GROUP BY c.type",
        (month,),
    ).fetchall()
    totals = {r[0]: r[1] for r in rows}
    return totals.get("income", 0), totals.get("expense", 0)


def account_balances(conn):
    return conn.execute(
        "SELECT a.name, a.currency, "
        "COALESCE(SUM(CASE WHEN c.type = 'income' THEN t.amount "
        "                  ELSE -t.amount END), 0) AS balance "
        "FROM accounts a "
        "LEFT JOIN transactions t ON t.account_id = a.id "
        "LEFT JOIN categories c ON c.id = t.category_id "
        "GROUP BY a.id ORDER BY a.name"
    ).fetchall()


def expenses_by_category(conn, month):
    rows = conn.execute(
        "SELECT c.name, SUM(t.amount) AS total FROM transactions t "
        "JOIN categories c ON c.id = t.category_id "
        "WHERE c.type = 'expense' AND substr(t.date, 1, 7) = ? "
        "GROUP BY c.id ORDER BY total DESC",
        (month,),
    ).fetchall()
    grand = sum(r["total"] for r in rows)
    return [
        {"name": r["name"], "total": r["total"],
         "percent": r["total"] * 100 // grand if grand else 0}
        for r in rows
    ]


def budget_rows(conn, month):
    rows = conn.execute(
        "SELECT c.id, c.name, b.monthly_limit FROM budgets b "
        "JOIN categories c ON c.id = b.category_id ORDER BY c.name"
    ).fetchall()
    result = []
    for r in rows:
        limit = r["monthly_limit"]
        spent = month_spent(conn, r["id"], month)
        percent = spent * 100 // limit
        result.append({
            "id": r["id"], "name": r["name"], "limit": limit, "spent": spent,
            "left": limit - spent, "percent": percent, "bar": min(percent, 100),
            "state": budget_state(spent, limit),
        })
    return result


def list_transactions(conn, month=None, category=None, account=None, limit=200):
    query = (
        "SELECT t.id, t.date, t.amount, t.note, a.name AS account, "
        "c.name AS category, c.type AS type "
        "FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id "
        "JOIN categories c ON c.id = t.category_id WHERE 1=1"
    )
    params = []
    if month:
        query += " AND substr(t.date, 1, 7) = ?"
        params.append(month)
    if category:
        query += " AND c.name = ?"
        params.append(category)
    if account:
        query += " AND a.name = ?"
        params.append(account)
    query += " ORDER BY t.date DESC, t.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(query, params).fetchall()


# ---------- templates ----------

TEMPLATES = {
    "base.html": """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fintracker</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--text:#1c2230;--muted:#667085;--line:#e4e7ec;--accent:#2f6fed;--good:#16a34a;--warn:#d97706;--bad:#dc2626}
@media (prefers-color-scheme: dark){:root{--bg:#12151c;--card:#1b202b;--text:#e8ecf4;--muted:#9aa4b8;--line:#2a3140;--accent:#6c9cff;--good:#4ade80;--warn:#fbbf24;--bad:#f87171}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:0 16px}
header{background:var(--card);border-bottom:1px solid var(--line)}
header .wrap{display:flex;align-items:center;gap:24px;height:56px}
.brand{font-weight:700;color:var(--text);text-decoration:none}
nav a{color:var(--muted);text-decoration:none;margin-right:16px;padding:4px 0}
nav a.on{color:var(--text);border-bottom:2px solid var(--accent)}
main{padding:24px 16px 48px}
h1{font-size:22px;margin:0}
h2{font-size:16px;margin:28px 0 10px}
.head{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
.months{display:flex;gap:12px;align-items:center}
.months a{color:var(--accent);text-decoration:none;font-size:22px;padding:0 6px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:16px}
.card,.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .label{display:block;color:var(--muted);font-size:13px}
.big{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}
.good,.ok{color:var(--good)}
.bad,.over{color:var(--bad)}
.warning{color:var(--warn)}
.muted{color:var(--muted)}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500;font-size:13px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden}
.fill{height:100%;background:var(--good)}
.fill.warning{background:var(--warn)}
.fill.over{background:var(--bad)}
.budget{margin-bottom:16px}
.budget .row{display:flex;justify-content:space-between;margin-bottom:4px}
.budget .sub{color:var(--muted);font-size:13px;margin-top:4px}
em{font-style:normal;font-size:12px;margin-left:6px}
form.grid-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;align-items:end}
label{display:flex;flex-direction:column;font-size:13px;color:var(--muted);gap:4px}
input,select{font:inherit;padding:7px 9px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--text)}
button{font:inherit;padding:8px 14px;border:0;border-radius:6px;background:var(--accent);color:#fff;cursor:pointer}
button.link{background:none;color:var(--bad);padding:2px 6px}
.flash{padding:10px 14px;border-radius:8px;margin-bottom:12px;border:1px solid var(--line);background:var(--card)}
.flash.error{border-color:var(--bad);color:var(--bad)}
.flash.warn{border-color:var(--warn);color:var(--warn)}
.flash.ok{border-color:var(--good)}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
</style>
</head>
<body>
<header><div class="wrap">
  <a class="brand" href="{{ url_for('dashboard') }}">fintracker</a>
  <nav>
    <a href="{{ url_for('dashboard') }}" class="{{ 'on' if active == 'dashboard' }}">Dashboard</a>
    <a href="{{ url_for('transactions') }}" class="{{ 'on' if active == 'transactions' }}">Transactions</a>
    <a href="{{ url_for('budgets') }}" class="{{ 'on' if active == 'budgets' }}">Budgets</a>
  </nav>
</div></header>
<main class="wrap">
{% for category, message in get_flashed_messages(with_categories=true) %}
  <div class="flash {{ category }}">{{ message }}</div>
{% endfor %}
{% block content %}{% endblock %}
</main>
</body>
</html>
""",

    "macros.html": """{% macro add_form(target, accounts, categories) %}
<form method="post" action="{{ url_for('add_transaction') }}" class="grid-form">
  <input type="hidden" name="csrf" value="{{ csrf_token }}">
  <input type="hidden" name="next" value="{{ target }}">
  <label>Amount <input name="amount" inputmode="decimal" placeholder="12.50" required></label>
  <label>Type (for a new category) <select name="type"><option value="expense">Expense</option><option value="income">Income</option></select></label>
  <label>Date <input name="date" type="date" value="{{ today }}" required></label>
  <label>Account <input name="account" list="accounts-list" placeholder="Cash" required></label>
  <label>Category <input name="category" list="categories-list" placeholder="Food" required></label>
  <label>Note <input name="note"></label>
  <button type="submit">Add</button>
  <datalist id="accounts-list">{% for a in accounts %}<option value="{{ a }}">{% endfor %}</datalist>
  <datalist id="categories-list">{% for c in categories %}<option value="{{ c }}">{% endfor %}</datalist>
</form>
{% endmacro %}

{% macro budget_bar(b) %}
<div class="budget">
  <div class="row"><strong>{{ b.name }}</strong>
    <span>{{ b.spent|money }} / {{ b.limit|money }}<em class="{{ b.state|lower }}">{{ b.state }}</em></span></div>
  <div class="bar"><div class="fill {{ b.state|lower }}" style="width: {{ b.bar }}%"></div></div>
  <div class="sub">{{ b.percent }}% used &middot; {{ b.left|money }} left</div>
</div>
{% endmacro %}

{% macro txn_table(rows) %}
<table>
<thead><tr><th>Date</th><th>Account</th><th>Category</th><th>Note</th><th class="num">Amount</th><th></th></tr></thead>
<tbody>
{% for t in rows %}
<tr>
  <td>{{ t.date }}</td><td>{{ t.account }}</td><td>{{ t.category }}</td>
  <td class="muted">{{ t.note or '' }}</td>
  <td class="num {{ 'good' if t.type == 'income' else 'bad' }}">{{ '+' if t.type == 'income' else '-' }}{{ t.amount|money }}</td>
  <td><form method="post" action="{{ url_for('delete_transaction', tid=t.id) }}" onsubmit="return confirm('Delete this transaction?')">
    <input type="hidden" name="csrf" value="{{ csrf_token }}"><button class="link" type="submit">Delete</button></form></td>
</tr>
{% else %}
<tr><td colspan="6" class="muted">No transactions.</td></tr>
{% endfor %}
</tbody>
</table>
{% endmacro %}
""",

    "dashboard.html": """{% extends "base.html" %}
{% from "macros.html" import add_form, budget_bar, txn_table with context %}
{% block content %}
<div class="head">
  <h1>Dashboard</h1>
  <div class="months"><a href="{{ url_for('dashboard', month=prev) }}">&lsaquo;</a><strong>{{ month }}</strong><a href="{{ url_for('dashboard', month=nxt) }}">&rsaquo;</a></div>
</div>
<div class="cards">
  <div class="card"><span class="label">Income</span><span class="big good">{{ income|money }}</span></div>
  <div class="card"><span class="label">Expenses</span><span class="big bad">{{ expenses|money }}</span></div>
  <div class="card"><span class="label">Net</span><span class="big {{ 'good' if net >= 0 else 'bad' }}">{{ net|signed }}</span></div>
</div>

<h2>Add transaction</h2>
<div class="panel">{{ add_form('dashboard', accounts, category_names) }}</div>

<div class="two">
  <div>
    <h2>Accounts</h2>
    <div class="panel">
    {% if balances %}
      <table><thead><tr><th>Account</th><th class="num">Balance</th></tr></thead><tbody>
      {% for a in balances %}
        <tr><td>{{ a.name }} <span class="muted">{{ a.currency }}</span></td><td class="num {{ 'good' if a.balance >= 0 else 'bad' }}">{{ a.balance|signed }}</td></tr>
      {% endfor %}
      </tbody></table>
    {% else %}<p class="muted">No accounts yet. Add your first transaction above.</p>{% endif %}
    </div>
  </div>
  <div>
    <h2>Expenses by category</h2>
    <div class="panel">
    {% if categories %}
      <table><thead><tr><th>Category</th><th class="num">Spent</th><th class="num">Share</th></tr></thead><tbody>
      {% for c in categories %}
        <tr><td>{{ c.name }}</td><td class="num">{{ c.total|money }}</td><td class="num muted">{{ c.percent }}%</td></tr>
      {% endfor %}
      </tbody></table>
    {% else %}<p class="muted">No expenses in {{ month }}.</p>{% endif %}
    </div>
  </div>
</div>

<h2>Budgets</h2>
<div class="panel">
{% for b in budgets %}{{ budget_bar(b) }}{% else %}
  <p class="muted">No budgets yet. <a href="{{ url_for('budgets') }}">Set one</a>.</p>
{% endfor %}
</div>

<h2>Recent transactions</h2>
<div class="panel">{{ txn_table(recent) }}</div>
{% endblock %}
""",

    "transactions.html": """{% extends "base.html" %}
{% from "macros.html" import add_form, txn_table with context %}
{% block content %}
<h1>Transactions</h1>
<h2>Add transaction</h2>
<div class="panel">{{ add_form('transactions', accounts, category_names) }}</div>

<h2>Filter</h2>
<div class="panel">
<form method="get" class="grid-form">
  <label>Month <input name="month" value="{{ month or '' }}" placeholder="YYYY-MM"></label>
  <label>Category <select name="category"><option value="">All</option>
    {% for c in category_names %}<option value="{{ c }}" {{ 'selected' if c == category }}>{{ c }}</option>{% endfor %}</select></label>
  <label>Account <select name="account"><option value="">All</option>
    {% for a in accounts %}<option value="{{ a }}" {{ 'selected' if a == account }}>{{ a }}</option>{% endfor %}</select></label>
  <button type="submit">Filter</button>
</form>
</div>

<h2>{{ rows|length }} transactions</h2>
<div class="panel">{{ txn_table(rows) }}</div>
{% endblock %}
""",

    "budgets.html": """{% extends "base.html" %}
{% from "macros.html" import budget_bar with context %}
{% block content %}
<div class="head">
  <h1>Budgets</h1>
  <div class="months"><a href="{{ url_for('budgets', month=prev) }}">&lsaquo;</a><strong>{{ month }}</strong><a href="{{ url_for('budgets', month=nxt) }}">&rsaquo;</a></div>
</div>

<h2>Set a monthly limit</h2>
<div class="panel">
{% if expense_categories %}
<form method="post" action="{{ url_for('set_budget') }}" class="grid-form">
  <input type="hidden" name="csrf" value="{{ csrf_token }}">
  <input type="hidden" name="month" value="{{ month }}">
  <label>Category <select name="category">{% for c in expense_categories %}<option>{{ c }}</option>{% endfor %}</select></label>
  <label>Monthly limit <input name="amount" inputmode="decimal" placeholder="300" required></label>
  <button type="submit">Save</button>
</form>
{% else %}
<p class="muted">Add an expense first: budgets are set per expense category.</p>
{% endif %}
</div>

<h2>Status for {{ month }}</h2>
<div class="panel">
{% for b in budgets %}
  {{ budget_bar(b) }}
  <form method="post" action="{{ url_for('remove_budget', cid=b.id) }}" style="margin:-8px 0 16px">
    <input type="hidden" name="csrf" value="{{ csrf_token }}">
    <input type="hidden" name="month" value="{{ month }}">
    <button class="link" type="submit">Remove budget</button>
  </form>
{% else %}
  <p class="muted">No budgets yet.</p>
{% endfor %}
</div>
{% endblock %}
""",
}


# ---------- app ----------

def create_app():
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)
    app.jinja_env.loader = DictLoader(TEMPLATES)
    app.jinja_env.filters["money"] = money
    app.jinja_env.filters["signed"] = signed

    csrf = secrets.token_urlsafe(24)
    app.config["CSRF_TOKEN"] = csrf

    @app.context_processor
    def inject():
        return {"csrf_token": csrf, "today": date.today().isoformat()}

    @app.before_request
    def guard():
        # Only answer requests addressed to localhost (defends against DNS rebinding)
        if request.host.split(":")[0] not in ALLOWED_HOSTS:
            abort(400)
        # Every POST needs the per-run token (defends against cross-site form posts)
        if request.method == "POST":
            if not hmac.compare_digest(request.form.get("csrf", ""), csrf):
                abort(400)

    @app.after_request
    def headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/")
    def dashboard():
        month = get_month()
        with db() as conn:
            income, expenses = month_totals(conn, month)
            accounts, category_names = lookups(conn)
            ctx = dict(
                active="dashboard", month=month,
                prev=shift_month(month, -1), nxt=shift_month(month, 1),
                income=income, expenses=expenses, net=income - expenses,
                balances=account_balances(conn),
                categories=expenses_by_category(conn, month),
                budgets=budget_rows(conn, month),
                recent=list_transactions(conn, month=month, limit=8),
                accounts=accounts, category_names=category_names,
            )
        return render_template("dashboard.html", **ctx)

    @app.get("/transactions")
    def transactions():
        month = valid_month(request.args.get("month"))
        category = request.args.get("category") or None
        account = request.args.get("account") or None
        with db() as conn:
            rows = list_transactions(conn, month, category, account)
            accounts, category_names = lookups(conn)
        return render_template(
            "transactions.html", active="transactions", rows=rows, month=month,
            category=category, account=account,
            accounts=accounts, category_names=category_names,
        )

    @app.post("/transactions/add")
    def add_transaction():
        form = request.form
        target = form.get("next") if form.get("next") in PAGES else "transactions"
        try:
            amount = parse_amount(form.get("amount", ""))
            day = parse_date(form.get("date", ""))
            account = form.get("account", "").strip()
            category = form.get("category", "").strip()
            kind = form.get("type", "expense")
            if not account or not category:
                raise ValueError("Account and category are required")
            if kind not in ("income", "expense"):
                raise ValueError("Invalid type")
            with db() as conn:
                account_id = get_or_create_account(conn, account, "USD")
                row = conn.execute(
                    "SELECT id, type FROM categories WHERE name = ?", (category,)
                ).fetchone()
                if row:  # an existing category decides the type
                    category_id, kind = row["id"], row["type"]
                else:
                    category_id = get_or_create_category(conn, category, kind)
                conn.execute(
                    "INSERT INTO transactions (date, amount, account_id, category_id, note) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (day, amount, account_id, category_id,
                     form.get("note", "").strip() or None),
                )
                warning = (budget_warning(conn, category_id, day[:7], category)
                           if kind == "expense" else None)
        except ValueError as e:
            flash(str(e), "error")
        else:
            flash(f"Added {kind} {fmt_money(amount)} [{account} / {category}]", "ok")
            if warning:
                flash(warning, "warn")
        return redirect(url_for(target))

    @app.post("/transactions/<int:tid>/delete")
    def delete_transaction(tid):
        with db() as conn:
            cur = conn.execute("DELETE FROM transactions WHERE id = ?", (tid,))
        if cur.rowcount:
            flash(f"Deleted transaction {tid}", "ok")
        else:
            flash(f"Transaction {tid} not found", "error")
        return redirect(url_for("transactions"))

    @app.get("/budgets")
    def budgets():
        month = get_month()
        with db() as conn:
            expense_categories = [
                r[0] for r in conn.execute(
                    "SELECT name FROM categories WHERE type = 'expense' ORDER BY name")
            ]
            ctx = dict(
                active="budgets", month=month,
                prev=shift_month(month, -1), nxt=shift_month(month, 1),
                budgets=budget_rows(conn, month),
                expense_categories=expense_categories,
            )
        return render_template("budgets.html", **ctx)

    @app.post("/budgets/set")
    def set_budget():
        month = valid_month(request.form.get("month")) or current_month()
        name = request.form.get("category", "").strip()
        try:
            limit = parse_amount(request.form.get("amount", ""))
            with db() as conn:
                row = conn.execute(
                    "SELECT id, type FROM categories WHERE name = ?", (name,)
                ).fetchone()
                if not row:
                    raise ValueError(f"Category '{name}' not found")
                if row["type"] != "expense":
                    raise ValueError("Budgets can only be set for expense categories")
                conn.execute(
                    "INSERT INTO budgets (category_id, monthly_limit) VALUES (?, ?) "
                    "ON CONFLICT(category_id) DO UPDATE SET "
                    "monthly_limit = excluded.monthly_limit",
                    (row["id"], limit),
                )
        except ValueError as e:
            flash(str(e), "error")
        else:
            flash(f"Budget for {name}: {fmt_money(limit)} per month", "ok")
        return redirect(url_for("budgets", month=month))

    @app.post("/budgets/<int:cid>/remove")
    def remove_budget(cid):
        month = valid_month(request.form.get("month")) or current_month()
        with db() as conn:
            cur = conn.execute("DELETE FROM budgets WHERE category_id = ?", (cid,))
        flash("Budget removed" if cur.rowcount else "No such budget",
              "ok" if cur.rowcount else "error")
        return redirect(url_for("budgets", month=month))

    return app
