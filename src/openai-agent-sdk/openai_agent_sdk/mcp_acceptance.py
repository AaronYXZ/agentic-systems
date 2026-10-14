"""Explicitly enabled provider acceptance check. Makes no model calls."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from time import perf_counter

from dotenv import load_dotenv

from .contracts import PROJECT_ROOT
from .jsearch import search_jsearch
from .openwebninja_mcp import open_search_session


async def check_mcp(query: str) -> dict:
    """Report metadata only after the connection has closed."""
    started = perf_counter()
    try:
        async with open_search_session(os.environ["OPENWEBNINJA_API_KEY"]) as adapter:
            result = await adapter.search(query)
        return {
            "outcome": "success",
            "jobs": len(result["jobs"]),
            "job_fields": sorted(result["jobs"][0]) if result["jobs"] else [],
            "connection_closed": True,
            "duration_seconds": round(perf_counter() - started, 3),
        }
    except Exception as error:
        # Provider and startup errors can contain secrets. Record only the class.
        return {
            "outcome": "error",
            "error_class": type(error).__name__,
            "duration_seconds": round(perf_counter() - started, 3),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow one paid MCP search")
    parser.add_argument(
        "--compare-direct",
        action="store_true",
        help="Also allow one direct RapidAPI search",
    )
    parser.add_argument("--query", default="Python engineer in US")
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "data" / "mcp-acceptance.json"
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to enable provider calls that may consume quota.")
    load_dotenv(PROJECT_ROOT / ".env")
    keys = ["OPENWEBNINJA_API_KEY"]
    if args.compare_direct:
        keys.append("JSEARCH_API_KEY")
    for key in keys:
        if not os.getenv(key, "").strip():
            parser.error(f"{key} is missing.")
    report = {"mcp": asyncio.run(check_mcp(args.query))}
    if args.compare_direct:
        started = perf_counter()
        try:
            result = search_jsearch(args.query, api_key=os.environ["JSEARCH_API_KEY"])
            report["direct"] = {
                "outcome": "success",
                "jobs": len(result["jobs"]),
                "job_fields": sorted(result["jobs"][0]) if result["jobs"] else [],
                "duration_seconds": round(perf_counter() - started, 3),
            }
        except Exception as error:
            report["direct"] = {"outcome": "error", "error_class": type(error).__name__}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Acceptance metadata saved to {args.output}")
    if any(item["outcome"] != "success" for item in report.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
