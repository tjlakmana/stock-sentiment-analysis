"""
Named Entity Recognition using spaCy.

Loads en_core_web_sm once as a process-level singleton (lazy, thread-safe).
Downloads the model automatically on first use if not already installed.

Returned EntitySpan objects carry the raw spaCy label (ORG, PERSON, GPE, …)
so the EntityClassifier can apply domain-specific rules on top.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from loguru import logger

_load_lock = threading.Lock()
_nlp = None

# Only these spaCy labels produce entities useful for entity resolution.
# MONEY, DATE, CARDINAL, etc. are deliberately excluded.
_USEFUL_LABELS = frozenset({
    "ORG", "PERSON", "GPE", "NORP",
    "FAC", "PRODUCT", "LAW", "EVENT",
})


@dataclass
class EntitySpan:
    text: str
    label: str   # spaCy label: ORG, PERSON, GPE, …
    start: int
    end: int


def _get_nlp():
    global _nlp
    if _nlp is not None:
        return _nlp
    with _load_lock:
        if _nlp is None:
            import spacy
            try:
                model = spacy.load(
                    "en_core_web_sm",
                    disable=["lemmatizer"],
                )
                logger.info("[ner] spaCy model loaded: en_core_web_sm")
            except OSError:
                logger.info("[ner] en_core_web_sm not found — downloading…")
                from spacy.cli import download as spacy_download
                spacy_download("en_core_web_sm")
                model = spacy.load("en_core_web_sm", disable=["lemmatizer"])
                logger.info("[ner] spaCy model downloaded and loaded.")
            _nlp = model
    return _nlp


class NERExtractor:
    """
    Extracts typed named-entity spans from article text using spaCy.
    The model is a module-level singleton — loaded once per process.
    """

    def extract(self, title: str, summary: str) -> list[EntitySpan]:
        """
        Run NER over title + summary and return deduplicated entity spans.

        Text is capped at 2 000 characters to bound CPU time on long articles.
        """
        text = f"{title or ''}. {summary or ''}".strip(". ")
        if not text:
            return []

        nlp = _get_nlp()
        doc = nlp(text[:2000])

        seen: set[str] = set()
        spans: list[EntitySpan] = []
        for ent in doc.ents:
            if ent.label_ not in _USEFUL_LABELS:
                continue
            normalized = ent.text.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            spans.append(EntitySpan(
                text=normalized,
                label=ent.label_,
                start=ent.start_char,
                end=ent.end_char,
            ))

        return spans
