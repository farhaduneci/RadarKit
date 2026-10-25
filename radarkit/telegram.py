"""Minimal Telegram Bot API client, async, stdlib only. Retries rate limits (honouring retry_after) and transient failures.

Token: passed in, else $TELEGRAM_BOT_TOKEN, else (macOS, local only) the Keychain item `KEYCHAIN`. The token never
appears in an error message: every message passes through `redact`.
"""
import asyncio, json, mimetypes, os, re, subprocess, uuid

from . import http

API = "https://api.telegram.org"
KEYCHAIN = "notify-telegram-bot"
LIMIT = 4096  # Telegram's max message length
RETRIES = 3


class TelegramError(Exception):
    """A Bot API call that failed for good. `description` is Telegram's own reason, when it gave one."""

    def __init__(self, method, description, code=None):
        self.method, self.description, self.code = method, description, code
        super().__init__(redact(f"{method} failed{f' ({code})' if code else ''}: {description}"))


def redact(s):
    return re.sub(r"bot\d+(?::|%3A)[\w-]+", "bot<token>", str(s))


def load_token(env=None):
    token = ((env or os.environ).get("TELEGRAM_BOT_TOKEN") or "").strip()
    if token:
        return token
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True)
    except (FileNotFoundError, OSError):  # not macOS, or no subprocesses (Workers)
        r = None
    if not r or r.returncode or not r.stdout.strip():
        raise TelegramError("token", "no bot token: set TELEGRAM_BOT_TOKEN (or, on macOS, store it once with: "
                                     f"security add-generic-password -a notify -s {KEYCHAIN} -w)")
    return r.stdout.strip()


def multipart(fields, files):
    """(body, content type). files: {field: path or (filename, bytes)}."""
    boundary = uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    for name, f in files.items():
        fname, data = (f if isinstance(f, tuple) else (os.path.basename(f), open(f, "rb").read()))
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'
                 f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def chunks(text, limit=LIMIT):
    """Split at line breaks so no piece passes Telegram's length limit."""
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            out, line = out + ([cur] if cur else []) + [line[:limit]], line[limit:]
            cur = ""
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    return out + ([cur] if cur else [])


class Bot:
    def __init__(self, token=None, timeout=60, sleep=asyncio.sleep):
        self._token, self.timeout, self._sleep = token, timeout, sleep

    @property
    def token(self):
        if self._token is None:
            self._token = load_token()
        return self._token

    async def call(self, method, params=None, files=None):
        """POST one method -> `result`. Retries 429, 5xx and network errors."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if files:
            body, ctype = multipart(params, files)
        else:
            body, ctype = json.dumps(params).encode(), "application/json"
        url = f"{API}/bot{self.token}/{method}"
        for attempt in range(RETRIES + 1):
            wait = 2 ** attempt
            try:
                status, raw = await http.transport()("POST", url, body, {"Content-Type": ctype}, self.timeout)
            except Exception as e:
                if attempt == RETRIES:
                    raise TelegramError(method, f"network: {e}")
                await self._sleep(wait)
                continue
            try:
                out = json.loads(raw)
            except ValueError:
                out = {"ok": False, "error_code": status, "description": raw[:200]}
            if out.get("ok"):
                return out.get("result")
            code = out.get("error_code")
            if attempt < RETRIES and (code == 429 or (code or 0) >= 500):
                await self._sleep((out.get("parameters") or {}).get("retry_after") or wait)
                continue
            raise TelegramError(method, out.get("description", "unknown error"), code)

    async def send_message(self, chat_id, text, silent=False, parse_mode=None, preview=False):
        """One message, or several when the text is over the limit. -> the last message sent."""
        last = None
        for part in chunks(text):
            last = await self.call("sendMessage", {"chat_id": chat_id, "text": part, "parse_mode": parse_mode,
                                                   "disable_notification": silent, "link_preview_options": {"is_disabled": not preview}})
        return last

    async def send_photo(self, chat_id, photo, caption=None, silent=False):
        return await self.call("sendPhoto", {"chat_id": chat_id, "caption": caption, "disable_notification": str(silent).lower()}, {"photo": photo})

    async def send_document(self, chat_id, doc, caption=None, silent=False):
        return await self.call("sendDocument", {"chat_id": chat_id, "caption": caption, "disable_notification": str(silent).lower()}, {"document": doc})

    async def get_updates(self, offset=None):
        return await self.call("getUpdates", {"timeout": 0, "offset": offset})
