import os
from typing import Iterable

import google.generativeai as genai


def configure() -> None:
    api_key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY")  # common alt name
    )
    if api_key:
        genai.configure(api_key=api_key)


def stream_completion(prompt: str) -> Iterable[str]:
    configure()
    # Fallback to a simple placeholder if no API key
    if not (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY")
    ):
        msg = "Model key not configured. Please set GEMINI_API_KEY or GOOGLE_GENERATIVE_AI_API_KEY."
        for token in msg.split(" "):
            yield token + " "
        return
    # Prefer configurable model; default to a widely available one.
    model_name = (
        os.getenv("GENAI_MODEL")
        or os.getenv("GOOGLE_GENAI_MODEL")
        or "gemini-2.5-flash"
    )
    try:
        model = genai.GenerativeModel(model_name)
        stream = model.generate_content(prompt, stream=True)
    except Exception:
        # Fallbacks in case of region/tier mismatches
        for candidate in ("gemini-2.0-flash-exp", "gemini-1.5-flash-latest", "gemini-1.5-pro-latest", "gemini-1.0-pro"): 
            try:
                model = genai.GenerativeModel(candidate)
                stream = model.generate_content(prompt, stream=True)
                break
            except Exception:
                stream = None
        if stream is None:
            # Last resort
            yield "Model resolution failed. Check API key/project access."
            return
    for evt in stream:
        if getattr(evt, "text", None):
            yield evt.text


