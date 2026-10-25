"""Careers-board fetchers: read public job-board endpoints, return canonical posting rows.

Row: {id, title, url, loc, date, desc, remote?, org?, team?}. A board is `<ats>:<token>`; `await fetch(ats, token)` is the
entry point (`fetch_sync` for scripts). A new board type is one async `f_<name>(token)` plus a FETCHERS entry and a test.
List calls stay cheap: a row may carry a lazy `desc` or a `_detail` hook (one request per role), resolved only on demand.
"""
import inspect, re
from urllib.parse import urlparse

from ..http import gather, is_dead, run  # noqa: F401  (is_dead re-exported for callers)
from . import ats, bigtech, enterprise

FETCHERS = {
    "greenhouse": ats.f_greenhouse, "ashby": ats.f_ashby, "lever": ats.f_lever, "recruitee": ats.f_recruitee,
    "personio": ats.f_personio, "workable": ats.f_workable, "smartrecruiters": ats.f_smartrecruiters, "join": ats.f_join,
    "teamtailor": ats.f_teamtailor, "gem": ats.f_gem,
    "apple": bigtech.f_apple, "google": bigtech.f_google, "microsoft": bigtech.f_microsoft, "pcsx": bigtech.f_pcsx,
    "amazon": bigtech.f_amazon,
    "workday": enterprise.f_workday, "oracle": enterprise.f_oracle, "jibe": enterprise.f_jibe, "talentbrew": enterprise.f_talentbrew,
}


async def _value(v):
    v = v() if callable(v) else v
    return await v if inspect.isawaitable(v) else v


async def resolve(row):
    """Fill a row's lazy fields in place: `_detail` (merged, empty values ignored) then a callable `desc`."""
    if row.get("_detail"):
        try:
            row.update({k: v for k, v in (await _value(row["_detail"])).items() if v})
        except Exception:
            pass
    row.pop("_detail", None)
    if callable(row.get("desc")):
        row["desc"] = await _value(row["desc"])
    return row


async def resolve_all(rows, limit=8):
    await gather([resolve(r) for r in rows], limit)
    return rows


async def fetch(ats_name, tok, cutoff=None, eager=True, limit=8):
    """Rows for one board. eager: every lazy field is resolved here (one request per role where the list lacks it).
    eager=False leaves them for a caller that filters first, then calls `resolve` on what it keeps."""
    f = FETCHERS[ats_name]
    rows = await (f(tok, cutoff) if ats_name == "smartrecruiters" else f(tok))
    return await resolve_all(rows, limit) if eager else rows


def fetch_sync(ats_name, tok, cutoff=None, eager=True):
    return run(fetch(ats_name, tok, cutoff, eager))


def board_of(url):
    """(ats, token) for a URL on a recognisable board, else None."""
    u = urlparse(url if "://" in url else "https://" + url)
    h, p = u.netloc.lower(), [x for x in u.path.split("/") if x]
    if "greenhouse.io" in h:
        if "embed" in p or not p:
            m = re.search(r"for=([\w-]+)", u.query)
            return ("greenhouse", m.group(1)) if m else None
        return ("greenhouse", p[0]) if p[0] not in ("v1", "boards") else None
    if "ashbyhq.com" in h and p:
        return ("ashby", p[0])
    if h.endswith("lever.co") and p and h.split(".")[0] in ("jobs", "api"):
        return ("lever", p[0])
    if h.endswith("recruitee.com") and h.split(".")[0] not in ("www", "app"):
        return ("recruitee", h.split(".")[0])
    m = re.match(r"([\w-]+)\.jobs\.personio\.(de|com)$", h)
    if m:
        return ("personio", m.group(1) + "." + m.group(2))
    if h.endswith("smartrecruiters.com") and h.split(".")[0] in ("jobs", "careers") and p:
        return ("smartrecruiters", p[0])
    if h == "join.com" and len(p) >= 2 and p[0] == "companies":
        return ("join", p[1])
    if h == "apply.workable.com" and p and p[0] not in ("j", "api"):
        return ("workable", p[0])
    if h == "jobs.gem.com" and p:
        return ("gem", p[0])
    if h.endswith(".myworkdayjobs.com") and p:
        site = p[1] if re.match(r"^[a-z]{2}-[A-Z]{2}$", p[0]) and len(p) > 1 else p[0]
        return ("workday", f"{h}/{h.split('.')[0]}/{site}")
    if h.endswith(".teamtailor.com") and h.split(".")[0] not in ("www", "app", "career"):
        return ("teamtailor", h)
    if re.match(r"^/jobs/\d{6,}-[a-z0-9-]+/?$", u.path.lower()) and h and not h.endswith(("wellfound.com", "teamtailor.com")):
        return ("teamtailor", h)  # employer's own domain on Teamtailor (careers.<co>.com/jobs/8493087-title)
    return None
