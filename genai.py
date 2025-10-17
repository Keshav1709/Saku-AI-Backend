import os
from typing import Iterable

import google.generativeai as genai


def configure() -> None:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)


def stream_completion(prompt: str) -> Iterable[str]:
    configure()
    # Fallback to a simple echo if no API key
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        for token in ("Echo: " + prompt).split(" "):
            yield token + " "
        return
    model = genai.GenerativeModel("gemini-1.5-flash")
    stream = model.generate_content(prompt, stream=True)
    for evt in stream:
        if evt.text:
            yield evt.text


