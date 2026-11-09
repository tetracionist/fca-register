import asyncio

import pytest

pytest.importorskip("mcp")

from mcp.server.mcpserver.exceptions import ToolError

from fca_register import mcp_server


class FakeClient:
    async def search(self, name, limit):
        return [{"fca_frn": "123456", "name": name, "status": "Authorised", "limit": limit}]

    async def firm(self, frn, appointed_reps=False):
        if frn != "123456":
            raise LookupError(f"No firm found with FRN {frn}")
        return {"fca_frn": frn, "appointed_reps": [] if appointed_reps else None}


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    monkeypatch.setattr(mcp_server, "_client", FakeClient())


def test_lists_both_tools():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert {t.name for t in tools} == {"search_firms", "get_firm"}


def test_search_firms_passes_name_and_limit():
    result = asyncio.run(mcp_server.search_firms("Harbour", limit=5))
    assert result == [{"fca_frn": "123456", "name": "Harbour", "status": "Authorised", "limit": 5}]


def test_get_firm_unknown_frn_is_a_tool_error():
    with pytest.raises(ToolError, match="No firm found with FRN 999"):
        asyncio.run(mcp_server.mcp.call_tool("get_firm", {"frn": "999"}))


def test_missing_credentials_is_a_tool_error(monkeypatch):
    monkeypatch.setattr(mcp_server, "_client", None)
    monkeypatch.delenv("FCA_API_EMAIL", raising=False)
    monkeypatch.delenv("FCA_API_KEY", raising=False)
    with pytest.raises(ToolError, match="FCA credentials missing"):
        asyncio.run(mcp_server.mcp.call_tool("search_firms", {"name": "x"}))


def test_get_firm_passes_appointed_reps_flag():
    assert asyncio.run(mcp_server.get_firm("123456"))["appointed_reps"] is None
    result = asyncio.run(mcp_server.get_firm("123456", include_appointed_reps=True))
    assert result["appointed_reps"] == []
