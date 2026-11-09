"""FCA Register lookup.

Usage:
  fca-register search "Harbour Underwriting" [--limit N]
  fca-register firm 123456 [--with-ar]

search returns the first 20 matches unless --limit says otherwise
(--limit 0 means every match). The total is printed to stderr.
firm skips appointed representatives unless --with-ar is given, since a
large AR network can take many requests to fetch.

Needs FCA_API_EMAIL and FCA_API_KEY in the environment.
"""
import asyncio
import json
import sys

from .client import Client, FcaApiError


async def main(argv):
    args = argv[1:]
    limit = 20
    with_ar = "--with-ar" in args
    if with_ar:
        args.remove("--with-ar")
    if "--limit" in args:
        i = args.index("--limit")
        try:
            limit = int(args[i + 1])
        except (IndexError, ValueError):
            sys.exit(__doc__)
        del args[i : i + 2]
    if len(args) != 2 or args[0] not in ("search", "firm"):
        sys.exit(__doc__)
    command, arg = args
    try:
        client = Client()
        if command == "search":
            result = await client.search(arg, limit)
        else:
            result = await client.firm(arg, appointed_reps=with_ar)
    except (LookupError, ValueError, FcaApiError) as e:
        sys.exit(str(e))
    print(json.dumps(result, indent=2))


def run():
    asyncio.run(main(sys.argv))


if __name__ == "__main__":
    run()
