"""List the Gemini models this API key can actually use.

Model availability changes over time and differs between keys — a name that
worked last year may be retired for new projects. Run this rather than guessing:

    python scripts/list_models.py

Then set GEMINI_CHAT_MODEL and GEMINI_EMBEDDING_MODEL in .env accordingly.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Running this file directly puts scripts/ on sys.path rather than the project
# root, so `app` would not be importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai  # noqa: E402

from app.core.config import settings  # noqa: E402


def main() -> int:
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set in .env", file=sys.stderr)
        return 1

    client = genai.Client(api_key=settings.gemini_api_key)

    generation: list[str] = []
    embedding: list[str] = []

    for model in client.models.list():
        actions = list(getattr(model, "supported_actions", None) or [])
        name = (model.name or "").replace("models/", "")
        if "generateContent" in actions:
            generation.append(name)
        if "embedContent" in actions:
            embedding.append(name)

    print("=== GEMINI_CHAT_MODEL candidates (generateContent) ===")
    for name in generation:
        print(f"  {name}")

    print("\n=== GEMINI_EMBEDDING_MODEL candidates (embedContent) ===")
    for name in embedding:
        print(f"  {name}")

    if not generation:
        print(
            "\nNo generation models returned. Check that the key is valid and "
            "that the Generative Language API is enabled for its project.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
