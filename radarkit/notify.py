"""Deliver a message on one channel: print | telegram | ntfy | webhook. Secrets come from the environment (or the Worker's
env), never from the config. A channel is one async function; add one to CHANNELS."""
import os

from . import http, telegram


async def c_print(cfg, text, event, env, vars):
    print(text)


async def c_telegram(cfg, text, event, env, vars):
    chat = cfg.get("chat_id") or env.get("TELEGRAM_CHAT_ID")
    if not chat:
        raise RuntimeError("telegram: no chat_id (config notify.chat_id or TELEGRAM_CHAT_ID)")
    await telegram.Bot(telegram.load_token(env)).send_message(chat, text, silent=cfg.get("silent", False), parse_mode=cfg.get("parse_mode"))


async def c_ntfy(cfg, text, event, env, vars):
    await http.request("POST", f"{cfg.get('server', 'https://ntfy.sh')}/{cfg['topic']}", text, {"Title": f"{cfg.get('title', 'radar')}: {event}"})


async def c_webhook(cfg, text, event, env, vars):
    await http.request("POST", cfg.get("url") or env["WEBHOOK_URL"], {"event": event, "text": text, **vars})


CHANNELS = {"print": c_print, "telegram": c_telegram, "ntfy": c_ntfy, "webhook": c_webhook}


async def deliver(cfg, text, event="message", env=None, **vars):
    """Send `text` on cfg["channel"] (default print). Raises on failure so the caller can keep the item unsent."""
    cfg = cfg or {"channel": "print"}
    ch = cfg.get("channel", "print")
    if ch not in CHANNELS:
        raise ValueError(f"unknown notify channel {ch!r}; one of {', '.join(CHANNELS)}")
    await CHANNELS[ch](cfg, text, event, dict(os.environ) if env is None else env, vars)
    return True
