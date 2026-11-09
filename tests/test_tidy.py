import pytest

from fca_register import Client
from fca_register.client import iso_date, tidy_permissions, tidy_trading_names


def test_iso_date():
    assert iso_date("28/3/2021") == "2021-03-28"
    assert iso_date("") is None
    assert iso_date("n/a") == "n/a"


def test_tidy_permissions_drops_limitation_not_found():
    raw = {"Arranging": [{"Customer Type": ["Retail"]}, {"Limitation Not Found": ["x"]}]}
    assert tidy_permissions(raw) == [{
        "activity": "Arranging",
        "customer_types": ["Retail"],
        "investment_types": [],
        "limitations": [],
    }]


def test_trading_names_current_first():
    raw = [
        {"Previous Names": [{"Name": "Old", "Effective From": "01/01/2020"}]},
        {"Current Names": [{"Name": "New", "Status": "Trading", "Effective From": "01/01/2010"}]},
    ]
    assert [r["name"] for r in tidy_trading_names(raw)] == ["New", "Old"]


def test_client_needs_credentials(monkeypatch):
    monkeypatch.delenv("FCA_API_EMAIL", raising=False)
    monkeypatch.delenv("FCA_API_KEY", raising=False)
    with pytest.raises(ValueError):
        Client()


def test_firm_fetches_every_endpoint(monkeypatch):
    import asyncio

    monkeypatch.setenv("FCA_API_EMAIL", "a@b.c")
    monkeypatch.setenv("FCA_API_KEY", "k")
    client = Client()
    seen = []

    async def fake_get(path, params=None, max_rows=None):
        seen.append(path)
        if path == "Firm/1":
            return [{"Organisation Name": "X"}], 1
        if path.endswith(("/Permissions", "/AR")):
            return {}, 0
        if path.endswith("/Names"):
            return [], 0
        return [{"path": path}], 1

    monkeypatch.setattr(client, "get", fake_get)
    firm = asyncio.run(client.firm("1"))
    for suffix in ["/Address", "/DisciplinaryHistory", "/Requirements"]:
        assert f"Firm/1{suffix}" in seen
    assert firm["addresses"] == [{"path": "Firm/1/Address"}]
    assert firm["disciplinary_history"] == [{"path": "Firm/1/DisciplinaryHistory"}]
    assert firm["requirements"] == [{"path": "Firm/1/Requirements"}]
