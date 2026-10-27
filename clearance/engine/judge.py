"""llm_judgment rules: a question about the record, answered with a verbatim quote.

Ported from prior-auth-completeness-gate's copilot (MIT): parse-or-abstain on
malformed replies, and a grounding guard that rejects any quote not found
verbatim (whitespace-normalized) in the packet text. A "yes" without a
verifiable quote is never trusted; it becomes unverifiable and a human decides.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from .types import Extraction

Answer = Literal["yes", "no", "unclear"]


@dataclass
class Judgment:
    answer: Answer
    quote: str
    page: int | None
    confidence: float
    explanation: str
    grounded: bool
    model: str


class Judge(Protocol):
    model: str

    def ask(self, question: str, extraction: Extraction, keywords: list[str]) -> Judgment: ...


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def find_quote(quote: str, extraction: Extraction) -> int | None:
    """Page number holding the quote verbatim, or None if it's nowhere in the packet."""
    needle = normalize(quote)
    if not needle:
        return None
    for page in extraction.pages:
        if needle in normalize(page.text):
            return page.number
    return None


def guard(raw: str, extraction: Extraction, model: str) -> Judgment:
    """Parse a model reply and apply the grounding guard."""
    try:
        data = json.loads(raw)
        answer = str(data["answer"]).lower()
        quote = str(data.get("quote") or "")
        explanation = str(data.get("explanation") or "")
        confidence = min(1.0, max(0.0, float(data.get("confidence", 0.0))))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
        return Judgment("unclear", "", None, 0.0,
                        f"Model reply could not be parsed ({err}).", False, model)
    if answer not in ("yes", "no", "unclear"):
        return Judgment("unclear", "", None, 0.0, f"Unknown answer '{answer}'.", False, model)

    page = find_quote(quote, extraction) if quote else None
    grounded = answer != "yes" or page is not None
    if not grounded:
        explanation += " [Downgraded: the quote was not found verbatim in the packet.]"
        answer = "unclear"
    return Judgment(answer, quote, page, confidence, explanation, grounded, model)


_SENTENCE = re.compile(r"(?<=[.;])\s+")


class KeywordJudge:
    """Offline stand-in for a model: first sentence containing a pack keyword.

    Its reply goes through the same guard as a real model's, so the pipeline,
    tests, and UI behave identically without an API key.
    """

    spent_usd = 0.0

    model = "offline-keyword"

    def ask(self, question: str, extraction: Extraction, keywords: list[str]) -> Judgment:
        for page in extraction.pages:
            # OCR wraps lines mid-sentence; negation scope is the sentence, not the line.
            for sentence in sentences(" ".join(ln.text for ln in page.lines)):
                low = sentence.lower()
                hit = next((k for k in keywords
                            if re.search(rf"\b{re.escape(k.lower())}\b", low)), None)
                if hit and not _negated(low, hit.lower()):
                    reply = {"answer": "yes", "quote": sentence.strip(), "confidence": 0.9,
                             "explanation": f"Mentions '{hit}' (offline keyword match)."}
                    return guard(json.dumps(reply), extraction, self.model)
        reply = {"answer": "no", "quote": "", "confidence": 0.8,
                 "explanation": "No sentence in the packet mentions a qualifying term."}
        return guard(json.dumps(reply), extraction, self.model)


def sentences(text: str) -> list[str]:
    """Split on sentence ends, but re-attach one-word fragments: fax speckle turns
    "No history of..." into "No. history of...", which must not lose its negation."""
    out: list[str] = []
    carry = ""
    for part in _SENTENCE.split(text):
        part = f"{carry} {part}".strip() if carry else part
        if len(part.split()) <= 1:
            carry = part
            continue
        carry = ""
        out.append(part)
    if carry:
        out.append(carry)
    return out


_NEGATIONS = re.compile(r"\b(denies|denied|no|not|negative for|without|never)\b")


def _negated(sentence: str, keyword: str) -> bool:
    """A negation anywhere earlier in the same sentence ("No history of HTN, CAD, or stroke")."""
    return bool(_NEGATIONS.search(sentence[: sentence.find(keyword)]))


_PROMPT = """You check whether a medical-supply referral packet documents something.
You never decide coverage; you only report what the packet says.

Question: {question}

Packet text (OCR of a faxed packet; [page N] marks page breaks):
\"\"\"
{record}
\"\"\"

Reply with: answer ("yes", "no", or "unclear"); quote (the supporting sentence copied
VERBATIM from the packet, or empty); a one-sentence explanation; confidence (0.0-1.0).

Rules: "yes" requires a verbatim quote, character for character (OCR typos included).
A symptom that is explicitly denied ("denies daytime sleepiness") is not documentation of it.
Use "unclear" when the text is related but insufficient or unreadable."""


JUDGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "quote": {"type": "string"},
        "explanation": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["answer", "quote", "explanation", "confidence"],
    "additionalProperties": False,
}

DEFAULT_MODEL = "claude-opus-5-5"
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
          "claude-haiku-5-5": (0.10, 0.50)}       # $ per million tokens (input, output)


class ClaudeJudge:
    def __init__(self, model: str | None = None, client=None):
        import anthropic

        self.model = model or os.environ.get("CLEARANCE_MODEL") or DEFAULT_MODEL
        self._client = client or anthropic.Anthropic()
        self.spent_usd = 0.0

    def ask(self, question: str, extraction: Extraction, keywords: list[str]) -> Judgment:
        prompt = _PROMPT.format(question=question, record=extraction.record_text())
        try:
            msg = self._client.messages.create(
                model=self.model, max_tokens=4000,
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": JUDGMENT_SCHEMA}},
                messages=[{"role": "user", "content": prompt}],
            )
            raw = "".join(b.text for b in msg.content if b.type == "text")
            price_in, price_out = PRICES.get(self.model, (0.0, 0.0))
            self.spent_usd += (msg.usage.input_tokens * price_in
                               + msg.usage.output_tokens * price_out) / 1e6
        except Exception as err:  # network/API failure is a missing measurement
            return Judgment("unclear", "", None, 0.0, f"Model call failed: {err}", False,
                            self.model)
        return guard(_strip_fences(raw), extraction, self.model)


def _strip_fences(raw: str) -> str:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return match.group(0) if match else raw


def default_judge() -> Judge:
    return ClaudeJudge() if os.environ.get("ANTHROPIC_API_KEY") else KeywordJudge()
