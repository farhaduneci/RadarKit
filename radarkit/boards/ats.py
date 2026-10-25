"""Hosted applicant-tracking systems with a public board API: one async fetcher per ATS, token = the company's board."""
import html, json, re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..http import get_json, get_text
from .common import iso, json_ld, lazy, text


def gh_loc(j):
    """location.name, plus the "Job Posting Location" metadata some boards use for the real place."""
    extra = [v for m in j.get("metadata") or [] if "posting location" in (m.get("name") or "").lower()
             for v in (m["value"] if isinstance(m.get("value"), list) else [m.get("value")]) if v]
    return "; ".join(filter(None, [(j.get("location") or {}).get("name", "")] + extra))


async def gh_detail(tok, jid):
    j = await get_json(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs/{jid}")
    return {"desc": text(j.get("content")), "team": "; ".join(d["name"] for d in j.get("departments") or [] if d.get("name")) or None}


async def f_greenhouse(tok):
    """The list without content is a fraction of the size; each kept role's description is one more request."""
    d = await get_json(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs")
    return [{"id": f"gh:{tok}:{j['id']}", "title": j["title"], "url": j["absolute_url"], "loc": gh_loc(j),
             "date": iso(j.get("first_published") or j.get("updated_at")), "desc": None, "org": j.get("company_name"),
             "_detail": lazy(gh_detail, tok, j["id"])} for j in d.get("jobs", [])]


async def f_ashby(tok):
    rows = []
    for j in (await get_json(f"https://api.ashbyhq.com/posting-api/job-board/{tok}")).get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        rows.append({"id": f"ab:{tok}:{j['id']}", "title": j["title"], "url": j.get("jobUrl") or j.get("applyUrl"),
                     "loc": "; ".join(filter(None, locs)), "remote": j.get("isRemote") or j.get("workplaceType") == "Remote",
                     "date": iso(j.get("publishedAt")), "team": j.get("department") or j.get("team"),
                     "desc": j.get("descriptionPlain") or lazy(text, j.get("descriptionHtml"))})
    return rows


def lever_desc(j):
    lists = "\n\n".join(l.get("text", "") + "\n" + text(l.get("content")) for l in j.get("lists") or [])
    return "\n\n".join([j.get("descriptionPlain") or "", lists, j.get("additionalPlain") or ""])


async def f_lever(tok):
    try:
        js = await get_json(f"https://api.lever.co/v0/postings/{tok}?mode=json")
    except Exception:
        js = await get_json(f"https://api.eu.lever.co/v0/postings/{tok}?mode=json")
    rows = []
    for j in js:
        c = j.get("categories") or {}
        rows.append({"id": f"lv:{tok}:{j['id']}", "title": j["text"], "url": j["hostedUrl"],
                     "loc": "; ".join(c.get("allLocations") or [c.get("location") or ""]), "remote": j.get("workplaceType") == "remote",
                     "date": iso(j.get("createdAt")), "team": c.get("team") or c.get("department"), "desc": lazy(lever_desc, j)})
    return rows


async def f_recruitee(tok):
    d = await get_json(f"https://{tok}.recruitee.com/api/offers/")
    return [{"id": f"rc:{tok}:{j['id']}", "title": j["title"], "url": j["careers_url"],
             "loc": "; ".join(filter(None, [j.get("location"), j.get("country")])), "remote": j.get("remote"),
             "date": iso(j.get("published_at") or j.get("created_at")), "team": j.get("department"),
             "desc": lazy(text, (j.get("description") or "") + "\n" + (j.get("requirements") or "")), "org": j.get("company_name")}
            for j in d.get("offers", [])]


async def f_personio(tok):
    root = ET.fromstring(await get_text(f"https://{tok}/xml?language=en"))
    rows = []
    for p in root.findall("position"):
        g = lambda k, p=p: (p.findtext(k) or "").strip()
        desc = lazy(lambda p=p: "\n\n".join((d.findtext("name") or "") + "\n" + text(d.findtext("value"))
                                            for d in p.findall("jobDescriptions/jobDescription")))
        rows.append({"id": f"ps:{tok.split('.')[0]}:{g('id')}", "title": g("name"), "url": f"https://{tok}/job/{g('id')}",
                     "loc": "; ".join(filter(None, [g("office")] + [o.text or "" for o in p.findall("additionalOffices/office")])),
                     "date": iso(g("createdAt")), "team": g("department") or None, "desc": desc, "org": g("subcompany") or None})
    return rows


async def f_workable(tok):
    """The account's public widget feed (apply.workable.com/<account>): every open role with its description."""
    rows = []
    for j in (await get_json(f"https://apply.workable.com/api/v1/widget/accounts/{tok}?details=true")).get("jobs", []):
        locs = j.get("locations") or [{"city": j.get("city"), "country": j.get("country")}]
        rows.append({"id": f"wk:{tok}:{j['shortcode']}", "title": j["title"], "url": j.get("url") or j.get("shortlink"),
                     "loc": "; ".join(", ".join(filter(None, [l.get("city"), l.get("country")])) for l in locs),
                     "remote": str(j.get("telecommuting")).lower() == "true", "date": iso(j.get("published_on") or j.get("created_at")),
                     "team": j.get("department"), "desc": lazy(text, j.get("description"))})
    return rows


async def sr_detail(tok, pid):
    d = await get_json(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings/{pid}")
    secs = (d.get("jobAd") or {}).get("sections") or {}
    out = {"desc": "\n\n".join(f"{s.get('title', '')}\n{text(s.get('text'))}" for s in secs.values() if s.get("text"))}
    if d.get("postingUrl"):
        out["url"] = d["postingUrl"].split("?")[0]
    return out


async def f_smartrecruiters(tok, cutoff=None):
    """Public postings list, newest first (released after the cutoff, if one); the description is a second request per role."""
    rows, off = [], 0
    after = f"&releasedAfter={cutoff}T00:00:00.000Z" if cutoff else ""
    while off < 2000:
        d = await get_json(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings?limit=100&offset={off}{after}")
        for j in d.get("content", []):
            l = j.get("location") or {}
            rows.append({"id": f"sr:{tok}:{j['id']}", "title": j["name"], "url": f"https://jobs.smartrecruiters.com/{tok}/{j['id']}",
                         "loc": l.get("fullLocation") or ", ".join(filter(None, [l.get("city"), l.get("country")])),
                         "remote": bool(l.get("remote")), "date": iso(j.get("releasedDate")), "org": (j.get("company") or {}).get("name"),
                         "team": (j.get("department") or {}).get("label"), "_detail": lazy(sr_detail, tok, j["id"])})
        off += 100
        if off >= d.get("totalFound", 0):
            break
    return rows


async def join_detail(url):
    j = json_ld(await get_text(url))
    return {"desc": text(j.get("description")), "org": (j.get("hiringOrganization") or {}).get("name")}


async def f_join(tok):
    """join.com/companies/<token>: the list is in the page's __NEXT_DATA__; the description is on each job page's JSON-LD."""
    h = await get_text(f"https://join.com/companies/{tok}")
    m = re.search(r"__NEXT_DATA__[^>]*>(.*?)</script>", h, re.S)
    items = (((json.loads(m.group(1))["props"]["pageProps"].get("initialState") or {}).get("jobs") or {}).get("items")) if m else []
    rows = []
    for j in items or []:
        city = j.get("city") or {}
        url = f"https://join.com/companies/{tok}/{j['idParam']}"
        rows.append({"id": f"jn:{tok}:{j['id']}", "title": j["title"], "url": url,
                     "loc": ", ".join(filter(None, [city.get("cityName"), city.get("countryName") or (j.get("country") or {}).get("iso3166")])),
                     "remote": bool(j.get("remoteType")) or str(j.get("workplaceType")).upper() == "REMOTE",
                     "date": iso(j.get("createdAt")), "_detail": lazy(join_detail, url)})
    return rows


def teamtailor_rss(host, body):
    ns = {"tt": "https://teamtailor.com/locations"}
    root = ET.fromstring(body)
    org = root.findtext("channel/title")
    rows = []
    for it in root.findall("channel/item"):
        link = (it.findtext("link") or "").strip()
        m = re.search(r"/jobs/(\d+)-", link)
        if not m:
            continue
        locs = ["; ".join(filter(None, [l.findtext("tt:city", "", ns), l.findtext("tt:country", "", ns)])) for l in it.findall("tt:locations/tt:location", ns)]
        try:
            date = parsedate_to_datetime(it.findtext("pubDate")).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            date = None
        rows.append({"id": f"tt:{host.split('.')[0]}:{m.group(1)}", "title": html.unescape((it.findtext("title") or "").strip()), "url": link,
                     "loc": "; ".join(filter(None, locs)), "remote": it.findtext("remoteStatus") in ("fully", "remote"),
                     "date": date, "team": it.findtext("department"), "desc": lazy(text, it.findtext("description")), "org": org})
    return rows


async def tt_detail(url, slug):
    j = json_ld(await get_text(url))
    locs = [(l.get("address") or {}).get("addressLocality") or "" for l in j.get("jobLocation") or []]
    return {"title": html.unescape(j.get("title") or slug.replace("-", " ")), "loc": "; ".join(filter(None, locs)),
            "date": iso(j.get("datePosted")), "desc": text(j.get("description")), "org": (j.get("hiringOrganization") or {}).get("name")}


def teamtailor_rows(host, root_html):
    """Teamtailor with no feed: the root page links every posting; each posting carries JobPosting JSON-LD."""
    rows = []
    for jid, slug in dict.fromkeys(re.findall(r'href="https?://[^"/]+/jobs/(\d+)-([^"/?#]+)"', root_html)):
        url = f"https://{host}/jobs/{jid}-{slug}"
        rows.append({"id": f"tt:{host.split('.')[0]}:{jid}", "title": slug.replace("-", " "), "url": url, "loc": "", "date": None,
                     "desc": "", "_detail": lazy(tt_detail, url, slug)})
    return rows


async def f_teamtailor(host):
    """/jobs.rss lists every posting with description and location; sites without it link postings on the root page."""
    try:
        body = await get_text(f"https://{host}/jobs.rss")
        if body.lstrip().startswith("<?xml"):
            return teamtailor_rss(host, body)
    except Exception:
        pass
    return teamtailor_rows(host, await get_text(f"https://{host}/"))


async def f_gem(tok):
    """Gem job boards (jobs.gem.com/<board>): one list call with plain-text descriptions."""
    rows = []
    for j in await get_json(f"https://api.gem.com/job_board/v0/{tok}/job_posts/"):
        locs = [(o.get("location") or {}).get("name") for o in j.get("offices") or []] or [(j.get("location") or {}).get("name")]
        rows.append({"id": f"gem:{tok}:{j['id']}", "title": j["title"], "url": j["absolute_url"], "loc": "; ".join(filter(None, locs)),
                     "remote": j.get("location_type") == "remote", "date": iso(j.get("first_published_at") or j.get("created_at")),
                     "team": "; ".join(d["name"] for d in j.get("departments") or [] if d.get("name")) or None,
                     "desc": j.get("content_plain") or lazy(text, j.get("content"))})
    return rows
