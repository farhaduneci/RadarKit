"""One async SQL interface, two backends with the same SQLite dialect: stdlib sqlite3 locally, Cloudflare D1 in a Worker.
Migrations are plain `.sql` files applied in name order and recorded in `_migrations`, the same way on both."""
import os, re, sqlite3


class Store:
    async def all(self, sql, params=()):
        """Rows as dicts."""
        raise NotImplementedError

    async def run(self, sql, params=()):
        raise NotImplementedError

    async def batch(self, stmts):
        """[(sql, params)], atomically."""
        raise NotImplementedError

    async def script(self, sql):
        """Several ;-separated statements, no params (migrations)."""
        raise NotImplementedError

    async def one(self, sql, params=()):
        rows = await self.all(sql, params)
        return rows[0] if rows else None

    async def migrate(self, directory):
        """Apply every not-yet-applied `<directory>/*.sql`. -> names applied."""
        await self.run("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied TEXT DEFAULT CURRENT_TIMESTAMP)")
        done = {r["name"] for r in await self.all("SELECT name FROM _migrations")}
        applied = []
        for name in sorted(f for f in os.listdir(directory) if f.endswith(".sql")):
            if name in done:
                continue
            await self.script(open(os.path.join(directory, name)).read())
            await self.run("INSERT INTO _migrations (name) VALUES (?)", (name,))
            applied.append(name)
        return applied

    async def migrate_sql(self, files):
        """Same as migrate, from {name: sql} (a Worker bundles its migrations as data, not files)."""
        await self.run("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied TEXT DEFAULT CURRENT_TIMESTAMP)")
        done = {r["name"] for r in await self.all("SELECT name FROM _migrations")}
        for name in sorted(set(files) - done):
            await self.script(files[name])
            await self.run("INSERT INTO _migrations (name) VALUES (?)", (name,))
        return sorted(set(files) - done)


class SqliteStore(Store):
    def __init__(self, path):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")

    async def all(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, tuple(params)).fetchall()]

    async def run(self, sql, params=()):
        cur = self.db.execute(sql, tuple(params))
        self.db.commit()
        return cur.rowcount

    async def batch(self, stmts):
        with self.db:
            for sql, params in stmts:
                self.db.execute(sql, tuple(params))

    async def script(self, sql):
        self.db.executescript(sql)


def _plain(v):
    return v.to_py() if hasattr(v, "to_py") else v


class D1Store(Store):
    """`binding` is the Worker's env.<DB>. The Workers SDK wraps bindings and converts arguments and results
    between Python and JS itself, so plain Python values go in and come out."""

    def __init__(self, binding):
        self.d1 = binding

    def _stmt(self, sql, params):
        st = self.d1.prepare(sql)
        return st.bind(*params) if params else st

    async def all(self, sql, params=()):
        res = _plain(await self._stmt(sql, params).all())
        rows = res["results"] if isinstance(res, dict) else _plain(res.results)
        return [dict(_plain(r)) for r in rows]

    async def run(self, sql, params=()):
        res = _plain(await self._stmt(sql, params).run())
        meta = res.get("meta") if isinstance(res, dict) else _plain(getattr(res, "meta", None))
        return (meta or {}).get("changes") if isinstance(meta, dict) else None

    async def batch(self, stmts):
        if stmts:
            await self.d1.batch([self._stmt(s, p) for s, p in stmts])

    async def script(self, sql):
        sql = re.sub(r"--[^\n]*", "", sql)  # migrations hold no "--" inside strings
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await self.d1.prepare(stmt).run()


def open_store(target):
    """A path (or ':memory:') -> SqliteStore; a D1 binding -> D1Store."""
    return SqliteStore(target) if isinstance(target, str) else D1Store(target)
