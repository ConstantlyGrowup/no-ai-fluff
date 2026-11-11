"""English round trip: EN -> bridge (Japanese by default) -> EN."""

from __future__ import annotations

import re

from ..masker import PLACEHOLDER_RE, check_placeholders, markup_signature
from ..rtt import RoundTripTranslator, split_long

LANG_NAMES_EN = {"ja": "Japanese", "en": "English", "ru": "Russian", "fr": "French", "de": "German",
                 "es": "Spanish", "zh": "Chinese"}
_LATIN = re.compile(r"[A-Za-z]")
_NON_LATIN_SCRIPT = re.compile(r"[Ѐ-ӿ぀-ヿ㐀-䶿一-鿿가-힯]")
_SENT_END_EN = re.compile(r"(?<=[.!?;])(?=\s)|(?<=\n)")
_SOFT_END_EN = re.compile(r"(?<=[,:—])(?=\s)")


def build_prompt_en(text: str, target: str, batched: bool = False, context: str | None = None) -> str:
    """Hunyuan-MT's official prompt for non-Chinese pairs; the context variant uses the same wording."""
    lang = LANG_NAMES_EN.get(target, target)
    if context and context.strip():
        return (f"{context.strip()}\nReferring to the information above, translate the following segment into {lang}. "
                f"Do not translate the text above and do not add explanations:\n{text}")
    return f"Translate the following segment into {lang}, without additional explanation.\n\n{text}"


def split_long_en(text: str, max_chars: int) -> list[str]:
    return split_long(text, max_chars, _SENT_END_EN, _SOFT_END_EN)


def validate_en(src: str, back: str, ids: list[int]) -> str:
    """Return the failure reason, or "" if the back-translation is usable."""
    chk = check_placeholders(back, ids)
    if not chk.ok:
        return f"placeholder missing={chk.missing} extra={chk.extra} dup={chk.duplicated}"
    if markup_signature(src) != markup_signature(back):
        return "inline markup changed"
    plain = PLACEHOLDER_RE.sub("", back)
    if _NON_LATIN_SCRIPT.search(plain) or not _LATIN.search(plain):
        return "back-translation not English"
    ls, lb = len(PLACEHOLDER_RE.sub("", src)), len(plain)
    if ls >= 20 and not (0.5 <= lb / ls <= 2.2):
        return f"length ratio {lb / ls:.2f} out of range"
    return ""


class EnRoundTripTranslator(RoundTripTranslator):
    prompt_fn = staticmethod(build_prompt_en)
    split_fn = staticmethod(split_long_en)
    validate = staticmethod(validate_en)

    def __init__(self, *args, **kw):
        kw.setdefault("source", "en")
        kw.setdefault("bridge", "ja")
        super().__init__(*args, **kw)
