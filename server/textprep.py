"""Text preparation for TTS: regex normalisation, optional LLM rewrite via Ollama,
sentence splitting and packing into TTS-sized segments."""
from __future__ import annotations

import json
import logging
import re
import urllib.request

log = logging.getLogger("listenup.textprep")

# ---------------------------------------------------------------------------
# Regex normalisation (always applied; also the fallback when the LLM is off)
# ---------------------------------------------------------------------------

_URL = re.compile(r"(https?://|www\.)\S+", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_CITATION = re.compile(r"\[\s*(\d+|[a-z]|citation needed|note \d+)\s*\]", re.I)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_EMPH = re.compile(r"(\*\*|__|\*|_|`)(?=\S)(.+?)(?<=\S)\1")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_BULLET = re.compile(r"^\s*([-*•‣◦▪]|\d+[.)])\s+", re.M)
_WS = re.compile(r"[ \t ]+")
_MULTI_NL = re.compile(r"\n{3,}")
_DANGLING = re.compile(r"(^|(?<=[.!?]\s))(see|source|sources|via|read more|link|links|here|more)\s*[.:;,]?\s*$", re.I)
_SEE_FOR = re.compile(r"(^|(?<=[.!?]\s))(see|read|click|visit)\s+(for|the|here|it)\b[^.!?\n]*[.!?]?\s*", re.I)
_SYMBOLS = [
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"(?<=\d)\s*%"), " percent"),
    (re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)(?:\s?(billion|million|thousand|[bmk])\b)?", re.I), lambda m: _money(m)),
    (re.compile(r"(?<![\w])~\s*(?=\d)"), "about "),
    (re.compile(r"(?<=\d)\s*[×x]\s*(?=\d)"), " by "),
    (re.compile(r"\s*[→⇒]\s*"), " to "),
    (re.compile(r"\s+[-–—]\s+"), ", "),
    (re.compile(r"[“”„]"), '"'),
    (re.compile(r"[‘’‚]"), "'"),
    (re.compile(r"…"), "..."),
    (re.compile(r"[​‌‍﻿]"), ""),
]
_UNITS = {
    "GB": "gigabytes", "MB": "megabytes", "KB": "kilobytes", "TB": "terabytes",
    "GHz": "gigahertz", "MHz": "megahertz", "kHz": "kilohertz", "Hz": "hertz",
    "ms": "milliseconds", "km": "kilometres", "kg": "kilograms", "mm": "millimetres",
    "cm": "centimetres", "fps": "frames per second", "px": "pixels", "mph": "miles per hour",
}
_UNIT_RE = re.compile(r"(?<=\d)\s?(" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")\b")


def _money(m: re.Match) -> str:
    amount, scale = m.group(1), (m.group(2) or "").lower()
    scale_word = {"b": "billion", "m": "million", "k": "thousand"}.get(scale, scale)
    return f"{amount} {scale_word} dollars" if scale_word else f"{amount} dollars"


