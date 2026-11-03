import sys

import pytest

from fintracker import cli as tracker


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Every test gets its own empty database."""
    monkeypatch.setenv("FINTRACKER_DB", str(tmp_path / "test.db"))


@pytest.fixture
def cli(monkeypatch, capsys):
    """Run the CLI like `python tracker.py <args>` and return its stdout."""
    def run(*args):
        monkeypatch.setattr(sys, "argv", ["tracker.py", *args])
        tracker.main()
        return capsys.readouterr().out
    return run


def seed(cli):
    cli("add", "3000", "-a", "Bank", "-c", "Salary", "-t", "income", "-d", "2026-11-01")
    cli("add", "12.50", "-a", "Cash", "-c", "Food", "-d", "2026-11-02", "-n", "lunch")
    cli("add", "45", "-a", "Bank", "-c", "Transport", "-d", "2026-11-03", "-n", "bus")
    cli("add", "120", "-a", "Bank", "-c", "Groceries", "-d", "2026-11-04")


# ---------- parsing and formatting ----------

@pytest.mark.parametrize("text, cents", [
    ("12.50", 1250),
    ("12,5", 1250),
    ("3000", 300000),
    ("0.01", 1),
])
def test_parse_amount_valid(text, cents):
    assert tracker.parse_amount(text) == cents


@pytest.mark.parametrize("text", ["abc", "", "0", "-5", "1.234", "nan", "inf"])
def test_parse_amount_invalid(text):
    with pytest.raises(ValueError):
        tracker.parse_amount(text)


def test_parse_date_valid():
    assert tracker.parse_date("2026-11-03") == "2026-11-03"


@pytest.mark.parametrize("text", ["2026-13-45", "11/03/2026", "yesterday", ""])
def test_parse_date_invalid(text):
    with pytest.raises(ValueError):
        tracker.parse_date(text)


def test_formatting():
    assert tracker.fmt_money(1250) == "12.50"
    assert tracker.fmt_money(5) == "0.05"
    assert tracker.fmt_signed(-1250) == "-12.50"
    assert tracker.fmt_signed(1250) == "+12.50"
    assert tracker.fmt_signed(0) == "+0.00"


# ---------- add and list ----------

def test_add_and_list(cli):
    out = cli("add", "12.50", "-a", "Cash", "-c", "Food", "-d", "2026-11-02", "-n", "lunch")
    assert "Added: 2026-11-02 expense 12.50" in out

    out = cli("list")
    assert "-12.50" in out
    assert "lunch" in out
    assert "Total: -12.50" in out


def test_list_empty(cli):
    assert "No transactions found." in cli("list")


def test_list_filters(cli):
    seed(cli)
    out = cli("list", "-c", "Food")
    assert "lunch" in out
    assert "bus" not in out

    out = cli("list", "-a", "Cash")
    assert "lunch" in out
    assert "bus" not in out


def test_list_month_filter(cli):
    seed(cli)
    cli("add", "500", "-a", "Bank", "-c", "Rent", "-d", "2026-10-15", "-n", "october rent")
    out = cli("list", "-m", "2026-10")
    assert "october rent" in out
    assert "lunch" not in out


def test_list_newest_first(cli):
    cli("add", "1", "-a", "Cash", "-c", "Food", "-d", "2026-11-01", "-n", "older")
    cli("add", "1", "-a", "Cash", "-c", "Food", "-d", "2026-11-05", "-n", "newer")
    out = cli("list")
    assert out.index("newer") < out.index("older")


def test_add_rejects_bad_input(cli, capsys):
    for bad in (["abc"], ["-5"], ["1.234"]):
        with pytest.raises(SystemExit) as exc:
            cli("add", *bad, "-a", "Cash", "-c", "Food")
        assert "Error" in str(exc.value.code)

    with pytest.raises(SystemExit) as exc:
        cli("add", "10", "-a", "Cash", "-c", "Food", "-d", "2026-13-45")
    assert "Error" in str(exc.value.code)
    assert "No transactions found." in cli("list")


def test_category_type_conflict(cli):
    cli("add", "10", "-a", "Cash", "-c", "Food")
    with pytest.raises(SystemExit) as exc:
        cli("add", "10", "-a", "Cash", "-c", "Food", "-t", "income")
    assert "already exists" in str(exc.value.code)


# ---------- balance and report ----------

def test_balance(cli):
    seed(cli)
    out = cli("balance")
    assert "+2835.00" in out   # Bank: 3000 - 45 - 120
    assert "-12.50" in out     # Cash


def test_balance_no_accounts(cli):
    assert "No accounts yet." in cli("balance")


def test_report(cli):
    seed(cli)
    cli("add", "500", "-a", "Bank", "-c", "Rent", "-d", "2026-10-15")
    out = cli("report", "-m", "2026-11")

    assert "Rent" not in out                      # other month is excluded
    assert out.index("Groceries") < out.index("Transport") < out.index("Food")
    assert "67%" in out and "25%" in out and " 7%" in out
    assert "Income:" in out and "3000.00" in out
    assert "Expenses:" in out and "177.50" in out
    assert "+2822.50" in out


def test_report_empty_month(cli):
    seed(cli)
    assert "No transactions for 2026-10." in cli("report", "-m", "2026-10")


# ---------- delete ----------

def test_delete_with_yes(cli):
    seed(cli)
    out = cli("delete", "2", "-y")
    assert "Deleted." in out
    assert "lunch" not in cli("list")


def test_delete_cancelled(cli, monkeypatch):
    seed(cli)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert "Cancelled." in cli("delete", "2")
    assert "lunch" in cli("list")


def test_delete_confirmed(cli, monkeypatch):
    seed(cli)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert "Deleted." in cli("delete", "2")
    assert "lunch" not in cli("list")


def test_delete_missing_id(cli):
    with pytest.raises(SystemExit) as exc:
        cli("delete", "999", "-y")
    assert "not found" in str(exc.value.code)


# ---------- CSV import ----------

GOOD_CSV = (
    "date,amount,account,category,type,note\n"
    "2026-11-01,2500,Bank,Salary,income,November salary\n"
    "2026-11-02,34.90,Cash,Food,expense,groceries\n"
    "2026-11-02,15,Bank,Transport,expense,bus pass\n"
)


def write_csv(tmp_path, text, name="data.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_import_dry_run_saves_nothing(cli, tmp_path):
    path = write_csv(tmp_path, GOOD_CSV)
    assert "Dry run OK: 3 transactions" in cli("import", path, "--dry-run")
    assert "No transactions found." in cli("list")


def test_import_saves_rows(cli, tmp_path):
    path = write_csv(tmp_path, GOOD_CSV)
    assert "Imported 3 transactions" in cli("import", path)
    out = cli("list")
    assert "November salary" in out
    assert "groceries" in out
    assert "Total: +2450.10" in out    # 2500 - 34.90 - 15


def test_import_optional_columns(cli, tmp_path):
    path = write_csv(tmp_path, "date,amount,account,category\n2026-11-01,10,Cash,Food\n")
    assert "Imported 1 transactions" in cli("import", path)
    assert "-10.00" in cli("list")


def test_import_is_all_or_nothing(cli, tmp_path, capsys):
    bad = (
        "date,amount,account,category\n"
        "2026-11-01,10,Cash,Food\n"          # line 2: valid
        "2026-13-45,10,Cash,Food\n"          # line 3: bad date
        "2026-11-03,abc,Cash,Food\n"         # line 4: bad amount
    )
    path = write_csv(tmp_path, bad)
    with pytest.raises(SystemExit) as exc:
        cli("import", path)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "line 3" in out and "line 4" in out
    assert "line 2" not in out
    assert "No transactions found." in cli("list")   # nothing was saved


def test_import_missing_columns(cli, tmp_path):
    path = write_csv(tmp_path, "date,amount\n2026-11-01,10\n")
    with pytest.raises(SystemExit) as exc:
        cli("import", path)
    assert "Missing columns" in str(exc.value.code)


def test_import_file_not_found(cli, tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli("import", str(tmp_path / "nope.csv"))
    assert "File not found" in str(exc.value.code)


# ---------- duplicate protection ----------

def test_import_skips_duplicates(cli, tmp_path):
    path = write_csv(tmp_path, GOOD_CSV)
    cli("import", path)
    out = cli("import", path)
    assert "Imported 0 transactions" in out
    assert "Skipped 3 duplicates" in out
    assert "Total: +2450.10" in cli("list")   # unchanged


def test_import_partial_overlap(cli, tmp_path):
    cli("import", write_csv(tmp_path, GOOD_CSV, "a.csv"))
    more = GOOD_CSV + "2026-11-05,7,Cash,Food,expense,snack\n"
    out = cli("import", write_csv(tmp_path, more, "b.csv"))
    assert "Imported 1 transactions" in out
    assert "Skipped 3 duplicates" in out


def test_import_keeps_identical_rows_within_one_file(cli, tmp_path):
    two = (
        "date,amount,account,category\n"
        "2026-11-01,3,Cash,Coffee\n"
        "2026-11-01,3,Cash,Coffee\n"
    )
    path = write_csv(tmp_path, two)
    assert "Imported 2 transactions" in cli("import", path)
    assert "Imported 0 transactions" in cli("import", path)   # re-import is safe


def test_import_matches_manually_added(cli, tmp_path):
    cli("add", "34.90", "-a", "Cash", "-c", "Food", "-d", "2026-11-02", "-n", "groceries")
    out = cli("import", write_csv(tmp_path, GOOD_CSV))
    assert "Imported 2 transactions" in out
    assert "Skipped 1 duplicates" in out


def test_import_allow_duplicates(cli, tmp_path):
    path = write_csv(tmp_path, GOOD_CSV)
    cli("import", path)
    out = cli("import", path, "--allow-duplicates")
    assert "Imported 3 transactions" in out
    assert "Skipped" not in out


def test_dry_run_reports_duplicates(cli, tmp_path):
    path = write_csv(tmp_path, GOOD_CSV)
    cli("import", path)
    out = cli("import", path, "--dry-run")
    assert "Dry run OK: 0 transactions" in out
    assert "3 duplicates would be skipped" in out


# ---------- export ----------

def test_export_to_stdout(cli):
    seed(cli)
    lines = cli("export", "-").strip().splitlines()
    assert lines[0] == "date,amount,account,category,type,currency,note"
    assert "2026-11-02,12.50,Cash,Food,expense,USD,lunch" in lines
    assert len(lines) == 5   # header + 4 transactions


def test_export_filters(cli):
    seed(cli)
    out = cli("export", "-", "-c", "Food")
    assert "lunch" in out
    assert "bus" not in out


def test_export_file_and_overwrite_protection(cli, tmp_path):
    seed(cli)
    target = tmp_path / "out.csv"
    assert "Exported 4 transactions" in cli("export", str(target))
    with pytest.raises(SystemExit) as exc:
        cli("export", str(target))
    assert "already exists" in str(exc.value.code)
    assert "Exported 4 transactions" in cli("export", str(target), "--force")


def test_export_import_roundtrip(cli, tmp_path):
    seed(cli)
    target = tmp_path / "out.csv"
    cli("export", str(target))
    before = cli("list")
    out = cli("import", str(target))
    assert "Imported 0 transactions" in out      # everything is a duplicate
    assert "Skipped 4 duplicates" in out
    assert cli("list") == before


# ---------- budgets ----------

def test_budget_set_and_status(cli):
    seed(cli)
    assert "Budget for Food: 100.00 per month" in cli("budget", "set", "Food", "100")
    cli("budget", "set", "Transport", "40")
    lines = cli("budget", "status", "-m", "2026-11").splitlines()

    food = next(l for l in lines if l.startswith("Food"))
    for part in ("100.00", "12.50", "87.50", "12%", "OK"):
        assert part in food

    transport = next(l for l in lines if l.startswith("Transport"))
    for part in ("45.00", "-5.00", "112%", "OVER"):
        assert part in transport


def test_budget_warnings_on_add(cli):
    cli("add", "10", "-a", "Cash", "-c", "Fun", "-d", "2026-11-01")
    cli("budget", "set", "Fun", "100")

    out = cli("add", "10", "-a", "Cash", "-c", "Fun", "-d", "2026-11-02")
    assert "Warning" not in out                      # 20 of 100

    out = cli("add", "65", "-a", "Cash", "-c", "Fun", "-d", "2026-11-03")
    assert "Warning: Fun budget at 85%" in out       # 85 of 100

    out = cli("add", "20", "-a", "Cash", "-c", "Fun", "-d", "2026-11-04")
    assert "Warning: Fun budget exceeded: 105.00 of 100.00 for 2026-11" in out


def test_budget_only_for_expense_categories(cli):
    seed(cli)
    with pytest.raises(SystemExit) as exc:
        cli("budget", "set", "Salary", "100")
    assert "expense" in str(exc.value.code)


def test_budget_unknown_category(cli):
    with pytest.raises(SystemExit) as exc:
        cli("budget", "set", "Nope", "100")
    assert "not found" in str(exc.value.code)


def test_budget_update_and_remove(cli):
    seed(cli)
    cli("budget", "set", "Food", "100")
    cli("budget", "set", "Food", "200")
    assert "200.00" in cli("budget", "status", "-m", "2026-11")
    assert "removed" in cli("budget", "remove", "Food")
    assert "No budgets set" in cli("budget", "status")
    with pytest.raises(SystemExit) as exc:
        cli("budget", "remove", "Food")
    assert "No budget set" in str(exc.value.code)


# ---------- edit ----------

def test_edit_amount_and_note(cli):
    seed(cli)
    out = cli("edit", "2", "--amount", "15", "-n", "dinner")
    assert "Updated 2:" in out and "15.00" in out
    listing = cli("list")
    assert "dinner" in listing
    assert "lunch" not in listing
    assert "-15.00" in listing


def test_edit_clear_note(cli):
    seed(cli)
    cli("edit", "2", "-n", "")
    assert "lunch" not in cli("list")


def test_edit_date_and_account(cli):
    seed(cli)
    cli("edit", "2", "-d", "2026-10-31", "-a", "Bank")
    assert "lunch" in cli("list", "-m", "2026-10")
    assert "+2822.50" in cli("balance")   # Bank: 3000 - 45 - 120 - 12.50


def test_edit_new_category_keeps_type(cli):
    seed(cli)
    cli("edit", "2", "-c", "Restaurants")
    out = cli("report", "-m", "2026-11")
    assert "Restaurants" in out
    assert "Food" not in out


def test_edit_to_existing_category_of_other_type(cli):
    seed(cli)
    cli("edit", "2", "-c", "Salary")      # now counts as income
    assert "Total: +2847.50" in cli("list")   # 3000 + 12.50 - 45 - 120


def test_edit_errors_change_nothing(cli):
    seed(cli)
    with pytest.raises(SystemExit) as exc:
        cli("edit", "2")
    assert "Nothing to change" in str(exc.value.code)

    with pytest.raises(SystemExit) as exc:
        cli("edit", "999", "--amount", "1")
    assert "not found" in str(exc.value.code)

    with pytest.raises(SystemExit) as exc:
        cli("edit", "2", "--amount", "abc")
    assert "Error" in str(exc.value.code)

    with pytest.raises(SystemExit) as exc:
        cli("edit", "2", "-c", "Salary", "-t", "expense")
    assert "already exists" in str(exc.value.code)

    with pytest.raises(SystemExit) as exc:
        cli("edit", "2", "-t", "income")
    assert "--category" in str(exc.value.code)

    assert "lunch" in cli("list")         # transaction 2 is untouched


def test_edit_warns_about_budget(cli):
    seed(cli)
    cli("budget", "set", "Food", "20")
    out = cli("edit", "3", "-c", "Food")   # moves 45.00 into Food
    assert "Warning: Food budget exceeded" in out


# ---------- packaging ----------

def test_db_path_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("FINTRACKER_DB", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert tracker.db_path() == tmp_path / "fintracker" / "fintracker.db"


def test_db_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FINTRACKER_DB", str(tmp_path / "x.db"))
    assert tracker.db_path() == tmp_path / "x.db"


def test_connect_creates_missing_directory(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "dir" / "f.db"
    monkeypatch.setenv("FINTRACKER_DB", str(target))
    tracker.connect().close()
    assert target.exists()


def test_schema_is_packaged():
    assert "CREATE TABLE IF NOT EXISTS transactions" in tracker.read_schema()


def test_db_option(cli, tmp_path):
    other = tmp_path / "other.db"
    cli("--db", str(other), "add", "5", "-a", "Cash", "-c", "Food")
    assert other.exists()


def test_version(cli, capsys):
    with pytest.raises(SystemExit) as exc:
        cli("--version")
    assert exc.value.code == 0
    assert "fintracker 0.1.0" in capsys.readouterr().out
