import pytest

pytest.importorskip("flask")

from fintracker import web  # noqa: E402


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("FINTRACKER_DB", str(tmp_path / "web.db"))
    application = web.create_app()
    application.testing = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def post(client, app, url, **data):
    data["csrf"] = app.config["CSRF_TOKEN"]
    return client.post(url, data=data, follow_redirects=True)


def add(client, app, amount="12.50", account="Cash", category="Food",
        kind="expense", day="2026-11-02", note=""):
    return post(client, app, "/transactions/add", amount=amount, account=account,
                category=category, type=kind, date=day, note=note, next="transactions")


def test_dashboard_empty(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Dashboard" in r.data


def test_rejects_missing_csrf(client):
    assert client.post("/transactions/add", data={"amount": "1"}).status_code == 400


def test_rejects_foreign_host(client):
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_add_and_list(client, app):
    r = add(client, app, note="lunch")
    assert b"lunch" in r.data
    assert b"12.50" in r.data
    dash = client.get("/?month=2026-11")
    assert b"Cash" in dash.data
    assert b"-12.50" in dash.data


def test_notes_are_escaped(client, app):
    add(client, app, note="<script>alert(1)</script>")
    page = client.get("/transactions").data
    assert b"<script>alert(1)</script>" not in page
    assert b"&lt;script&gt;" in page


def test_invalid_amount_shows_error(client, app):
    r = add(client, app, amount="abc")
    assert b"Invalid amount" in r.data
    assert b"No transactions." in client.get("/transactions").data


def test_existing_category_decides_type(client, app):
    add(client, app, amount="100", account="Bank", category="Salary", kind="income")
    add(client, app, amount="10", account="Bank", category="Salary", kind="expense")
    assert b"110.00" in client.get("/?month=2026-11").data   # both are income


def test_delete_transaction(client, app):
    add(client, app, note="lunch")
    r = post(client, app, "/transactions/1/delete")
    assert b"lunch" not in r.data
    assert b"Deleted transaction 1" in r.data


def test_budget_flow(client, app):
    add(client, app, amount="90", day="2026-11-01")
    r = post(client, app, "/budgets/set", category="Food", amount="100", month="2026-11")
    assert b"WARNING" in r.data
    assert b"90%" in client.get("/budgets?month=2026-11").data
    r = post(client, app, "/budgets/1/remove", month="2026-11")
    assert b"WARNING" not in r.data


def test_budget_errors(client, app):
    r = post(client, app, "/budgets/set", category="Nope", amount="10")
    assert b"not found" in r.data
    add(client, app, amount="100", account="Bank", category="Salary", kind="income")
    r = post(client, app, "/budgets/set", category="Salary", amount="10")
    assert b"expense categories" in r.data
