# fintracker
![Tests](https://github.com/BilousNikita/fintracker/actions/workflows/tests.yml/badge.svg)

A small command-line personal finance tracker written in Python.
Data is stored locally in a SQLite database, with no external dependencies.

## Features

- Add income and expense transactions
- List and filter transactions by month, account or category
- Balance per account
- Monthly report with expenses by category
- Delete transactions
- Import transactions from CSV (validated first, all-or-nothing)
- Local web interface (optional): dashboard, transactions, budgets

## Requirements

- Python 3.10+
- No third-party packages (uses only the standard library)

## Setup

```bash
git clone git@github.com:BilousNikita/fintracker.git
cd fintracker
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

By default the database lives at `~/.local/share/fintracker/fintracker.db`
(respects `$XDG_DATA_HOME`). Override it with the `FINTRACKER_DB` environment
variable or the global `--db PATH` option. It is created automatically on
first run.

## Usage

### Add a transaction

```bash
fintracker add 3000 -a Bank -c Salary -t income
fintracker add 12.50 -a Cash -c Food -n "lunch"
fintracker add 45 -a Bank -c Transport -d 2026-11-01
```

| Option | Description |
| --- | --- |
| `-a, --account` | Account name (created if missing) |
| `-c, --category` | Category name (created if missing) |
| `-t, --type` | `income` or `expense` (default: `expense`) |
| `-d, --date` | `YYYY-MM-DD` (default: today) |
| `-n, --note` | Optional note |
| `--currency` | Currency for a new account (default: `USD`) |

### List transactions

```bash
fintracker list
fintracker list -m 2026-11 -c Food
```

Filters: `-m/--month`, `-c/--category`, `-a/--account`, `-l/--limit`.

### Balance and report

```bash
fintracker balance
fintracker report
fintracker report -m 2026-10
```

### Budgets

```bash
fintracker budget set Food 300      # monthly limit for an expense category
fintracker budget status            # current month
fintracker budget status -m 2026-10
fintracker budget remove Food
```

`add` prints a warning when an expense brings its category to 80% of the
monthly limit, or past it.

### Edit a transaction

```bash
fintracker edit 3 --amount 15.00
fintracker edit 3 -d 2026-10-31 -a Bank
fintracker edit 3 -c Restaurants -n "dinner with friends"
fintracker edit 3 -n ""            # clear the note
```

Only the fields you pass are changed. A new category inherits the
transaction's current type unless you pass `--type`; moving a transaction
to an existing category of the other type changes it between income and expense.

### Delete a transaction

```bash
fintracker delete 3        # asks for confirmation
fintracker delete 3 -y     # no confirmation
```

### Import from CSV

```bash
fintracker import transactions.csv --dry-run   # validate only
fintracker import transactions.csv
```

Required columns: `date`, `amount`, `account`, `category`.
Optional columns: `type` (default `expense`), `currency`, `note`.

```csv
date,amount,account,category,type,note
2026-11-01,2500,Bank,Salary,income,November salary
2026-11-02,34.90,Cash,Food,expense,groceries
```

If any row is invalid, nothing is imported and the offending lines are
reported. Rows identical to existing transactions (same date, amount, account, category and note) are skipped, so re-importing a file is safe. Use `--allow-duplicates` to disable this.

### Export to CSV

```bash
fintracker export backup.csv
fintracker export report.csv -m 2026-11 -c Food
fintracker export -            # print to stdout
```

The file uses the same columns as the import format, so it can be imported
back (duplicates are skipped). An existing file is not overwritten unless you
pass `--force`.

## Web interface

A small local web UI (dashboard, transactions, budgets) is included.
It needs Flask, which is an optional dependency:

```bash
pip install -e ".[web]"
fintracker web              # http://127.0.0.1:5000
fintracker web --port 8080
```

The server listens on `127.0.0.1` only, rejects requests for other hosts and
protects every form with a CSRF token. It uses the same database as the CLI.

## Data model

- `accounts` (id, name, currency)
- `categories` (id, name, type: income or expense)
- `transactions` (id, date, amount, account_id, category_id, note)
- `budgets` (category_id, monthly_limit)

Amounts are stored as integer cents to avoid floating-point rounding errors.
The sign of a transaction is determined by its category type.

## Privacy

`fintracker.db`, `*.csv` and `.env` are listed in `.gitignore`, so your
financial data is never committed.

## Project structure

```
fintracker/
├── fintracker/
│   ├── cli.py        # CLI
│   ├── web.py        # optional local web UI
│   ├── schema.sql    # database schema
│   └── __main__.py
├── tests/
├── pyproject.toml
└── README.md
```

## Roadmap

- `accounts` and `categories` listing commands
- Automatic database backups

## Running tests

```bash
pip install -e . -r requirements-dev.txt
pytest
```

Tests use a temporary database, so your real data is never touched.
Continuous integration runs the suite on Python 3.10, 3.12 and 3.14.
