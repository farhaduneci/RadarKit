"""HTTP for every runtime. One async `request`; the transport is picked once: stdlib urllib (in a thread) locally,
the Workers `fetch` inside a Cloudflare Python Worker. Tests swap it with `set_transport`."""
import asyncio, json, urllib.error, urllib.request

REQUESTS = 0  # requests sent by this process (attempts, retries included): callers diff it to track a budget
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
TIMEOUT = 20


class HTTPError(Exception):
    """A non-2xx answer that retrying did not fix. `dead` means the resource is gone for good (404/410)."""

    def __init__(self, url, status, body=""):
        self.url, self.status, self.body = url, status, body
        super().__init__(f"HTTP {status} for {url.split('?')[0]}: {body[:160]}")

    @property
    def dead(self):
        return self.status in (404, 410)


def in_workers():
    try:
        import workers  # noqa: F401  (only importable inside a Python Worker)
        return True
    except ImportError:
        return False


async def urllib_transport(method, url, body, headers, timeout):
    def go():
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
    return await asyncio.to_thread(go)


async def workers_transport(method, url, body, headers, timeout):
    from workers import fetch
    kw = {"method": method, "headers": headers}
    if body is not None:
        kw["body"] = body.decode() if isinstance(body, bytes) else body
    r = await fetch(url, **kw)
    return r.status, await r.text()


_transport = None


def set_transport(fn):
    """fn(method, url, body: bytes|None, headers, timeout) -> (status, text), async. None restores the default."""
    global _transport
    _transport = fn


def transport():
    return _transport or (workers_transport if in_workers() else urllib_transport)


def is_dead(e):
    """Gone for good (404/410, unknown host) rather than slow or flaky: not worth a retry."""
    if isinstance(e, HTTPError):
        return e.dead
    s = str(e)
    return "nodename nor servname" in s or "Name or service not known" in s


async def request(method, url, body=None, headers=None, timeout=TIMEOUT, retries=1):
    """Response text. A dict/list body is sent as JSON. Retries 429, 5xx and network errors; raises HTTPError otherwise."""
    h = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8", **(headers or {})}
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
        h.setdefault("Content-Type", "application/json")
    if isinstance(body, str):
        body = body.encode()
    global REQUESTS
    for attempt in range(retries + 1):
        last = attempt == retries
        REQUESTS += 1
        try:
            status, text = await transport()(method, url, body, h, timeout)
        except Exception as e:
            if last or is_dead(e):
                raise
            await asyncio.sleep(1 + attempt)
            continue
        if 200 <= status < 300:
            return text
        if not last and (status == 429 or status >= 500):
            await asyncio.sleep(2 * (attempt + 1))
            continue
        raise HTTPError(url, status, text)


async def get_text(url, **kw):
    return await request("GET", url, **kw)


async def get_json(url, **kw):
    return json.loads(await request("GET", url, **kw))


async def post_json(url, body, **kw):
    return json.loads(await request("POST", url, body, **kw))


async def gather(coros, limit=8):
    """Run coroutines with at most `limit` in flight; results in order, exceptions returned in place, not raised."""
    sem = asyncio.Semaphore(limit)

    async def one(c):
        async with sem:
            return await c
    return await asyncio.gather(*(one(c) for c in coros), return_exceptions=True)


def run(coro):
    """Sync entry for CLIs and scripts: run one coroutine to completion."""
    return asyncio.run(coro)
