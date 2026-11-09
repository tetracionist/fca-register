import asyncio

import pytest
import requests

from fca_register import client as client_mod
from fca_register import Client


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("FCA_API_EMAIL", "a@b.c")
    monkeypatch.setenv("FCA_API_KEY", "k")
    monkeypatch.setattr(client_mod, "STATE_FILE", str(tmp_path / "rate.json"))

    def make(responses):
        """responses maps a URL suffix to a FakeResponse."""
        c = Client()
        c.limiter.path = str(tmp_path / "rate.json")

        def fake_get(url, params=None, timeout=None):
            for suffix, resp in responses.items():
                if url.endswith(suffix):
                    return resp
            return FakeResponse(200, {"Data": []})

        c.session.get = fake_get
        return c

    return make


def test_client_made_outside_event_loop_works_inside_one(make_client):
    # On Python 3.9, asyncio primitives made outside asyncio.run bind to
    # the wrong loop, so the client must create them lazily.
    c = make_client({"Firm/1": FakeResponse(200, {"Data": [{"x": 1}]})})
    data, _ = asyncio.run(c.get("Firm/1"))
    assert data == [{"x": 1}]


def test_client_can_be_reused_across_event_loops(make_client):
    c = make_client({"Firm/1": FakeResponse(200, {"Data": [{"x": 1}]})})
    for _ in range(2):
        firm = asyncio.run(c.firm("1"))
        assert firm["errors"] == {}


def test_404_counts_as_empty(make_client):
    c = make_client({"Firm/1/Requirements": FakeResponse(404)})
    data, total = asyncio.run(c.get("Firm/1/Requirements"))
    assert (data, total) == ([], 0)


def test_one_failing_sub_endpoint_keeps_the_rest(make_client):
    c = make_client({
        "Firm/1": FakeResponse(200, {"Data": [{"Organisation Name": "X"}]}),
        "Firm/1/DisciplinaryHistory": FakeResponse(500),
    })
    firm = asyncio.run(c.firm("1"))
    assert firm["details"] == {"Organisation Name": "X"}
    assert firm["disciplinary_history"] is None
    assert "500" in firm["errors"]["disciplinary_history"]


def test_failing_details_still_raises(make_client):
    c = make_client({"Firm/1": FakeResponse(500)})
    with pytest.raises(requests.HTTPError):
        asyncio.run(c.firm("1"))


def test_appointed_reps_are_opt_in(make_client):
    c = make_client({"Firm/1": FakeResponse(200, {"Data": [{"Organisation Name": "X"}]})})
    seen = []
    real_get = c.session.get

    def spy(url, params=None, timeout=None):
        seen.append(url)
        return real_get(url, params=params, timeout=timeout)

    c.session.get = spy
    firm = asyncio.run(c.firm("1"))
    assert not any(u.endswith("/AR") for u in seen)
    assert firm["appointed_reps"] is None

    seen.clear()
    asyncio.run(c.firm("1", appointed_reps=True))
    assert any(u.endswith("/AR") for u in seen)