def regex_clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _EMAIL.sub("", text)
    text = _CITATION.sub("", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_EMPH.sub(r"\2", text)
    text = _BULLET.sub("", text)
    text = _UNIT_RE.sub(lambda m: " " + _UNITS[m.group(1)], text)
    for pat, rep in _SYMBOLS:
        text = pat.sub(rep, text)
    text = _WS.sub(" ", text)
    # Make sure every paragraph ends with terminal punctuation so the TTS pauses.
    paras = []
    for p in text.split("\n"):
        p = _SEE_FOR.sub("", p)
        p = _DANGLING.sub("", p).strip().rstrip(":;,- ")
        if not p:
            continue
        if p[-1] not in ".!?\"'":
            p += "."
        paras.append(p)
    text = "\n".join(paras)
    return _MULTI_NL.sub("\n\n", text).strip()


# ---------------------------------------------------------------------------
# LLM rewrite via Ollama
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You prepare text for a text-to-speech engine that reads it aloud.
Rewrite the user's text so it sounds natural when spoken, keeping the meaning, order and level of detail. Rules:
- Expand abbreviations, symbols, units, currencies and numbers into words a narrator would say (e.g. "16GB" -> "16 gigabytes", "$749" -> "749 dollars", "~2.4GHz" -> "about 2.4 gigahertz", "e.g." -> "for example", "vs." -> "versus").
- Remove URLs, citation markers like [3], footnote numbers, navigation cruft, "click here", cookie notices, share buttons, image captions and anything that is not real content.
- Replace tables, code blocks and long lists of identifiers with a short spoken summary such as "A code example follows in the original article."
- Spell out acronyms only the first time if they are obscure; keep common ones like NASA or GPU.
- Keep sentences short. Split run-on sentences. Turn headings into short sentences.
- Do not add commentary, do not summarise, do not skip paragraphs, do not answer questions in the text.
Output only the rewritten text, as plain prose paragraphs."""

_THINK = re.compile(r"<think>.*?</think>\s*", re.S)
_PREAMBLE = re.compile(r"^(here('s| is) (the |your )?(rewritten|revised|cleaned|prepared)[^\n]*:?\s*)", re.I)


def llm_clean(text: str, model: str, host: str = "http://127.0.0.1:11434", timeout: float = 120.0) -> str | None:
    """Ask a local Ollama model to rewrite text for speech. Returns None on failure
    or if the result looks broken (so the caller can fall back to regex_clean)."""
    body = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": max(512, int(len(text) / 2.5))},
    }
    req = urllib.request.Request(
        f"{host}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        log.warning("LLM cleanup failed (%s); using regex fallback", e)
        return None
    out = data.get("message", {}).get("content", "") or ""
    out = _THINK.sub("", out)
    out = _PREAMBLE.sub("", out.strip()).strip()
    if not out:
        return None
    ratio = len(out) / max(1, len(text))
    if ratio < 0.35 or ratio > 2.2:
        log.warning("LLM output length ratio %.2f out of range; using regex fallback", ratio)
        return None
    return out


def ollama_available(host: str, model: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as r:
            names = [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception as e:  # noqa: BLE001
        return False, f"Ollama unreachable: {e}"
    base = model.split(":")[0]
    if model in names or any(n.split(":")[0] == base for n in names):
        return True, "ok"
    return False, f"model {model} not pulled (have: {', '.join(names[:8])})"


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------

_ABBREV = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "inc", "ltd", "co", "no", "fig", "approx"}
_SENT_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")


def split_sentences(paragraph: str) -> list[str]:
    parts, buf = [], ""
    for piece in _SENT_END.split(paragraph):
        piece = piece.strip()
        if not piece:
            continue
        if buf:
            last_word = buf.rstrip(".").split()[-1].lower() if buf.split() else ""
            if last_word in _ABBREV or re.fullmatch(r"[A-Z]", buf.rstrip(".").split()[-1] if buf.split() else ""):
                buf = buf + " " + piece
                continue
            parts.append(buf)
        buf = piece
    if buf:
        parts.append(buf)
    return parts


def _hard_split(sentence: str, max_chars: int) -> list[str]:
    """Split an over-long sentence on commas/semicolons, then on spaces."""
    if len(sentence) <= max_chars:
        return [sentence]
    out, cur = [], ""
    for clause in re.split(r"(?<=[,;:])\s+", sentence):
        if cur and len(cur) + 1 + len(clause) > max_chars:
            out.append(cur)
            cur = clause
        else:
            cur = (cur + " " + clause).strip()
    if cur:
        out.append(cur)
    final = []
    for piece in out:
        while len(piece) > max_chars:
            cut = piece.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            final.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if piece:
            final.append(piece)
    return final


def pack_segments(text: str, max_chars: int = 260, first_max: int = 120) -> list[str]:
    """Turn cleaned text into TTS segments. Segments never cross paragraph
    boundaries. The first segment is kept short so audio starts quickly."""
    segments: list[str] = []
    limit = first_max
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        cur = ""
        for sent in split_sentences(para):
            for piece in _hard_split(sent, limit):
                if cur and len(cur) + 1 + len(piece) > limit:
                    segments.append(cur)
                    cur = piece
                    limit = max_chars
                else:
                    cur = (cur + " " + piece).strip()
        if cur:
            segments.append(cur)
            limit = max_chars
    return segments


def batch_paragraphs(text: str, max_chars: int = 1500, first_max: int = 500) -> list[str]:
    """Group paragraphs into batches for the LLM so the first batch is small
    (fast time-to-first-audio) and later ones are large (fewer round trips)."""
    batches, cur, limit = [], "", first_max
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        if cur and len(cur) + 2 + len(para) > limit:
            batches.append(cur)
            cur, limit = para, max_chars
        else:
            cur = (cur + "\n\n" + para).strip()
    if cur:
        batches.append(cur)
    return batches
