"""
keywords.py - dependency-free keyword extraction (v3).

Surfaces the most salient terms in a lecture (or across a course) using term
frequency over a stopword-filtered token stream, with a light bonus for capitalised
terms and multi-word phrases (which tend to be domain concepts). No models, no
network - deterministic and fast, like ``core.summarize_text``.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List

# A compact English stoplist plus lecture filler ("okay", "gonna", "slide").
STOPWORDS = set("""
a about above after again against all am an and any are aren't as at be because been
before being below between both but by can cannot could couldn't did didn't do does
doesn't doing don't down during each few for from further had hadn't has hasn't have
haven't having he he'd he'll he's her here here's hers herself him himself his how how's
i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't my
myself no nor not of off on once only or other ought our ours ourselves out over own same
shan't she she'd she'll she's should shouldn't so some such than that that's the their
theirs them themselves then there there's these they they'd they'll they're they've this
those through to too under until up very was wasn't we we'd we'll we're we've were weren't
what what's when when's where where's which while who who's whom why why's with won't would
wouldn't you you'd you'll you're you've your yours yourself yourselves
also actually basically essentially just like really right okay ok yeah yep gonna wanna
kind sort lot lots thing things stuff going get got go one two three first second next
slide slides lecture lectures today week course question questions example examples
use uses used using make makes made manage manages managed decide decides decided
need needs needed want wants take takes taken give gives given see sees seen say says
said know knows known show shows shown find finds found call calls called come comes
mean means put puts let lets able based way ways
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")


def _tokens(text: str) -> List[str]:
    return [w for w in (m.group(0) for m in _WORD.finditer(text or ""))
            if len(w) > 2 and w.lower() not in STOPWORDS]


def keywords(text: str, limit: int = 15) -> List[Dict[str, object]]:
    """Top single-word keywords by frequency, capitalisation-boosted.

    Returns ``[{"term", "count", "score"}]`` ordered by score then count. Terms are
    lower-cased for counting; a term that appears capitalised mid-sentence (a likely
    proper noun / defined concept) gets a 1.5x score bonus.
    """
    toks = _tokens(text)
    if not toks:
        return []
    counts: Counter = Counter(t.lower() for t in toks)
    # how often each term appears capitalised (excluding pure ALLCAPS shouting)
    capped: Counter = Counter(
        t.lower() for t in toks if t[0].isupper() and not t.isupper())
    scored = []
    for term, n in counts.items():
        boost = 1.5 if capped.get(term, 0) >= max(1, n // 2) else 1.0
        scored.append({"term": term, "count": n, "score": round(n * boost, 2)})
    scored.sort(key=lambda d: (d["score"], d["count"], d["term"]), reverse=True)
    return scored[:limit]


def key_phrases(text: str, limit: int = 12) -> List[Dict[str, object]]:
    """Top 2-3 word phrases of content words (bigrams/trigrams), by frequency.

    Captures domain concepts a single-word count misses ("transport layer",
    "three way handshake"). Phrases must occur at least twice to qualify.
    """
    toks = [t.lower() for t in _tokens(text)]
    phrases: Counter = Counter()
    for n in (2, 3):
        for i in range(len(toks) - n + 1):
            phrases[" ".join(toks[i:i + n])] += 1
    out = [{"phrase": p, "count": c} for p, c in phrases.items() if c >= 2]
    # prefer longer phrases on a tie so trigrams win over their bigram substrings
    out.sort(key=lambda d: (d["count"], len(d["phrase"].split())), reverse=True)
    return out[:limit]
