"""Enterprise recruiting suites that host many companies: Workday, Oracle Recruiting Cloud, Jibe, TalentBrew (Radancy)."""
import html, re, urllib.parse

from ..http import gather, get_json, get_text, post_json
from .common import iso, json_ld, lazy, text

WORKDAY_CAP = 2000  # a Workday search never pages past this; bigger boards are split by their first facet


def wd_base(tok):
    host, tenant, site = tok.split("/", 2)
    return f"https://{host}/wday/cxs/{tenant}/{site}", f"https://{host}/en-US/{site}"


async def wd_detail(api, path):
    j = (await get_json(api + path)).get("jobPostingInfo") or {}
    locs = [j.get("location")] + list(j.get("additionalLocations") or [])
    return {"desc": text(j.get("jobDescription")), "date": iso(j.get("startDate")), "loc": "; ".join(filter(None, locs)) or None,
            "remote": "remote" in (j.get("remoteType") or "").lower() or None}


async def wd_search(api, facets, seen):
    """Every posting of one search: the first page gives the total, the rest are fetched a few at a time."""
    page = lambda off: post_json(api + "/jobs", {"limit": 20, "offset": off, "searchText": "", "appliedFacets": facets})
    first = await page(0)
    total = first.get("total") or 0
    pages = [first] + [p for p in await gather([page(o) for o in range(20, min(total, WORKDAY_CAP), 20)], limit=4) if isinstance(p, dict)]
    for d in pages:
        for p in d.get("jobPostings") or []:
            if p.get("externalPath"):
                seen.setdefault(p["externalPath"], p)
    return total, first.get("facets") or []


async def f_workday(tok):
    """Workday careers sites. Token: `<host>/<tenant>/<site>`, e.g. nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite.
    The list has title, path and a location summary; the description, date and full locations are one detail request per role."""
    api, web = wd_base(tok)
    seen = {}
    total, facets = await wd_search(api, {}, seen)
    if total >= WORKDAY_CAP and facets:  # split by the first facet (usually job family) so every role is reachable
        f = facets[0]
        for v in f.get("values") or []:
            await wd_search(api, {f["facetParameter"]: [v["id"]]}, seen)
    tenant = tok.split("/")[1]
    rows = []
    for path, p in seen.items():
        jid = (p.get("bulletFields") or [path.rsplit("_", 1)[-1]])[0]
        rows.append({"id": f"wd:{tenant}:{jid}", "title": p["title"], "url": web + path, "loc": p.get("locationsText") or "",
                     "date": None, "desc": None, "org": tenant.capitalize(), "_detail": lazy(wd_detail, api, path)})
    return rows


async def oracle_detail(host, site, rid):
    q = urllib.parse.quote(f'ById;Id="{rid}",siteNumber={site}', safe=";=,")
    d = await get_json(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder={q}")
    j = (d.get("items") or [{}])[0]
    return {"desc": "\n\n".join(text(j.get(k)) for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr") if j.get(k)),
            "team": j.get("Category") or j.get("JobFunction")}


async def f_oracle(tok):
    """Oracle Recruiting Cloud (Candidate Experience). Token: `<host>/<siteNumber>`, e.g. eeho.fa.us2.oraclecloud.com/CX_45001."""
    host, site = tok.split("/", 1)
    rows, off, total = [], 0, None
    while total is None or off < total:
        finder = f"findReqs;siteNumber={site},limit=100,offset={off},sortBy=POSTING_DATES_DESC"
        d = await get_json(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
                           f"&expand=requisitionList.secondaryLocations&finder={urllib.parse.quote(finder, safe=';=,')}")
        it = (d.get("items") or [{}])[0]
        total = it.get("TotalJobsCount") or 0
        reqs = it.get("requisitionList") or []
        for j in reqs:
            locs = [j.get("PrimaryLocation")] + [s.get("Name") for s in j.get("secondaryLocations") or []]
            rows.append({"id": f"orc:{site}:{j['Id']}", "title": j["Title"], "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j['Id']}",
                         "loc": "; ".join(filter(None, locs)), "date": iso(j.get("PostedDate")), "org": None,
                         "remote": "remote" in (j.get("WorkplaceType") or "").lower(), "desc": None,
                         "_detail": lazy(oracle_detail, host, site, j["Id"])})
        if not reqs:
            break
        off += len(reqs)
    return rows


async def f_jibe(host):
    """Jibe (iCIMS) careers sites with /api/jobs, e.g. careers.amd.com. Token: the host."""
    rows, page = [], 1
    while True:
        d = await get_json(f"https://{host}/api/jobs?page={page}&limit=100")
        jobs = [j.get("data") or {} for j in d.get("jobs") or []]
        for j in jobs:
            rows.append({"id": f"jibe:{host.split('.')[1] if host.count('.') > 1 else host}:{j['req_id']}", "title": j["title"],
                         "url": f"https://{host}/careers-home/jobs/{j['req_id']}", "loc": j.get("full_location") or j.get("location_name") or "",
                         "date": iso(j.get("posted_date")), "team": "; ".join(c.strip() for c in j.get("category") or [] if c and c.strip()) or None,
                         "remote": "remote" in (j.get("location_type") or "").lower(), "org": j.get("hiring_organization"),
                         "desc": lazy(lambda j=j: "\n\n".join(text(j.get(k)) for k in ("description", "responsibilities", "qualifications") if j.get(k)))})
        if not jobs or len(rows) >= (d.get("totalCount") or 0):
            break
        page += 1
    return rows


CARD = re.compile(r'<a[^>]*href="(/job/[^"]+)"[^>]*data-job-id="(\d+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]*href="/job/|$)', re.S)


async def tb_detail(url):
    j = json_ld(await get_text(url))
    return {"desc": text(j.get("description")), "date": iso(j.get("datePosted"))}


def talentbrew_rows(host, results_html):
    rows = []
    for path, jid, title, rest in CARD.findall(results_html):
        loc = re.search(r'class="[^"]*location[^"]*"[^>]*>(.*?)<', rest, re.S)
        cat = re.search(r'class="[^"]*category[^"]*"[^>]*>(.*?)<', rest, re.S)
        rows.append({"id": f"tb:{host.split('.')[1] if host.count('.') > 1 else host}:{jid}", "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
                     "url": f"https://{host}{path}", "loc": html.unescape(loc.group(1)).strip() if loc else "", "date": None, "desc": None,
                     "team": html.unescape(cat.group(1)).strip() if cat else None, "_detail": lazy(tb_detail, f"https://{host}{path}")})
    return rows


async def f_talentbrew(host):
    """TalentBrew (Radancy) careers sites, e.g. careers.arm.com: HTML results, 100 a page; JD from each job page's JSON-LD. Token: the host."""
    rows, page = {}, 1
    while page < 100:
        d = await get_json(f"https://{host}/search-jobs/results?ActiveFacetID=0&CurrentPage={page}&RecordsPerPage=100"
                           "&SearchResultsModuleName=Search+Results&SearchFiltersModuleName=Search+Filters&SortCriteria=0&SortDirection=0&SearchType=5")
        got = talentbrew_rows(host, d.get("results") or "")
        new = [r for r in got if r["id"] not in rows]
        rows.update((r["id"], r) for r in new)
        if not new:
            break
        page += 1
    return list(rows.values())
