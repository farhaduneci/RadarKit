# RadarKit

Shared library for radars (JobRadar, CareerRadar). Stdlib-only, async Python that must run both locally and in a Cloudflare
Python Worker (Pyodide): no threads outside `http.urllib_transport`, no subprocess outside `telegram.load_token`, no C deps.

## Map

- `radarkit/http.py`: async `request`, transport switch (urllib / `workers.fetch`), `gather`, `run`.
- `radarkit/boards/`: `ats.py` (hosted ATSs), `bigtech.py` (Apple, Google, PCSX, Amazon), `enterprise.py` (Workday, Oracle,
  Jibe, TalentBrew), `common.py` helpers, `__init__.py` registry + `fetch` / `resolve` / `board_of`.
- `store.py` (sqlite3 / D1), `runlog.py`, `notify.py`, `telegram.py`, `template.py`, `jev.py`, `ai.py`.

## Rules

- Incremental commits: small, logical, conventional (`feat:`, `fix:`...), stage only touched paths, push each.
- Row ids are state keys in consumers: never change an existing fetcher's id format.
- Tests: `python3 -m pytest -q` green before every commit. A fetcher change gets a fixture-backed test; fixtures stay small.
- Nothing personal in tracked files. Static text (messages, prompts) belongs to consumers' files, never in this library.
- Release: bump `version` in `pyproject.toml` and `radarkit/__init__.py`, tag `vX.Y.Z`, push the tag; consumers pin tags.
