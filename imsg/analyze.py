"""Text analysis: word frequency and topic summaries."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from imsg.reactions import REACTION_WORDS, is_reaction_message, strip_reaction_prefix

# Common English stop words + iMessage noise.
STOP_WORDS = frozenset(
    """
    a about above after again against all am an and any are aren't as at be because been
    before being below between both but by can't cannot could couldn't did didn't do does
    doesn't doing don't down during each few for from further get got had hadn't has hasn't
    have haven't having he he'd he'll he's her here here's hers herself him himself his how
    how's i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most
    mustn't my myself no nor not of off on once only or other ought our ours ourselves out
    over own same shan't she she'd she'll she's should shouldn't so some such than that
    that's the their theirs them themselves then there there's these they they'd they'll
    they're they've this those through to too under until up very was wasn't we we'd we'll
    we're we've were weren't what what's when when's where where's which while who who's
    whom why why's with won't would wouldn't you you'd you'll you're you've your yours
    yourself yourselves yeah yes no ok okay lol lmao omg u ur im idk tbh nvm tho tho rn
    haha hehe aww wow ooh ahh um uh huh oh ah emojis
    """.split()
)

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
PUNCT_RE = re.compile(r"[^\w\s']+", re.UNICODE)
WORD_RE = re.compile(r"[a-zA-Z']{3,}")
# iMessage attributedBody metadata that slips through decoding.
_GARBAGE_WORD_RE = re.compile(
    r"\d{1,3}kim[a-z]|kim(?:message|breadcrumb|file|balloon|date)|"
    r"ns(?:attributed|string|dictionary|mutable|number|value|object)|"
    r"attribute(?:name)?|optionflags|objectversion|streamtyped",
    re.I,
)


def is_garbage_word(word: str) -> bool:
    if len(word) > 28:
        return True
    if _GARBAGE_WORD_RE.search(word):
        return True
    # Long single token with digits mashed into camelCase (e.g. 44kimBreadcrumb...).
    if re.fullmatch(r"[a-zA-Z0-9]{18,}", word) and any(c.isdigit() for c in word):
        return True
    return False


def normalize_text(text: str) -> str:
    text = URL_RE.sub(" ", text)
    text = PUNCT_RE.sub(" ", text)
    return text.lower()


def _analysis_text(text: str) -> str | None:
    if is_reaction_message(text):
        text = strip_reaction_prefix(text)
        if not text or is_reaction_message(text):
            return None
    return text


def tokenize(texts: Iterable[str]) -> list[str]:
    tokens: list[str] = []
    for text in texts:
        cleaned = _analysis_text(text)
        if not cleaned:
            continue
        for word in WORD_RE.findall(normalize_text(cleaned)):
            if (
                word not in STOP_WORDS
                and word not in REACTION_WORDS
                and not word.isdigit()
                and not is_garbage_word(word)
            ):
                tokens.append(word)
    return tokens


def top_words(texts: Iterable[str], n: int = 25) -> list[dict]:
    counts = Counter(tokenize(texts))
    return [{"word": w, "count": c} for w, c in counts.most_common(n)]


def topic_phrases(texts: Iterable[str], n: int = 15) -> list[dict]:
    """Simple bigram topics from frequent adjacent word pairs."""
    bigrams: Counter[str] = Counter()
    for text in texts:
        words = tokenize([text])
        for i in range(len(words) - 1):
            bigrams[f"{words[i]} {words[i + 1]}"] += 1
    return [{"phrase": p, "count": c} for p, c in bigrams.most_common(n)]


def summarize_topics(texts: list[str]) -> dict:
    if not texts:
        return {"words": [], "phrases": [], "message_count": 0}
    return {
        "message_count": len(texts),
        "words": top_words(texts, 20),
        "phrases": topic_phrases(texts, 12),
    }
