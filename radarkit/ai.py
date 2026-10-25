"""Workers AI text generation, through the Worker's `AI` binding when there is one, else the REST API
(CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN). Prompts live in the caller's files; this is only the client."""
import json, os

from .http import post_json

MODEL = "@cf/moonshotai/kimi-k2.6"


def text_of(out):
    """The generated text, whatever the model's response shape (Workers AI, Chat Completions or Responses API)."""
    if isinstance(out, str):
        return out
    out = out.get("result", out) if isinstance(out, dict) else out
    if isinstance(out, dict):
        if isinstance(out.get("response"), str):
            return out["response"]
        if out.get("choices"):
            c = out["choices"][0]
            if not (c.get("message") or {}).get("content"):
                raise ValueError(f"empty AI answer (finish_reason {c.get('finish_reason')}): raise max_tokens for a reasoning model")
            return c["message"]["content"]
        for item in out.get("output") or []:
            for c in item.get("content") or [] if isinstance(item, dict) else []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    return c["text"]
        if isinstance(out.get("output_text"), str):
            return out["output_text"]
    raise ValueError(f"no text in AI response: {json.dumps(out)[:300]}")


async def run_binding(binding, model, body):
    """The Workers SDK wraps env.AI and converts Python arguments and results itself."""
    res = await binding.run(model, body)
    return res.to_py() if hasattr(res, "to_py") else res


async def complete(system, user, model=MODEL, binding=None, account=None, token=None, max_tokens=2000, temperature=0.4, extra=None):
    """One chat completion -> text. binding: the Worker's env.AI; else REST with account id and API token (args or env).
    extra: model-specific input merged into the body, e.g. {"chat_template_kwargs": {"thinking": False}} for Kimi."""
    body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": temperature, **(extra or {})}
    account = account or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = token or os.environ.get("CLOUDFLARE_API_TOKEN")
    if binding is not None:
        try:
            return text_of(await run_binding(binding, model, body)).strip()
        except Exception:
            if not (account and token):  # no REST credentials to fall back on
                raise
    if not (account and token):
        raise RuntimeError("ai: no AI binding and no CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN for the REST API")
    out = await post_json(f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}", body,
                          headers={"Authorization": f"Bearer {token}"}, timeout=180, retries=2)
    return text_of(out).strip()
