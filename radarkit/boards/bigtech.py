"""Big-tech careers sites with their own search: Apple, Google, Eightfold PCSX (Microsoft, Qualcomm), Amazon.
Tokens: a location, or `*` for the whole company. Google also takes a raw query (`company=DeepMind`)."""
import json, re, urllib.parse

from ..http import get_json, get_text
from .common import find_key, iso, lazy, ssr_json, text, us_date

GLOBAL = ("", "*")


def apple_rows(res):
    return [{"id": f"apple:{j['positionId']}", "title": j["postingTitle"], "loc": "; ".join(l.get("name", "") for l in j.get("locations") or []),
             "url": f"https://jobs.apple.com/en-gb/details/{j['positionId']}/{j.get('transformedPostingTitle', '')}",
             "date": (j.get("postDateInGMT") or "")[:19] or None, "desc": j.get("jobSummary") or "", "org": "Apple",
             "team": (j.get("team") or {}).get("teamName")} for j in res]


async def f_apple(loc):
    """jobs.apple.com search; the page embeds each result page as JSON. Token: the location slug in its URL (`germany-DEU`) or `*`."""
    rows, page = [], 1
    q = "" if loc in GLOBAL else f"location={loc}&"
    while True:
        pg = await get_text(f"https://jobs.apple.com/en-gb/search?{q}page={page}")
        res = find_key(ssr_json(pg, "searchResults") or {}, "searchResults")
        if not res or not res["searchResults"]:
            break
        rows += apple_rows(res["searchResults"])
        if len(rows) >= res.get("totalRecords", 0):
            break
        page += 1
    return rows


def google_rows(jobs):
    return [{"id": f"google:{j[0]}", "title": j[1], "url": f"https://www.google.com/about/careers/applications/jobs/results/{j[0]}",
             "loc": "; ".join(l[0] for l in j[9] or []), "date": iso((j[12] or [None])[0]),
             "desc": "\n\n".join(text(x[1]) for x in (j[3], j[4], j[10]) if x and x[1]), "org": j[7] or "Google"} for j in jobs]


async def f_google(tok):
    """Google careers search: results ride in the page's ds:1 block, 20 a page. Token: a location, `*`, or a query like `company=DeepMind`."""
    q = "" if tok in GLOBAL else tok + "&" if "=" in tok else f"location={urllib.parse.quote(tok)}&"
    rows, page = [], 1
    while True:
        pg = await get_text(f"https://www.google.com/about/careers/applications/jobs/results/?{q}page={page}")
        m = re.search(r"AF_initDataCallback\(\{key: 'ds:1', hash: '[^']*', data:(.*?), sideChannel", pg, re.S)
        data = json.loads(m.group(1)) if m else None
        if not data or not data[0]:
            break
        rows += google_rows(data[0])
        if len(rows) >= (data[2] or 0):
            break
        page += 1
    return rows


async def pcsx_desc(host, domain, pid):
    d = await get_json(f"https://{host}/api/pcsx/position_details?position_id={pid}&domain={domain}&hl=en")
    return text(d["data"].get("jobDescription"))


def pcsx_rows(host, domain, positions):
    pre = "ms" if domain == "microsoft.com" else f"pcsx:{domain.split('.')[0]}"
    return [{"id": f"{pre}:{p['id']}", "title": p["name"], "url": f"https://{host}{p['positionUrl']}", "loc": "; ".join(p.get("locations") or []),
             "remote": p.get("workLocationOption") == "remote", "date": iso(p.get("postedTs")), "team": p.get("department"),
             "org": domain.split(".")[0].capitalize(), "desc": lazy(pcsx_desc, host, domain, p["id"])} for p in positions]


async def f_pcsx(tok):
    """Eightfold PCSX careers sites. Token: `<host>/<domain>[/<location>]`, e.g. careers.qualcomm.com/qualcomm.com."""
    host, domain, loc = (tok.split("/", 2) + [""])[:3]
    loc = "" if loc in GLOBAL else loc
    rows, start = [], 0
    while True:
        d = (await get_json(f"https://{host}/api/pcsx/search?domain={domain}&query=&location={urllib.parse.quote(loc)}&start={start}&sort_by=timestamp"))["data"]
        rows += pcsx_rows(host, domain, d["positions"])
        start += len(d["positions"])
        if not d["positions"] or start >= (d.get("count") or 0):
            break
    return rows


async def f_microsoft(loc):
    """apply.careers.microsoft.com (PCSX). Token: a location, e.g. Berlin, or `*`."""
    return await f_pcsx(f"apply.careers.microsoft.com/microsoft.com/{loc}")


AMAZON = "https://www.amazon.jobs"


def amazon_rows(jobs):
    return [{"id": f"amazon:{j['id_icims']}", "title": j["title"], "url": AMAZON + j["job_path"],
             "loc": j.get("normalized_location") or j.get("location") or "", "date": us_date(j.get("posted_date")),
             "team": j.get("job_category"), "org": j.get("company_name") or "Amazon",
             "desc": lazy(lambda j=j: "\n\n".join(text(j.get(k)) for k in ("description", "basic_qualifications", "preferred_qualifications") if j.get(k)))}
            for j in jobs]


async def f_amazon(tok):
    """amazon.jobs search.json, 100 a page. A search stops at 10000 hits, so the token lists categories, each searched on
    its own: `software-development,machine-learning-science`, or `*` for one capped search."""
    rows = {}
    for cat in [c for c in tok.split(",") if c] if tok not in GLOBAL else [""]:
        q = f"&category[]={cat}" if cat else ""
        off = 0
        while off < 10000:
            d = await get_json(f"{AMAZON}/en/search.json?result_limit=100&offset={off}&sort=recent{q}")
            for r in amazon_rows(d.get("jobs") or []):
                rows[r["id"]] = r
            off += 100
            if not d.get("jobs") or off >= d.get("hits", 0):
                break
    return list(rows.values())
