"""Async client for the FCA Financial Services Register API."""
import asyncio
import fcntl
import json
import math
import os
import sys
import time

import requests

BASE = "https://register.fca.org.uk/services/V0.1"
# FCA limit is 50 requests per 10 seconds (subject to change); breaching it
# blocks the account for 1 minute. Stay a little under to absorb jitter.
MAX_REQUESTS = int(os.environ.get("FCA_MAX_REQUESTS", 45))
WINDOW = float(os.environ.get("FCA_WINDOW_SECONDS", 10))
BLOCK_SECONDS = 61
CONCURRENCY = 10  # requests in flight at once
STATE_FILE = os.path.expanduser("~/.cache/fca_rate_limit.json")


class FcaApiError(Exception):
    """The API answered (usually HTTP 200) with an error code in the body."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class FcaAuthError(FcaApiError, PermissionError):
    """Bad or missing API key or email (FSR-API-01-*)."""


class FcaBadRequestError(FcaApiError, ValueError):
    """The API rejected the input, e.g. a malformed FRN or search string."""


class FcaSystemError(FcaApiError, RuntimeError):
    """The FCA's side failed (FSR-API-99-*) or sent a code we don't know."""


# FSR-API-<api>-<endpoint>-<result>. Result 00 is success, 11 is "no
# record", 22 is "page not found"; 21 and 31 are bad input. The FCA docs
# swap 11 and 21 for Firm Requirements, so those two are listed by hand.
NOT_FOUND_CODES = {"FSR-API-02-06-21"}
BAD_REQUEST_CODES = {"FSR-API-02-06-11"}


def check_status(body):
    """Raise FcaApiError if the body carries an error code; return True if
    it says there is no data, False if it is a normal success."""
    code = body.get("Status") if isinstance(body, dict) else None
    if not isinstance(code, str) or not code.startswith("FSR-API-"):
        return False  # no code to go on; trust the HTTP status
    message = body.get("Message") or ""
    parts = code.split("-")
    api, result = parts[2], parts[-1]
    if code in BAD_REQUEST_CODES:
        raise FcaBadRequestError(code, message)
    if result == "00":
        return False
    if api == "01":
        raise FcaAuthError(code, message)
    if code in NOT_FOUND_CODES or result in ("11", "22"):
        return True
    if result in ("21", "31"):
        raise FcaBadRequestError(code, message)
    raise FcaSystemError(code, message)


class RateLimiter:
    """Sliding window shared by every fca_register process via a locked state file:
    never more than MAX_REQUESTS starts in any WINDOW, and nobody calls while
    a 429 block is in force."""

    def __init__(self, max_requests, window, path=STATE_FILE):
        self.max_requests = max_requests
        self.window = window
        self.path = path
        self._lock = None
        self._loop = None
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def _update(self, fn):
        """Run fn(state, now) under an exclusive file lock; save and return."""
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                state = json.loads(f.read() or "{}")
            except ValueError:
                state = {}
            now = time.time()
            state["starts"] = [
                t for t in state.get("starts", []) if now - t < self.window
            ]
            result = fn(state, now)
            f.seek(0)
            f.truncate()
            f.write(json.dumps(state))
            return result

    def _try_acquire(self, state, now):
        """Take a slot and return 0, or return how long to wait."""
        blocked_until = state.get("blocked_until", 0)
        if now < blocked_until:
            return blocked_until - now
        starts = state["starts"]
        if len(starts) < self.max_requests:
            starts.append(now)
            return 0
        return min(starts) + self.window - now

    @property
    def lock(self):
        # Made inside the running loop, and remade if the loop changes:
        # asyncio primitives bind to a loop (on Python 3.9, to whatever
        # loop exists when they are created).
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._lock, self._loop = asyncio.Lock(), loop
        return self._lock

    async def wait(self):
        async with self.lock:
            while True:
                delay = await asyncio.to_thread(self._update, self._try_acquire)
                if delay <= 0:
                    return
                await asyncio.sleep(delay)

    async def blocked(self):
        """Record a 429 so every process holds off for the block period."""
        def mark(state, now):
            state["blocked_until"] = max(
                state.get("blocked_until", 0), now + BLOCK_SECONDS
            )
        await asyncio.to_thread(self._update, mark)


class Client:
    def __init__(self, email=None, api_key=None):
        """Credentials default to FCA_API_EMAIL and FCA_API_KEY."""
        email = email or os.environ.get("FCA_API_EMAIL")
        api_key = api_key or os.environ.get("FCA_API_KEY")
        if not email or not api_key:
            raise ValueError(
                "FCA credentials missing: pass email and api_key, or set "
                "FCA_API_EMAIL and FCA_API_KEY"
            )
        self.session = requests.Session()
        self.session.headers.update({
            "X-Auth-Email": email,
            "X-Auth-Key": api_key,
            "Content-Type": "application/json",
        })
        self.limiter = RateLimiter(MAX_REQUESTS, WINDOW)
        self._in_flight = None
        self._loop = None

    @property
    def in_flight(self):
        # Per event loop, for the same reason as RateLimiter.lock.
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._in_flight, self._loop = asyncio.Semaphore(CONCURRENCY), loop
        return self._in_flight

    async def get_page(self, url, params=None):
        """Call the API once, back off on rate limits, return the JSON body."""
        for _ in range(3):
            async with self.in_flight:
                await self.limiter.wait()
                r = await asyncio.to_thread(
                    self.session.get, url, params=params, timeout=30
                )
            if r.status_code == 429:
                print(f"Rate limited; waiting {BLOCK_SECONDS}s", file=sys.stderr)
                await self.limiter.blocked()  # wait() then sleeps it out
                continue
            if r.status_code == 404:
                # Some sub-endpoints 404 when a firm has nothing to show.
                return {}
            r.raise_for_status()
            body = r.json()
            if check_status(body):
                return {}
            return body
        raise RuntimeError(f"Rate limited on {url}")

    async def get(self, path, params=None, max_rows=None):
        """Return the Data for path across pages, fetching pages concurrently.

        Most endpoints return a list; some (Permissions, AR) return a dict of
        lists, which are merged key by key. max_rows stops after enough pages.
        Returns (data, total_count).
        """
        params = dict(params or {})
        url = f"{BASE}/{path}"
        first = await self.get_page(url, params)
        data = first.get("Data") or []
        info = first.get("ResultInfo") or {}
        total = int(info.get("total_count") or 0)
        per_page = int(info.get("per_page") or 0)

        if info.get("Next") and total and per_page:
            wanted = total if not max_rows else min(total, max_rows)
            pages = range(2, math.ceil(wanted / per_page) + 1)
            bodies = await asyncio.gather(
                *(self.get_page(url, {**params, "pgnp": p}) for p in pages)
            )
            for body in bodies:
                merge(data, body.get("Data") or [])
        else:
            # No count to plan with: fall back to following Next one by one.
            next_url = info.get("Next")
            while next_url and not (max_rows and len(data) >= max_rows):
                body = await self.get_page(next_url)
                merge(data, body.get("Data") or [])
                next_url = (body.get("ResultInfo") or {}).get("Next")

        if max_rows and isinstance(data, list):
            data = data[:max_rows]
        return data, total

    async def search(self, name, limit=20):
        """Find firms by name. Returns FRN, name and status for each match."""
        rows, total = await self.get(
            "Search", {"q": name, "type": "firm"}, max_rows=limit or None
        )
        # The API's ordering shifts slightly between pages, so pages fetched
        # in parallel can repeat a row; keep the first of each.
        seen = set()
        matches = []
        for row in rows:
            frn = row.get("Reference Number")
            if frn in seen:
                continue
            seen.add(frn)
            matches.append({
                "fca_frn": frn,
                "name": row.get("Name"),
                "status": row.get("Status"),
            })
        print(f"{len(matches)} of {total or len(matches)} matches", file=sys.stderr)
        return matches

    async def firm(self, frn, appointed_reps=False):
        """Everything the playbook needs about one firm, fetched in parallel.

        Appointed representatives are skipped unless appointed_reps is True:
        a firm with a big AR network takes many pages to fetch.

        If a sub-endpoint fails, its field is None and the error is listed
        under "errors"; only a failure of the main firm record raises.
        """
        parts = {
            "details": "",
            "trading_names": "/Names",
            "permissions": "/Permissions",
            "individuals": "/Individuals",
            "addresses": "/Address",
            "disciplinary_history": "/DisciplinaryHistory",
            "requirements": "/Requirements",
        }
        if appointed_reps:
            parts["appointed_reps"] = "/AR"
        results = await asyncio.gather(
            *(self.get(f"Firm/{frn}{p}") for p in parts.values()),
            return_exceptions=True,
        )
        got = dict(zip(parts, results))
        details = got.pop("details")
        if isinstance(details, BaseException):
            raise details
        details = details[0]  # (data, total) -> data
        if not details:
            # The API answers 200 with an empty Data for an unknown FRN.
            raise LookupError(f"No firm found with FRN {frn}")

        errors = {}
        data = {}
        for key, result in got.items():
            if isinstance(result, Exception):
                errors[key] = f"{type(result).__name__}: {result}"
                data[key] = None
            elif isinstance(result, BaseException):
                raise result  # cancellation and the like
            else:
                data[key] = result[0]

        def tidied(key, fn):
            return None if data.get(key) is None else fn(data[key])

        return {
            "fca_frn": frn,
            "details": details[0],
            "trading_names": tidied("trading_names", tidy_trading_names),
            "permissions": tidied("permissions", tidy_permissions),
            "individuals": data["individuals"],
            # None unless asked for (or if the fetch failed).
            "appointed_reps": tidied("appointed_reps", tidy_appointed_reps),
            # Passed through as the API returns them (not yet tidied).
            "addresses": data["addresses"],
            "disciplinary_history": data["disciplinary_history"],
            "requirements": data["requirements"],
            "source_url": f"https://register.fca.org.uk/s/firm?id={frn}",
            "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "errors": errors,
        }


PERMISSION_FIELDS = {
    "Customer Type": "customer_types",
    "Investment Type": "investment_types",
    "Limitation": "limitations",
}


def tidy_permissions(raw):
    """Turn {activity: [{"Customer Type": [...]}, ...]} into one flat record
    per activity. "Limitation Not Found" just means no limitation, so it is
    dropped; any other unexpected key is kept in snake_case."""
    tidy = []
    for activity, items in (raw or {}).items():
        record = {
            "activity": activity,
            "customer_types": [],
            "investment_types": [],
            "limitations": [],
        }
        for item in items or []:
            for key, values in (item or {}).items():
                if key == "Limitation Not Found":
                    continue
                field = PERMISSION_FIELDS.get(key) or key.lower().replace(" ", "_")
                record.setdefault(field, []).extend(values or [])
        tidy.append(record)
    return tidy


def iso_date(value):
    """'28/03/2021' -> '2021-03-28'; anything else is passed through."""
    try:
        day, month, year = value.split("/")
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    except (AttributeError, ValueError):
        return value or None


def snake(key):
    return key.lower().replace(" ", "_")


def newest_first(records):
    """Current before previous, then most recent effective_from first."""
    by_date = sorted(records, key=lambda r: r.get("effective_from") or "", reverse=True)
    return sorted(by_date, key=lambda r: not r["current"])  # stable sort


def tidy_trading_names(raw):
    """[{"Current Names": [...]}, {"Previous Names": [...]}] -> flat list."""
    known = {"Name", "Status", "Effective From", "Effective To"}
    tidy = []
    for group in raw or []:
        for label, rows in (group or {}).items():
            current = label.lower().startswith("current")
            for row in rows or []:
                record = {
                    "name": row.get("Name"),
                    "type": (row.get("Status") or "").lower() or None,
                    "current": current,
                    "effective_from": iso_date(row.get("Effective From")),
                    "effective_to": iso_date(row.get("Effective To")),
                }
                record.update(
                    {snake(k): v for k, v in row.items() if k not in known}
                )
                tidy.append(record)
    return newest_first(tidy)


def tidy_appointed_reps(raw):
    """{"CurrentAppointedRepresentatives": [...], "Previous...": [...]} ->
    flat list. Principal FRN/name (always this firm), the API URL (just the
    FRN again) and the FCA's [NotinUse] field are dropped."""
    dropped = {"URL", "Principal FRN", "Principal Firm Name"}
    known = {
        "FRN", "Name", "RecordSubType", "Effective Date", "Termination Date",
        "Tied Agent", "EEA Tied Agent",
    }
    flag = lambda v: str(v).lower() == "true" if v is not None else None
    tidy = []
    for label, rows in (raw or {}).items():
        current = label.lower().startswith("current")
        for row in rows or []:
            frn = row.get("FRN")
            record = {
                "fca_frn": frn,
                "name": row.get("Name"),
                "type": (row.get("RecordSubType") or "").lower() or None,
                "current": current,
                "effective_from": iso_date(row.get("Effective Date")),
                "effective_to": iso_date(row.get("Termination Date")),
                "tied_agent": flag(row.get("Tied Agent")),
                "eea_tied_agent": flag(row.get("EEA Tied Agent")),
                "source_url": f"https://register.fca.org.uk/s/firm?id={frn}",
            }
            record.update({
                snake(k): v for k, v in row.items()
                if k not in known | dropped and not k.startswith("[NotinUse]")
            })
            tidy.append(record)
    return newest_first(tidy)


def merge(data, page):
    if isinstance(data, dict):
        for key, rows in (page or {}).items():
            data[key] = (data.get(key) or []) + (rows or [])
    else:
        data.extend(page)
