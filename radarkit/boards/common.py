"""Helpers every fetcher shares: HTML to text, dates, embedded JSON, lazy fields."""
import html, json, re, time
from datetime import datetime


def text(h):
    """HTML to plain text, keeping paragraph breaks."""
    h = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", html.unescape(h or ""), flags=re.I)
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def iso(v):
    """Epoch seconds/ms or an ISO-ish string -> 'YYYY-MM-DDTHH:MM:SS', else None."""
    if isinstance(v, (int, float)):
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(v / 1000 if v > 1e11 else v))
    return (v or "")[:19] or None


def us_date(s):
    """'October 15, 2026' -> '2026-10-15T00:00:00'."""
    try:
        return datetime.strptime((s or "").strip(), "%B %d, %Y").strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def lazy(fn, *a):
    """A field computed only for rows the caller keeps (HTML stripping or a detail request per role is the cost)."""
    return lambda: fn(*a)


def ssr_json(page, pat):
    """The JSON a server-rendered page embeds as a JS string literal: the first literal whose payload holds `pat`."""
    for m in re.finditer(r'JSON\.parse\("((?:[^"\\]|\\.)*)"\)', page):
        raw = json.loads('"' + m.group(1) + '"')
        if pat in raw:
            return json.loads(raw)


def find_key(o, key):
    """First dict, depth first, that has `key`."""
    if isinstance(o, dict):
        if key in o:
            return o
        for v in o.values():
            r = find_key(v, key)
            if r:
                return r


def json_ld(page):
    """The JobPosting JSON-LD block of a job page, else {}."""
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            j = json.loads(m.group(1))
        except ValueError:
            continue
        for o in j if isinstance(j, list) else [j]:
            if isinstance(o, dict) and o.get("@type") == "JobPosting":
                return o
    return {}
