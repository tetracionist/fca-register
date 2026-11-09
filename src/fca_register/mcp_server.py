"""MCP server exposing the FCA Register client as two tools over stdio.

Run with `fca-register-mcp` (needs the `mcp` extra and Python 3.10+).
Needs FCA_API_EMAIL and FCA_API_KEY in the environment.
"""
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .client import Client

mcp = MCPServer("fca-register")
_client = None


def client():
    # Created on first use so missing credentials surface as a tool error
    # rather than stopping the server from starting.
    global _client
    if _client is None:
        _client = Client()
    return _client


@mcp.tool()
async def search_firms(name: str, limit: int = 20) -> list[dict]:
    """Search the FCA Register for firms by name.

    Returns each match's FCA reference number (fca_frn), name and status.
    limit caps the number of matches; 0 returns every match.
    """
    try:
        return await client().search(name, limit)
    except (LookupError, ValueError) as e:
        raise ToolError(str(e)) from e


@mcp.tool()
async def get_firm(frn: str) -> dict:
    """Get one firm's full FCA Register record by its reference number (FRN).

    Includes details, trading names, permissions, individuals, appointed
    representatives, addresses, disciplinary history and requirements.
    """
    try:
        return await client().firm(frn)
    except (LookupError, ValueError) as e:
        # MCP only passes ToolError messages back to the model.
        raise ToolError(str(e)) from e


def run():
    mcp.run()


if __name__ == "__main__":
    run()
