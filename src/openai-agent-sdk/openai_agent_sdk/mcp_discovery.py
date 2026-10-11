"""Start the pinned server and validate discovery without provider calls."""

import asyncio

from .openwebninja_mcp import open_search_session


async def check_discovery() -> None:
    async with open_search_session():
        pass
    print("Validated jsearch.search_v2; connection closed. No provider call made.")


def main() -> None:
    asyncio.run(check_discovery())


if __name__ == "__main__":
    main()
