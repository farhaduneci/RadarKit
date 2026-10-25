import asyncio, json, os, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from radarkit import ai, http, notify, runlog, store, telegram, template  # noqa: E402
from radarkit import boards  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    return open(os.path.join(FIX, name)).read()


@pytest.fixture
def web():
    """Fake transport: first route whose key is in the URL answers; every call is recorded."""
    routes, calls = {}, []

    async def t(method, url, body, headers, timeout):
        calls.append((method, url, body))
        for k, v in routes.items():
            if k in url:
                r = v(url, body) if callable(v) else v
                return r if isinstance(r, tuple) else (200, r)
        return 404, "not found"
    http.set_transport(t)
    yield routes, calls
    http.set_transport(None)


def run(c):
    return asyncio.run(c)


def test_retry_on_5xx_then_ok(web):
    routes, calls = web
    answers = iter([(503, "busy"), (200, '{"ok": 1}')])
    routes["x.test"] = lambda u, b: next(answers)
    http_sleep = asyncio.sleep
    asyncio.sleep = lambda s: http_sleep(0)
    try:
        assert run(http.get_json("https://x.test/a")) == {"ok": 1}
    finally:
        asyncio.sleep = http_sleep
    assert len(calls) == 2


def test_404_is_dead_and_not_retried(web):
    routes, calls = web
    with pytest.raises(http.HTTPError) as e:
        run(http.get_text("https://gone.test/"))
    assert e.value.dead and len(calls) == 1


def test_workday_rows_and_lazy_detail(web):
    routes, calls = web
    routes["/jobs"] = fixture("workday.json")
    routes["/job/"] = fixture("workday_detail.json")
    rows = run(boards.fetch("workday", "nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite"))
    assert len(rows) == 2 and rows[0]["id"].startswith("wd:nvidia:JR")
    assert rows[0]["url"].startswith("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/")
    assert rows[0]["desc"] and "_detail" not in rows[0]


def test_workday_splits_by_facet_past_cap(web):
    routes, calls = web
    big = json.loads(fixture("workday.json"))
    big["total"] = 5000
    routes["/jobs"] = lambda u, b: json.dumps(big if not json.loads(b)["appliedFacets"] else dict(big, total=2))
    run(boards.fetch("workday", "x.wd1.myworkdayjobs.com/x/Ext", eager=False))
    facets = [json.loads(b)["appliedFacets"] for m, u, b in calls if m == "POST"]
    assert any(facets) and {k for f in facets for k in f} == {big["facets"][0]["facetParameter"]}


def test_amazon_searches_each_category(web):
    routes, calls = web
    routes["search.json"] = fixture("amazon.json")
    rows = run(boards.fetch("amazon", "software-development,machine-learning-science"))
    assert len(rows) == 2  # same jobs in both categories: deduped
    assert sum("category[]=" in u for _, u, _ in calls) == 2
    assert rows[0]["date"].startswith("20") and rows[0]["url"].startswith("https://www.amazon.jobs/en/jobs/")


def test_oracle_jibe_gem_pcsx_talentbrew_parse(web):
    routes, _ = web
    routes["recruitingCEJobRequisitions"] = fixture("oracle.json")
    routes["recruitingCEJobRequisitionDetails"] = '{"items": [{"ExternalDescriptionStr": "<p>Build</p>"}]}'
    routes["careers.amd.com/api/jobs"] = fixture("amd.json")
    routes["api.gem.com"] = fixture("gem.json")
    routes["pcsx/search"] = fixture("qualcomm.json")
    routes["position_details"] = '{"data": {"jobDescription": "<b>RTL</b>"}}'
    routes["search-jobs/results"] = lambda u, b: fixture("talentbrew.json") if "CurrentPage=1&" in u else '{"results": ""}'
    routes["careers.arm.com/job/"] = '<script type="application/ld+json">{"@type": "JobPosting", "description": "<p>Verify</p>"}</script>'
    o = run(boards.fetch("oracle", "eeho.fa.us2.oraclecloud.com/CX_45001"))
    assert len(o) == 2 and o[0]["desc"] == "Build" and "CX_45001/job/" in o[0]["url"]
    a = run(boards.fetch("jibe", "careers.amd.com"))
    assert len(a) == 2 and a[0]["id"].startswith("jibe:amd:")
    g = run(boards.fetch("gem", "groq"))
    assert len(g) == 2 and g[0]["desc"]
    q = run(boards.fetch("pcsx", "careers.qualcomm.com/qualcomm.com"))
    assert len(q) == 2 and q[0]["id"].startswith("pcsx:qualcomm:") and q[0]["desc"] == "RTL"
    t = run(boards.fetch("talentbrew", "careers.arm.com"))
    assert len(t) == 3 and t[0]["title"] and t[0]["loc"] and t[0]["desc"] == "Verify"


