"""Field extraction. `Extractor.extract(pack, pages, images) -> Extraction`.

A doc-router client would implement the same method: push the pack's schema,
pull back fields with confidence.
"""

import os


def default_extractor():
    from .fields import OcrFieldExtractor

    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("CLEARANCE_EXTRACTOR") == "claude":
        from .claude import ClaudeExtractor

        return ClaudeExtractor()
    return OcrFieldExtractor()
