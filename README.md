# fca-register

Async Python client and command-line tool for the
[FCA Financial Services Register API](https://register.fca.org.uk/Developer/s/).

It stays under the FCA rate limit (50 requests per 10 seconds) with a sliding
window that every process on the machine shares through a locked file in
`~/.cache`, so parallel runs don't get the account blocked.
It runs on POSIX systems only, because the lock uses `fcntl`.

## Install

```sh
pip install fca-register
```

## Credentials

Register for an API key on the FCA developer portal, then set:

```sh
export FCA_API_EMAIL=you@example.com
export FCA_API_KEY=your-key
```

## CLI

```sh
fca-register search "Harbour Underwriting" [--limit N]   # --limit 0 = all
fca-register firm 123456
```

Both print JSON to stdout. `python -m fca_register` works too.

## Library

```python
import asyncio
from fca_register import Client

async def main():
    client = Client()  # or Client(email=..., api_key=...)
    matches = await client.search("Harbour Underwriting", limit=5)
    firm = await client.firm(matches[0]["fca_frn"])
    print(firm["permissions"])

asyncio.run(main())
```

## Settings

| Variable             | Default | Meaning                         |
| -------------------- | ------- | ------------------------------- |
| `FCA_MAX_REQUESTS`   | 45      | Requests allowed per window     |
| `FCA_WINDOW_SECONDS` | 10      | Window length in seconds        |
