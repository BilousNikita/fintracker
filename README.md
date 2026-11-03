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

## Requirements

- Python 3.10+
- No third-party packages (uses only the standard library)

## Setup

```bash
git clone git@github.com:BilousNikita/fintracker.git
cd fintracker
python3 -m venv .venv
source .venv/bin/activate
```

The database file `fintracker.db` is created automatically on first run
from `schema.sql`.

## Usage

### Add a transaction

```bash
python tracker.py add 3000 -a Bank -c Salary -t income
python tracker.py add 12.50 -a Cash -c Food -n "lunch"
python tracker.py add 45 -a Bank -c Transport -d 2026-11-01
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
python tracker.py list
python tracker.py list -m 2026-11 -c Food
```

Filters: `-m/--month`, `-c/--category`, `-a/--account`, `-l/--limit`.

### Balance and report

```bash
python tracker.py balance
python tracker.py report
python tracker.py report -m 2026-10
```

### Budgets

```bash
python tracker.py budget set Food 300      # monthly limit for an expense category
python tracker.py budget status            # current month
python tracker.py budget status -m 2026-10
python tracker.py budget remove Food
```

`add` prints a warning when an expense brings its category to 80% of the
monthly limit, or past it.

### Delete a transaction

```bash
python tracker.py delete 3        # asks for confirmation
python tracker.py delete 3 -y     # no confirmation
```

### Import from CSV

```bash
python tracker.py import transactions.csv --dry-run   # validate only
python tracker.py import transactions.csv
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
python tracker.py export backup.csv
python tracker.py export report.csv -m 2026-11 -c Food
python tracker.py export -            # print to stdout
```

The file uses the same columns as the import format, so it can be imported
back (duplicates are skipped). An existing file is not overwritten unless you
pass `--force`.

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
├── tracker.py    # CLI
├── schema.sql    # database schema
└── README.md
```

## Roadmap

- `edit` command for existing transactions
- Installable package with `pyproject.toml`

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests use a temporary database, so your real data is never touched.
Continuous integration runs the suite on Python 3.10, 3.12 and 3.14.
