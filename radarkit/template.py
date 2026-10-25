"""Message text lives in files, never in code. A template is `<dir>/<name>.txt` (or `.md`) with `{placeholders}`."""
import os, re


def load(directory, tname):
    for ext in (".txt", ".md", ""):
        p = os.path.join(directory, tname + ext)
        if os.path.exists(p):
            return open(p).read()
    raise FileNotFoundError(f"no template {tname!r} in {directory}")


def fill(template, **vars):
    """{key} placeholders; unknown keys stay as written, so a literal brace in the text is safe. Trims the result."""
    return re.sub(r"\{(\w+)\}", lambda m: str(vars[m.group(1)]) if m.group(1) in vars else m.group(0), template).strip()


def render(directory, tname, **vars):
    return fill(load(directory, tname), **vars)
