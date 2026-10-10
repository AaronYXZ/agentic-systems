"""Start the pinned server and validate discovery without provider calls."""

import asyncio

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from .openwebninja_mcp import OpenWebNinjaAdapter, server_parameters


async def check_discovery() -> None:
    async with stdio_client(server_parameters()) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=15)
            await OpenWebNinjaAdapter.discover(session)
    print("Validated jsearch.search_v2; connection closed. No provider call made.")


def main() -> None:
    asyncio.run(check_discovery())


if __name__ == "__main__":
    main()