def test_microsoft_keeps_its_id_prefix(web):
    routes, _ = web
    routes["pcsx/search"] = fixture("qualcomm.json")
    rows = run(boards.fetch("microsoft", "Berlin", eager=False))
    assert rows[0]["id"].startswith("ms:")


def test_board_of():
    assert boards.board_of("https://job-boards.greenhouse.io/anthropic") == ("greenhouse", "anthropic")
    assert boards.board_of("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite") == \
        ("workday", "nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite")
    assert boards.board_of("https://jobs.gem.com/groq") == ("gem", "groq")


def test_store_migrate_and_query(tmp_path):
    (tmp_path / "0001_init.sql").write_text("-- postings\nCREATE TABLE p (id TEXT PRIMARY KEY, n INT);\nCREATE INDEX p_n ON p (n);")
    s = store.SqliteStore(":memory:")
    assert run(s.migrate(str(tmp_path))) == ["0001_init.sql"]
    assert run(s.migrate(str(tmp_path))) == []
    run(s.batch([("INSERT INTO p VALUES (?, ?)", ("a", 1)), ("INSERT INTO p VALUES (?, ?)", ("b", None))]))
    assert run(s.all("SELECT * FROM p ORDER BY id")) == [{"id": "a", "n": 1}, {"id": "b", "n": None}]


def test_runlog_health_flags_silence_and_failures(tmp_path):
    log = runlog.FileLog(str(tmp_path / "runs.jsonl"))
    run(log.append("poll", False))
    run(log.append("poll", False))
    flags = run(runlog.health(log, {"poll": 24, "report": 168}))
    assert "report: never ran" in flags and any("2 failed runs" in f for f in flags)
    s = runlog.StoreLog(store.SqliteStore(":memory:"))
    run(s.append("poll", True, n=3))
    assert run(s.recent("poll"))[0]["n"] == 3 and run(runlog.health(s, {"poll": 1})) == []


def test_template_fill_keeps_unknown_braces(tmp_path):
    (tmp_path / "hi.txt").write_text("Hi {name}, {n} new {unknown}\n")
    assert template.render(str(tmp_path), "hi", name="A", n=2) == "Hi A, 2 new {unknown}"


def test_telegram_redacts_and_chunks(web):
    routes, calls = web
    routes["api.telegram.org"] = (400, '{"ok": false, "error_code": 400, "description": "chat not found"}')
    with pytest.raises(telegram.TelegramError) as e:
        run(telegram.Bot("123:ABC").send_message(1, "x"))
    assert "123:ABC" not in str(e.value) and "chat not found" in str(e.value)
    assert all(len(c) <= 4096 for c in telegram.chunks("a" * 5000 + "\nb"))


def test_notify_telegram_and_webhook(web):
    routes, calls = web
    routes["api.telegram.org"] = '{"ok": true, "result": {}}'
    routes["hook.test"] = "ok"
    run(notify.deliver({"channel": "telegram"}, "hello", env={"TELEGRAM_BOT_TOKEN": "1:x", "TELEGRAM_CHAT_ID": "5"}))
    run(notify.deliver({"channel": "webhook", "url": "https://hook.test/"}, "hello", "match", env={}, n=1))
    assert json.loads(calls[0][2])["chat_id"] == "5" and json.loads(calls[1][2]) == {"event": "match", "text": "hello", "n": 1}
    with pytest.raises(ValueError):
        run(notify.deliver({"channel": "pigeon"}, "x"))


def test_ai_text_shapes():
    assert ai.text_of({"result": {"response": "a"}}) == "a"
    assert ai.text_of({"choices": [{"message": {"content": "b"}}]}) == "b"
    assert ai.text_of({"output": [{"type": "reasoning", "content": []}, {"content": [{"type": "output_text", "text": "c"}]}]}) == "c"
