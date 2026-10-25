"""Jev, TypeSafe's System One judgment model (https://typesafe.ai): typed questions about a state, answered as scores,
probabilities and choices. Questions live in the caller's files; this is only the client."""
import os

from .http import post_json

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"


async def ask(state, questions, key=None, model=MODEL, url=URL, retries=2):
    """{question: answer} for one state. Key: passed in, else $TYPESAFE_API_KEY."""
    key = key or os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise RuntimeError("jev: no API key (TYPESAFE_API_KEY)")
    d = await post_json(url, {"model": model, "state": state, "questions": questions},
                        headers={"Authorization": f"Bearer {key}"}, timeout=120, retries=retries)
    return d["answers"]
