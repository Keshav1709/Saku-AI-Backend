from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse, RedirectResponse
import os
from urllib.parse import urlencode
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import time
import uuid
import storage  # local module
import rag  # local module
import genai  # local module

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/chat/stream")
async def chat_stream(prompt: str = ""):
    async def event_generator():
        # initial meta
        yield f"data: {{\"type\": \"meta\", \"prompt\": {prompt!r} }}\n\n"
        text = (
            f"Thanks for your message: {prompt}. This is the FastAPI SSE stream. "
            "We will replace this with a real RAG answer shortly.\n\n"
        )
        for token in text.split(" "):
            yield f"data: {{\"type\": \"token\", \"value\": {token!r}}}\n\n"
            await asyncio.sleep(0.02)
        yield "data: {\"type\": \"done\"}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/search")
async def semantic_search(query: str = Form(...), k: int = Form(5)):
    results = rag.query(query, top_k=int(k))
    return {"results": rag.format_citations(results)}

@app.post("/ingest/upload")
async def ingest_upload(file: UploadFile = File(...)):
    # Read file and extract text
    raw = await file.read()
    text = ""
    if file.filename.lower().endswith(".pdf"):
        text = rag.extract_text_from_pdf(raw)
    else:
        try:
            text = raw.decode("utf-8")
        except Exception:
            text = ""
    if not text:
        return JSONResponse({"ok": False, "error": "No text extracted"}, status_code=400)

    doc_id = str(uuid.uuid4())
    rag.upsert_document(doc_id, text, {"filename": file.filename})
    registry = storage.load_docs_registry()
    registry.append({"id": doc_id, "title": file.filename, "createdAt": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    storage.save_docs_registry(registry)
    return {"ok": True, "doc_id": doc_id}

@app.get("/docs")
async def docs():
    return JSONResponse({"docs": storage.load_docs_registry()})

# --- Mock connectors state ---
CONNECTORS = None
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

@app.get("/connectors")
async def connectors():
    global CONNECTORS
    if CONNECTORS is None:
        # Load from disk once
        CONNECTORS = {c["key"]: {"name": c["name"], "connected": c["connected"]} for c in storage.load_connectors()}
    return {"connectors": [{"key": k, **v} for k, v in CONNECTORS.items()]}

@app.post("/connectors/toggle")
async def toggle_connector(payload: dict):
    key = payload.get("key")
    global CONNECTORS
    if CONNECTORS is None:
        CONNECTORS = {c["key"]: {"name": c["name"], "connected": c["connected"]} for c in storage.load_connectors()}
    if key not in CONNECTORS:
        return JSONResponse({"error": "unknown connector"}, status_code=400)
    CONNECTORS[key]["connected"] = not CONNECTORS[key]["connected"]
    # Persist
    storage.save_connectors([{ "key": k, "name": v["name"], "connected": v["connected"] } for k, v in CONNECTORS.items()])
    return {"ok": True, "connector": {"key": key, **CONNECTORS[key]}}


def _ensure_connectors_loaded():
    global CONNECTORS
    if CONNECTORS is None:
        CONNECTORS = {c["key"]: {"name": c["name"], "connected": c["connected"]} for c in storage.load_connectors()}


@app.get("/connectors/{key}/auth-url")
async def get_connector_auth_url(key: str):
    _ensure_connectors_loaded()
    if key not in CONNECTORS:
        return JSONResponse({"error": "unknown connector"}, status_code=400)
    state = uuid.uuid4().hex
    params = {"key": key, "state": state}
    url = f"{FRONTEND_URL}/api/connect/callback?{urlencode(params)}"
    return {"url": url, "state": state}


@app.get("/connectors/callback")
async def connector_callback(key: str, state: str | None = None):
    _ensure_connectors_loaded()
    if key not in CONNECTORS:
        return JSONResponse({"error": "unknown connector"}, status_code=400)
    CONNECTORS[key]["connected"] = True
    storage.save_connectors([{ "key": k, "name": v["name"], "connected": v["connected"] } for k, v in CONNECTORS.items()])
    return RedirectResponse(f"{FRONTEND_URL}/connect?connected={key}")


@app.post("/ingest/url")
async def ingest_url(url: str = Form(...)):
    text = rag.extract_text_from_url(url)
    if not text:
        return JSONResponse({"ok": False, "error": "No text extracted"}, status_code=400)
    doc_id = str(uuid.uuid4())
    rag.upsert_document(doc_id, text, {"url": url})
    registry = storage.load_docs_registry()
    registry.append({"id": doc_id, "title": url, "createdAt": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    storage.save_docs_registry(registry)
    return {"ok": True, "doc_id": doc_id}


@app.post("/chat")
async def chat(message: str = Form(...)):
    # Retrieve context
    top = rag.query(message, top_k=5)
    context = "\n\n".join([d for d, _ in top])
    citations = rag.format_citations(top)
    prompt = f"You are SakuAI. Use the context to answer.\n\nContext:\n{context}\n\nQuestion: {message}\nAnswer:"
    # Stream via SSE
    async def event_generator():
        import json as _json
        yield f"data: {{\"type\": \"context\", \"citations\": {_json.dumps(citations)} }}\n\n"
        for token in genai.stream_completion(prompt):
            yield f"data: {{\"type\": \"token\", \"value\": {token!r}}}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
