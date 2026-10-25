"""Every run leaves one record; a health check reads them back. Records go to a JSONL file or to a Store table, so the
same "did this routine go quiet / start failing?" question works locally and on Cloudflare."""
import calendar, json, os, time

SCHEMA = "CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, ts TEXT, job TEXT, ok INTEGER, data TEXT)"


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def hours_since(ts):
    return (time.time() - calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))) / 3600


class FileLog:
    def __init__(self, path):
        self.path = path

    async def append(self, job, ok, **data):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        rec = {"ts": now(), "job": job, "ok": bool(ok), **data}
        with open(self.path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    async def recent(self, job=None, limit=200):
        if not os.path.exists(self.path):
            return []
        rows = [json.loads(l) for l in open(self.path) if l.strip()]
        return [r for r in rows if job is None or r.get("job") == job][-limit:]


class StoreLog:
    def __init__(self, store):
        self.store = store

    async def append(self, job, ok, **data):
        await self.store.run(SCHEMA)
        rec = {"ts": now(), "job": job, "ok": bool(ok), **data}
        await self.store.run("INSERT INTO runs (ts, job, ok, data) VALUES (?, ?, ?, ?)", (rec["ts"], job, int(bool(ok)), json.dumps(data)))
        return rec

    async def recent(self, job=None, limit=200):
        await self.store.run(SCHEMA)
        q = "SELECT ts, job, ok, data FROM runs" + (" WHERE job = ?" if job else "") + " ORDER BY id DESC LIMIT ?"
        rows = await self.store.all(q, ((job,) if job else ()) + (limit,))
        return [{"ts": r["ts"], "job": r["job"], "ok": bool(r["ok"]), **json.loads(r["data"] or "{}")} for r in reversed(rows)]


async def health(log, windows):
    """windows: {job: max hours between runs}. -> list of human-readable problems (empty = healthy)."""
    flags = []
    for job, hours in windows.items():
        runs = await log.recent(job, 50)
        if not runs:
            flags.append(f"{job}: never ran")
            continue
        age = hours_since(runs[-1]["ts"])
        if age > hours:
            flags.append(f"{job}: last run {age:.0f} h ago (window {hours} h)")
        fails = 0
        for r in reversed(runs):
            if r["ok"]:
                break
            fails += 1
        if fails >= 2:
            flags.append(f"{job}: {fails} failed runs in a row")
    return flags
