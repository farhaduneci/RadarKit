# 🧰 RadarKit

Building blocks for **radars**: small programs that watch part of the web on a schedule, keep what they saw, and speak up only
when something changes. Pure standard-library Python, async, and the same code runs on a laptop (cron, launchd) and inside a
[Cloudflare Python Worker](https://developers.cloudflare.com/workers/languages/python/).

Used by [JobRadar](https://github.com/farhaduneci/JobRadar) (new roles at favourite companies) and CareerRadar (hiring trends
at the companies shaping AI).

## 📦 What is inside

| Module | Does |
|---|---|
| `radarkit.http` | One async HTTP client. The transport is picked once: urllib (in a thread) locally, `workers.fetch` in a Worker. Retries 429/5xx, `gather` with a concurrency cap, `run` for sync callers. |
| `radarkit.boards` | Careers-board fetchers returning one canonical row shape. Lists are cheap; descriptions and detail pages are lazy and resolved only for rows you keep. |
| `radarkit.store` | Async SQL on SQLite: stdlib `sqlite3` locally, D1 in a Worker. `.sql` migrations applied the same way on both. |
| `radarkit.runlog` | One record per run (JSONL file or a store table) and a `health` check: which job went quiet, which keeps failing. |
| `radarkit.notify` | Channels: `print`, `telegram`, `ntfy`, `webhook`. Secrets come from the environment, never the config. |
| `radarkit.telegram` | Telegram Bot API client: messages (split at the length limit), photos, documents; token redacted from every error. |
| `radarkit.template` | Message text lives in files with `{placeholders}`, never in code. |
| `radarkit.jev` | Client for [TypeSafe](https://typesafe.ai) System One (Jev): typed questions in, scores and probabilities out. |
| `radarkit.ai` | Workers AI text generation through the Worker's `AI` binding, or the REST API anywhere else. |

## 🗂️ Supported boards

A board is `<ats>:<token>`.

| ATS | Token | Example |
|---|---|---|
| `greenhouse` `ashby` `lever` `recruitee` `workable` `smartrecruiters` `gem` | board name | `greenhouse:anthropic`, `ashby:openai`, `gem:groq` |
| `personio` | `<name>.jobs.personio.de` host part | `personio:acme.de` |
| `join` | company slug on join.com | `join:acme` |
| `teamtailor` | careers host | `teamtailor:careers.acme.com` |
| `workday` | `<host>/<tenant>/<site>` | `workday:nvidia.wd5.myworkdayjobs.com/nvidia/NVIDIAExternalCareerSite` |
| `oracle` | `<host>/<siteNumber>` | `oracle:eeho.fa.us2.oraclecloud.com/CX_45001` |
| `pcsx` (Eightfold) | `<host>/<domain>[/<location>]` | `pcsx:careers.qualcomm.com/qualcomm.com` |
| `jibe` `talentbrew` | careers host | `jibe:careers.amd.com`, `talentbrew:careers.arm.com` |
| `google` | location, `*`, or a query | `google:Berlin`, `google:*`, `google:company=DeepMind` |
| `apple` | location slug or `*` | `apple:germany-DEU` |
| `microsoft` | location or `*` | `microsoft:Berlin` |
| `amazon` | comma-separated categories, or `*` (one search, capped at 10000) | `amazon:software-development,machine-learning-science` |

Workday searches stop at 2000 results; bigger boards are split by their first facet automatically.

## 🚀 Usage

```bash
pip install "radarkit @ git+https://github.com/farhaduneci/RadarKit@v0.1.2"
```

```python
from radarkit import boards, http, notify

rows = http.run(boards.fetch("greenhouse", "anthropic", eager=False))  # list only: fast
keep = [r for r in rows if "engineer" in r["title"].lower()]
http.run(boards.resolve_all(keep))                                     # descriptions for the kept rows
http.run(notify.deliver({"channel": "telegram"}, f"{len(keep)} engineering roles open"))
```

Inside a Python Worker the same calls are awaited directly; `store.D1Store(self.env.DB)` replaces `store.SqliteStore(path)`
and `ai.complete(..., binding=self.env.AI)` uses the Workers AI binding.

### 🔑 Environment

| Variable | Used by |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | `notify` telegram channel (on macOS the token may live in the Keychain item `notify-telegram-bot`) |
| `TYPESAFE_API_KEY` | `jev` |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | `ai` outside a Worker |

## 🛠️ Development

```bash
python3 -m pytest -q
```

Tests run offline against saved sample responses in `tests/fixtures/`. A new board type is one async `f_<name>(token)` in
`radarkit/boards/`, a `FETCHERS` entry, a fixture and a test.

---

Built with ❤️ by Farhad ∙ Istanbul, October 2026.
