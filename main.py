from fastapi import FastAPI, UploadFile, File, Form, Request as FastAPIRequest
from fastapi.responses import StreamingResponse, JSONResponse, RedirectResponse
import os
from urllib.parse import urlencode, parse_qs
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import time
import uuid
import storage  # local module
import rag  # local module
import genai  # local module
from google_auth import GoogleAuthService, GmailService, DriveService, CalendarService
import json
from typing import Dict, Any, List
import pathlib
import datetime
import config
import subprocess

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
    print("DEBUG: Health check endpoint called")
    return {"ok": True}


@app.get("/")
async def root():
    print("DEBUG: Root endpoint called")
    return {"message": "SakuAI Backend is running!", "endpoints": ["/health", "/connectors", "/docs"]}

@app.get("/chat/stream")
async def chat_stream(prompt: str = ""):
    async def event_generator():
        import json as _json
        # initial meta
        yield f"data: {{\"type\": \"meta\", \"prompt\": {prompt!r} }}\n\n"
        
        # Retrieve context using RAG
        top = rag.query(prompt, top_k=5)
        context = "\n\n".join([d for d, _ in top])
        citations = rag.format_citations(top)
        
        # Send context information
        yield f"data: {{\"type\": \"context\", \"citations\": {_json.dumps(citations)} }}\n\n"
        
        # Create enhanced prompt with context
        enhanced_prompt = f"""You are SakuAI, an intelligent AI assistant. Use the provided context to answer the user's question accurately and helpfully.

Context:
{context}

User Question: {prompt}

Please provide a helpful, accurate response based on the context. If the context doesn't contain relevant information, you can still provide general assistance but mention that you don't have specific context about this topic."""

        # Stream the AI response
        for token in genai.stream_completion(enhanced_prompt):
            yield f"data: {{\"type\": \"token\", \"value\": {token!r}}}\n\n"
            await asyncio.sleep(0.01)  # Small delay for smooth streaming
        
        yield "data: {\"type\": \"done\"}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# Google Services Data Endpoints
@app.get("/integrations/gmail/messages")
async def get_gmail_messages(max_results: int = 10, query: str = None):
    """Get Gmail messages"""
    try:
        messages = gmail_service.get_messages(max_results, query)
        return {"messages": messages}
    except Exception as e:
        return JSONResponse({"error": f"Failed to fetch Gmail messages: {str(e)}"}, status_code=500)


@app.get("/integrations/drive/files")
async def get_drive_files(max_results: int = 10, query: str = None):
    """Get Google Drive files"""
    try:
        files = drive_service.get_files(max_results, query)
        return {"files": files}
    except Exception as e:
        return JSONResponse({"error": f"Failed to fetch Drive files: {str(e)}"}, status_code=500)


@app.get("/integrations/calendar/events")
async def get_calendar_events(max_results: int = 10, time_min: str = None):
    """Get Google Calendar events"""
    try:
        events = calendar_service.get_events(max_results, time_min)
        return {"events": events}
    except Exception as e:
        return JSONResponse({"error": f"Failed to fetch Calendar events: {str(e)}"}, status_code=500)


@app.post("/integrations/drive/download/{file_id}")
async def download_drive_file(file_id: str):
    """Download a file from Google Drive"""
    try:
        file_data = drive_service.download_file(file_id)
        if not file_data:
            return JSONResponse({"error": "Failed to download file"}, status_code=500)
        
        # For now, return base64 encoded data
        import base64
        encoded_data = base64.b64encode(file_data).decode('utf-8')
        return {"file_data": encoded_data}
    except Exception as e:
        return JSONResponse({"error": f"Failed to download file: {str(e)}"}, status_code=500)


@app.post("/integrations/disconnect")
async def disconnect_integration(payload: dict):
    """Disconnect an integration"""
    service_type = payload.get("service_type")
    if not service_type:
        return JSONResponse({"error": "service_type required"}, status_code=400)
    
    try:
        # Revoke credentials
        google_auth_service.revoke_credentials(service_type)
        
        # Update connector status
        _ensure_connectors_loaded()
        connector_key = service_type  # Use the service_type directly (gmail, drive, calendar)
        if connector_key in CONNECTORS:
            CONNECTORS[connector_key]["connected"] = False
            storage.save_connectors([{ "key": k, "name": v["name"], "connected": v["connected"] } for k, v in CONNECTORS.items()])
        
        return {"ok": True, "message": f"{service_type} disconnected successfully"}
    except Exception as e:
        return JSONResponse({"error": f"Failed to disconnect: {str(e)}"}, status_code=500)


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

# Initialize Google services
google_auth_service = GoogleAuthService()
gmail_service = GmailService(google_auth_service)
drive_service = DriveService(google_auth_service)
calendar_service = CalendarService(google_auth_service)

@app.get("/connectors")
async def connectors():
    print("DEBUG: Connectors endpoint called")
    global CONNECTORS
    if CONNECTORS is None:
        print("DEBUG: Loading connectors from storage")
        # Load from disk once
        CONNECTORS = {c["key"]: {"name": c["name"], "connected": c["connected"]} for c in storage.load_connectors()}
        print(f"DEBUG: Loaded connectors: {CONNECTORS}")
    result = {"connectors": [{"key": k, **v} for k, v in CONNECTORS.items()]}
    print(f"DEBUG: Returning connectors: {result}")
    return result

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
    print(f"DEBUG: Auth URL endpoint called for key: {key}")
    _ensure_connectors_loaded()
    if key not in CONNECTORS:
        print(f"DEBUG: Unknown connector: {key}")
        return JSONResponse({"error": "unknown connector"}, status_code=400)
    
    # Handle Google services
    if key in ['gmail', 'drive', 'calendar']:
        service_type = key  # Use the key directly since they match
        state = f"{service_type}:{uuid.uuid4().hex}"
        print(f"DEBUG: Generating Google OAuth URL for service: {service_type}, state: {state}")
        
        try:
            auth_url = google_auth_service.get_auth_url(service_type, state)
            print(f"DEBUG: Generated auth URL: {auth_url}")
            return {"url": auth_url, "state": state}
        except Exception as e:
            print(f"DEBUG: Error generating auth URL: {str(e)}")
            return JSONResponse({"error": f"Failed to generate auth URL: {str(e)}"}, status_code=500)
    
    # Fallback for non-Google services
    state = uuid.uuid4().hex
    params = {"key": key, "state": state}
    url = f"{FRONTEND_URL}/api/connect/callback?{urlencode(params)}"
    print(f"DEBUG: Generated fallback URL: {url}")
    return {"url": url, "state": state}


@app.get("/connectors/callback")
async def connector_callback(request: FastAPIRequest):
    """Handle OAuth callback from Google services"""
    print("DEBUG: OAuth callback received")
    try:
        query_params = parse_qs(str(request.url.query))
        
        # Get parameters from query string
        code = query_params.get('code', [None])[0]
        state = query_params.get('state', [None])[0]
        error = query_params.get('error', [None])[0]
        
        print(f"DEBUG: Callback params - code: {code}, state: {state}, error: {error}")
        
        if error:
            print(f"DEBUG: OAuth error: {error}")
            return RedirectResponse(f"{FRONTEND_URL}/settings?error={error}&state={state}")
        
        if not code:
            print("DEBUG: No authorization code received")
            return RedirectResponse(f"{FRONTEND_URL}/settings?error=no_code&state={state}")
        
        # Parse service type from scope parameter (Google modifies state)
        service_type = None
        scope = query_params.get('scope', [None])[0]
        print(f"DEBUG: Scope parameter: {scope}")
        
        if scope:
            # Count specific scopes for each service
            gmail_scopes = sum(1 for s in ['gmail.readonly', 'gmail.modify'] if s in scope)
            drive_scopes = sum(1 for s in ['drive.readonly', 'drive.file'] if s in scope)
            calendar_scopes = sum(1 for s in ['calendar.readonly', 'calendar.events'] if s in scope)
            
            print(f"DEBUG: Scope counts - Gmail: {gmail_scopes}, Drive: {drive_scopes}, Calendar: {calendar_scopes}")
            
            # Determine service based on which has the most specific scopes
            if gmail_scopes >= drive_scopes and gmail_scopes >= calendar_scopes:
                service_type = 'gmail'
            elif drive_scopes >= calendar_scopes:
                service_type = 'drive'
            else:
                service_type = 'calendar'
            
            print(f"DEBUG: Determined service type from scope: {service_type}")
        else:
            print("DEBUG: No scope parameter, defaulting to gmail")
            service_type = 'gmail'  # Default fallback
        
        print(f"DEBUG: Exchanging code for tokens for service: {service_type}")
        print(f"DEBUG: Authorization code: {code[:20]}...")
        
        # Check if this code has already been processed (to prevent duplicate processing)
        code_key = f"{service_type}_{code[:20]}"
        if hasattr(connector_callback, '_processed_codes'):
            if code_key in connector_callback._processed_codes:
                print(f"DEBUG: Authorization code already processed, skipping")
                return RedirectResponse(f"{FRONTEND_URL}/settings?connected={service_type}")
        else:
            connector_callback._processed_codes = set()
        
        connector_callback._processed_codes.add(code_key)
        
        # Exchange code for tokens
        try:
            token_data = google_auth_service.exchange_code_for_token(service_type, code)
            print(f"DEBUG: Token exchange successful: {bool(token_data)}")
            print(f"DEBUG: Token data keys: {list(token_data.keys()) if token_data else 'None'}")
        except Exception as token_error:
            print(f"DEBUG: Token exchange failed: {str(token_error)}")
            # Remove from processed codes if it failed
            connector_callback._processed_codes.discard(code_key)
            raise token_error
        
        # Update connector status
        _ensure_connectors_loaded()
        connector_key = service_type  # Use the service_type directly (gmail, drive, calendar)
        print(f"DEBUG: Updating connector: {connector_key}")
        print(f"DEBUG: Available connectors: {list(CONNECTORS.keys())}")
        if connector_key in CONNECTORS:
            CONNECTORS[connector_key]["connected"] = True
            print(f"DEBUG: Set {connector_key} to connected: {CONNECTORS[connector_key]}")
            storage.save_connectors([{ "key": k, "name": v["name"], "connected": v["connected"] } for k, v in CONNECTORS.items()])
            print(f"DEBUG: Connector {connector_key} marked as connected")
        else:
            print(f"DEBUG: ERROR - Connector {connector_key} not found in CONNECTORS")
        
        print(f"DEBUG: Redirecting to frontend with success")
        return RedirectResponse(f"{FRONTEND_URL}/settings?connected={service_type}")
        
    except Exception as e:
        print(f"DEBUG: OAuth callback error: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(f"{FRONTEND_URL}/settings?error=oauth_failed")


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


# Google Services Data Endpoints
@app.get("/integrations/gmail/messages")
async def get_gmail_messages(max_results: int = 10, query: str = None):
    """Get Gmail messages"""
    try:
        messages = gmail_service.get_messages(max_results, query)
        return {"messages": messages}
    except Exception as e:
        return JSONResponse({"error": f"Failed to fetch Gmail messages: {str(e)}"}, status_code=500)


@app.get("/integrations/drive/files")
async def get_drive_files(max_results: int = 10, query: str = None):
    """Get Google Drive files"""
    try:
        files = drive_service.get_files(max_results, query)
        return {"files": files}
    except Exception as e:
        return JSONResponse({"error": f"Failed to fetch Drive files: {str(e)}"}, status_code=500)


@app.get("/integrations/calendar/events")
async def get_calendar_events(max_results: int = 10, time_min: str = None):
    """Get Google Calendar events"""
    try:
        events = calendar_service.get_events(max_results, time_min)
        return {"events": events}
    except Exception as e:
        return JSONResponse({"error": f"Failed to fetch Calendar events: {str(e)}"}, status_code=500)


@app.post("/integrations/drive/download/{file_id}")
async def download_drive_file(file_id: str):
    """Download a file from Google Drive"""
    try:
        file_data = drive_service.download_file(file_id)
        if not file_data:
            return JSONResponse({"error": "Failed to download file"}, status_code=500)
        
        # For now, return base64 encoded data
        import base64
        encoded_data = base64.b64encode(file_data).decode('utf-8')
        return {"file_data": encoded_data}
    except Exception as e:
        return JSONResponse({"error": f"Failed to download file: {str(e)}"}, status_code=500)


@app.post("/integrations/disconnect")
async def disconnect_integration(payload: dict):
    """Disconnect an integration"""
    service_type = payload.get("service_type")
    if not service_type:
        return JSONResponse({"error": "service_type required"}, status_code=400)
    
    try:
        # Revoke credentials
        google_auth_service.revoke_credentials(service_type)
        
        # Update connector status
        _ensure_connectors_loaded()
        connector_key = service_type  # Use the service_type directly (gmail, drive, calendar)
        if connector_key in CONNECTORS:
            CONNECTORS[connector_key]["connected"] = False
            storage.save_connectors([{ "key": k, "name": v["name"], "connected": v["connected"] } for k, v in CONNECTORS.items()])
        
        return {"ok": True, "message": f"{service_type} disconnected successfully"}
    except Exception as e:
        return JSONResponse({"error": f"Failed to disconnect: {str(e)}"}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# ---------------------- Meetings (M1–M5) ----------------------

# In-memory upload tokens map for signed uploads
UPLOAD_TOKENS: Dict[str, Dict[str, Any]] = {}
# Map objectUri -> metadata captured at upload time
UPLOAD_OBJECTS: Dict[str, Dict[str, Any]] = {}


def _now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _load_meetings() -> List[Dict[str, Any]]:
    return storage.load_meetings()


def _save_meetings(meetings: List[Dict[str, Any]]) -> None:
    storage.save_meetings(meetings)


def _find_meeting_idx(meetings: List[Dict[str, Any]], meeting_id: str) -> int:
    for i, m in enumerate(meetings):
        if m.get("id") == meeting_id:
            return i
    return -1


def _uploads_dir() -> str:
    p = os.path.join(os.path.dirname(__file__), "data", "uploads")
    os.makedirs(p, exist_ok=True)
    return p


def _ffprobe_duration_seconds(file_path: str) -> int | None:
    try:
        # Use ffprobe to get duration in seconds (float), then round to int
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ], stderr=subprocess.STDOUT, text=True).strip()
        if not out:
            return None
        seconds_float = float(out)
        if seconds_float <= 0:
            return None
        return int(seconds_float)
    except Exception:
        return None


@app.get("/meetings")
async def list_meetings():
    meetings = _load_meetings()
    # Lightweight list shape is fine; return full objects for now
    return {"meetings": meetings}


@app.post("/meetings")
async def create_meeting(
    title: str = Form(...),
    provider: str = Form("Zoom"),
    date: str = Form(None),
    tags: str = Form("[]"),
):
    try:
        tag_list = json.loads(tags) if tags else []
    except Exception:
        tag_list = []
    meetings = _load_meetings()
    mid = uuid.uuid4().hex
    now = _now_iso()
    meeting: Dict[str, Any] = {
        "id": mid,
        "title": title,
        "provider": provider or "Zoom",
        "date": date or now,
        "tags": tag_list,
        "createdAt": now,
        "updatedAt": now,
        "notes": [],
        "agenda": [],
        "actions": [],
        "recording": {
            "status": "idle",
        },
        "insights": {
            "summary": "",
            "chapters": [],
            "highlights": [],
            "keyQuestions": [],
            "extractedActions": [],
            "edited": False,
            "status": "idle",
        },
    }
    meetings.append(meeting)
    _save_meetings(meetings)
    return {"ok": True, "id": mid}


@app.get("/meetings/{meeting_id}")
async def get_meeting(meeting_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"meeting": meetings[idx]}


@app.put("/meetings/{meeting_id}")
async def update_meeting(
    meeting_id: str,
    title: str | None = Form(None),
    provider: str | None = Form(None),
    date: str | None = Form(None),
    tags: str | None = Form(None),
):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    m = meetings[idx]
    if title is not None:
        m["title"] = title
    if provider is not None:
        m["provider"] = provider
    if date is not None:
        m["date"] = date
    if tags is not None:
        try:
            m["tags"] = json.loads(tags)
        except Exception:
            pass
    m["updatedAt"] = _now_iso()
    meetings[idx] = m
    _save_meetings(meetings)
    return {"ok": True}


@app.delete("/meetings/{meeting_id}")
async def delete_meeting(meeting_id: str):
    meetings = _load_meetings()
    # Best-effort: delete transcript chunks for this meeting
    try:
        collection = rag.get_collection()
        # Try by meetingId
        try:
            collection.delete(where={"meetingId": meeting_id})  # type: ignore
        except Exception:
            # Fallback: delete by transcriptDocId ids
            for m in meetings:
                if m.get("id") != meeting_id:
                    continue
                doc_id = m.get("recording", {}).get("transcriptDocId")
                if not doc_id:
                    continue
                res = collection.get(where={"doc_id": doc_id}, include=["ids"])  # type: ignore
                ids = res.get("ids") or []
                ids = ids[0] if ids and isinstance(ids[0], list) else ids
                if ids:
                    collection.delete(ids=ids)
    except Exception:
        pass
    next_meetings = [m for m in meetings if m.get("id") != meeting_id]
    if len(next_meetings) == len(meetings):
        return JSONResponse({"error": "not_found"}, status_code=404)
    _save_meetings(next_meetings)
    return {"ok": True}


@app.post("/meetings/{meeting_id}/notes")
async def add_note(meeting_id: str, text: str = Form(...)):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    note = {"id": uuid.uuid4().hex, "text": text, "createdAt": _now_iso()}
    meetings[idx].setdefault("notes", []).append(note)
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True, "note": note}


@app.post("/meetings/{meeting_id}/agenda")
async def add_agenda(meeting_id: str, item: str = Form(...)):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    agenda_item = {"id": uuid.uuid4().hex, "item": item, "createdAt": _now_iso()}
    meetings[idx].setdefault("agenda", []).append(agenda_item)
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True, "agenda": agenda_item}


@app.post("/meetings/{meeting_id}/actions")
async def add_action(
    meeting_id: str,
    title: str = Form(...),
    assignee: str | None = Form(None),
    due: str | None = Form(None),
):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    action = {
        "id": uuid.uuid4().hex,
        "title": title,
        "assignee": assignee or "",
        "due": due or "",
        "done": False,
        "createdAt": _now_iso(),
    }
    meetings[idx].setdefault("actions", []).append(action)
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True, "action": action}


@app.post("/meetings/{meeting_id}/upload-url")
async def get_upload_url(
    meeting_id: str,
    filename: str = Form("recording"),
    contentType: str = Form("application/octet-stream"),
):
    # Validate meeting exists
    meetings = _load_meetings()
    if _find_meeting_idx(meetings, meeting_id) < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)

    token = uuid.uuid4().hex
    uploads_dir = _uploads_dir()
    safe_name = filename.replace("/", "_").replace("\\", "_")
    path = os.path.join(uploads_dir, f"{token}_{safe_name}")
    object_uri = f"file://{path}"
    # Token expires in 30 minutes
    UPLOAD_TOKENS[token] = {
        "path": path,
        "contentType": contentType,
        "filename": safe_name,
        "meetingId": meeting_id,
        "objectUri": object_uri,
        "expiresAt": time.time() + 1800,
        "consumed": False,
    }
    backend_base = (config.BACKEND_URL or os.getenv("NEXT_PUBLIC_BACKEND_URL") or "http://localhost:8000").rstrip("/")
    upload_url = f"{backend_base}/uploads/{token}"
    return {"uploadUrl": upload_url, "objectUri": object_uri}


@app.put("/uploads/{token}")
async def put_upload(token: str, request: FastAPIRequest):
    meta = UPLOAD_TOKENS.get(token)
    if not meta or meta.get("consumed"):
        return JSONResponse({"error": "invalid_or_consumed_token"}, status_code=410)
    if time.time() > float(meta.get("expiresAt", 0)):
        return JSONResponse({"error": "expired_token"}, status_code=410)

    body = await request.body()  # single read
    path = meta["path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(body)
    size = os.path.getsize(path)
    content_type = request.headers.get("content-type") or meta.get("contentType") or "application/octet-stream"
    object_uri = meta.get("objectUri")
    UPLOAD_OBJECTS[object_uri] = {
        "size": size,
        "contentType": content_type,
        "path": path,
        "uploadedAt": _now_iso(),
    }
    meta["consumed"] = True
    return JSONResponse({"ok": True, "size": size})


@app.post("/meetings/{meeting_id}/recording")
async def set_recording(meeting_id: str, objectUri: str = Form(...)):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    meta = UPLOAD_OBJECTS.get(objectUri, {})
    # Derive file path from objectUri
    path = objectUri
    if objectUri.startswith("file://"):
        path = objectUri[len("file://"):]
    size = meta.get("size")
    if size is None and os.path.exists(path):
        try:
            size = os.path.getsize(path)
        except Exception:
            size = None
    content_type = meta.get("contentType") or "application/octet-stream"
    meetings[idx].setdefault("recording", {})
    meetings[idx]["recording"].update({
        "objectUri": objectUri,
        "status": "uploaded",
        "size": size,
        "contentType": content_type,
        # duration populated in transcription step if available
    })
    # Try to compute duration now via ffprobe when available
    if isinstance(path, str) and os.path.exists(path):
        duration_sec = _ffprobe_duration_seconds(path)
        if duration_sec is not None:
            meetings[idx]["recording"]["duration"] = duration_sec
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True}


@app.post("/meetings/{meeting_id}/transcribe")
async def transcribe_meeting(meeting_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    m = meetings[idx]
    rec = m.get("recording") or {}
    if not rec.get("objectUri"):
        return JSONResponse({"error": "no_recording"}, status_code=400)

    # Update status
    rec["status"] = "transcribing"
    meetings[idx]["recording"] = rec
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)

    # Stub transcription: build a simple transcript from title/agenda/notes
    notes_text = "\n".join([n.get("text", "") for n in m.get("notes", [])])
    agenda_text = "\n".join([a.get("item", "") for a in m.get("agenda", [])])
    transcript_text = f"Transcript for meeting '{m.get('title','')}'.\nAgenda:\n{agenda_text}\nNotes:\n{notes_text}".strip()
    if not transcript_text:
        transcript_text = f"Transcript placeholder for meeting {m.get('title','Untitled')} on {m.get('date','')}"

    # Index transcript into RAG with meeting metadata as timecoded chunks
    doc_id = f"meeting-{meeting_id}-transcript"
    collection = rag.get_collection()
    # Create coarse chunks and assign synthetic timecodes
    raw = " ".join(transcript_text.split())
    chunk_size = 400
    overlap = 50
    chunks: List[str] = []
    start = 0
    while start < len(raw):
        end = min(len(raw), start + chunk_size)
        chunk = raw[start:end]
        chunks.append(chunk)
        if end == len(raw):
            break
        start = max(end - overlap, start + 1)
    # Assume each chunk is ~30s for stub
    docs = []
    ids = []
    metadatas = []
    for i, text in enumerate(chunks):
        start_sec = i * 30
        end_sec = start_sec + 30
        ids.append(f"{doc_id}_{i}")
        docs.append(text)
        metadatas.append({
            "doc_id": doc_id,
            "meetingId": meeting_id,
            "chunk_index": i,
            "startSec": start_sec,
            "endSec": end_sec,
        })
    if ids:
        collection.upsert(ids=ids, documents=docs, metadatas=metadatas)

    # Update meeting recording info
    rec["status"] = "transcribed"
    rec["transcriptDocId"] = doc_id
    rec["transcriptText"] = transcript_text
    # Attempt to populate duration if available via simple heuristic (unknown -> None)
    rec.setdefault("duration", None)
    meetings[idx]["recording"] = rec
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True, "transcriptDocId": doc_id}


@app.post("/meetings/{meeting_id}/insights/run")
async def run_insights(meeting_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    m = meetings[idx]
    m.setdefault("insights", {})
    m["insights"]["status"] = "analyzing"
    _save_meetings(meetings)

    # Build grounded inputs (transcript + notes)
    notes_text = "\n".join([n.get("text", "") for n in m.get("notes", [])])
    transcript_doc = m.get("recording", {}).get("transcriptDocId")
    transcript_snips: List[str] = []
    if transcript_doc:
        # Query a few chunks to simulate grounding
        for doc, _meta in rag.query(f"summary of meeting {m.get('title','')}", top_k=3):
            transcript_snips.append(doc)
    grounded = ("\n\n".join(transcript_snips + [notes_text])).strip()

    # Simple deterministic stub for insights
    summary = (grounded[:400] + ("…" if len(grounded) > 400 else "")) or f"Meeting '{m.get('title','')}' with provider {m.get('provider','')} on {m.get('date','')}"
    chapters = [
        {"title": "Introduction", "startSec": 0},
        {"title": "Discussion", "startSec": 60},
        {"title": "Decisions", "startSec": 120},
    ]
    # Generate highlights from first few notes or transcript snippets
    highlights: List[Dict[str, Any]] = []
    for i, line in enumerate((notes_text.split("\n") if notes_text else [])[:4]):
        if not line.strip():
            continue
        highlights.append({"label": "Note", "startSec": i * 45, "text": line.strip()})
    if not highlights and transcript_snips:
        for i, s in enumerate(transcript_snips[:3]):
            highlights.append({"label": "Topic", "startSec": i * 60, "text": s[:160] + ("…" if len(s) > 160 else "")})

    key_questions = []
    extracted_actions = [
        # Try to mirror user actions to show pipeline works
        {"title": a.get("title"), "assignee": a.get("assignee"), "due": a.get("due")}
        for a in m.get("actions", [])[:3]
    ]

    m["insights"].update({
        "summary": summary,
        "chapters": chapters,
        "highlights": highlights,
        "keyQuestions": key_questions,
        "extractedActions": extracted_actions,
        "edited": m.get("insights", {}).get("edited", False),
        "status": "ready",
        "updatedAt": _now_iso(),
    })
    meetings[idx] = m
    _save_meetings(meetings)
    return {"ok": True}


@app.get("/meetings/{meeting_id}/insights")
async def get_insights(meeting_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"insights": meetings[idx].get("insights", {})}


@app.get("/meetings/search")
async def search_meetings(
    q: str = "",
    tags: str | None = None,
    provider: str | None = None,
    dateRange: str | None = None,
    participants: str | None = None,
):
    # Basic blended search over metadata + transcript index
    meetings = _load_meetings()
    # Filter by provider/tags/date first
    def match_filters(m: Dict[str, Any]) -> bool:
        if provider and (m.get("provider") or "").lower() != provider.lower():
            return False
        if tags:
            try:
                want = set([t.strip().lower() for t in json.loads(tags)])
            except Exception:
                want = set([t.strip().lower() for t in (tags or "").split(",") if t.strip()])
            have = set([(t or "").strip().lower() for t in m.get("tags", [])])
            if not want.issubset(have):
                return False
        # dateRange placeholder: expected format "start,end" ISO; optional
        if dateRange and "," in dateRange:
            try:
                start, end = dateRange.split(",", 1)
                ds = (m.get("date") or "")
                if start and ds < start:
                    return False
                if end and ds > end:
                    return False
            except Exception:
                pass
        return True

    candidates = [m for m in meetings if match_filters(m)]
    if not q.strip():
        return {"results": candidates}

    # Keyword score
    def kw_score(m: Dict[str, Any]) -> int:
        agenda_text = " ".join([a.get("item", "") for a in m.get("agenda", [])])
        actions_text = " ".join([a.get("title", "") for a in m.get("actions", [])])
        hay = f"{m.get('title','')} {m.get('provider','')} {' '.join(m.get('tags', []))} {agenda_text} {actions_text}".lower()
        return hay.count(q.lower())

    # Transcript score via RAG
    transcript_hits: Dict[str, int] = {}
    for doc, meta in rag.query(q, top_k=20):
        mid = (meta or {}).get("meetingId")
        if not mid:
            continue
        transcript_hits[mid] = transcript_hits.get(mid, 0) + 1

    # Combine and sort
    def total_score(m: Dict[str, Any]) -> float:
        base = kw_score(m)
        extra = transcript_hits.get(m.get("id"), 0)
        return base + 0.75 * extra

    ranked = sorted(candidates, key=total_score, reverse=True)
    return {"results": ranked}


@app.delete("/meetings/{meeting_id}/transcript")
async def delete_transcript(meeting_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    m = meetings[idx]
    doc_id = m.get("recording", {}).get("transcriptDocId")
    try:
        collection = rag.get_collection()
        if doc_id:
            res = collection.get(where={"doc_id": doc_id}, include=["ids"])  # type: ignore
            ids = res.get("ids") or []
            ids = ids[0] if ids and isinstance(ids[0], list) else ids
            if ids:
                collection.delete(ids=ids)
    except Exception:
        pass
    # Clear transcript fields on meeting
    rec = m.setdefault("recording", {})
    rec.pop("transcriptDocId", None)
    rec.pop("transcriptText", None)
    m["recording"] = rec
    meetings[idx] = m
    _save_meetings(meetings)
    return {"ok": True}


@app.get("/meetings/{meeting_id}/transcript")
async def get_transcript(meeting_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    m = meetings[idx]
    transcript_doc_id = m.get("recording", {}).get("transcriptDocId")
    if not transcript_doc_id:
        return JSONResponse({"error": "no_transcript"}, status_code=404)

    try:
        collection = rag.get_collection()
        res = collection.get(where={"doc_id": transcript_doc_id}, include=["documents", "metadatas", "ids"])  # type: ignore
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        # Chroma returns lists within lists
        docs = docs[0] if docs and isinstance(docs[0], list) else docs
        metas = metas[0] if metas and isinstance(metas[0], list) else metas
        chunks = []
        for doc, meta in zip(docs, metas):
            chunks.append({
                "chunk_index": (meta or {}).get("chunk_index", 0),
                "text": doc or "",
                "startSec": (meta or {}).get("startSec"),
                "endSec": (meta or {}).get("endSec"),
                "meta": meta or {},
            })
        chunks.sort(key=lambda c: int(c.get("chunk_index") or 0))
        full_text = "\n\n".join([c["text"] for c in chunks if c.get("text")])
        return {"transcriptDocId": transcript_doc_id, "transcript": full_text, "chunks": chunks}
    except Exception as e:
        return JSONResponse({"error": "transcript_fetch_failed", "message": str(e)}, status_code=500)


@app.get("/meetings/{meeting_id}/progress")
async def get_progress(meeting_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    m = meetings[idx]
    rec = m.get("recording", {})
    ins = m.get("insights", {})
    return {
        "meetingId": meeting_id,
        "recording": {
            "status": rec.get("status"),
            "objectUri": rec.get("objectUri"),
            "transcriptDocId": rec.get("transcriptDocId"),
            "duration": rec.get("duration"),
        },
        "insights": {
            "status": ins.get("status"),
            "updatedAt": ins.get("updatedAt"),
        },
        "updatedAt": m.get("updatedAt"),
    }


@app.post("/meetings/{meeting_id}/actions/{action_id}/toggle")
async def toggle_action_status(meeting_id: str, action_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    actions = meetings[idx].setdefault("actions", [])
    found = None
    for a in actions:
        if a.get("id") == action_id:
            found = a
            break
    if not found:
        return JSONResponse({"error": "action_not_found"}, status_code=404)
    found["done"] = not bool(found.get("done"))
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True, "action": found}

@app.put("/meetings/{meeting_id}/insights")
async def update_insights(meeting_id: str, payload: dict):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    ins = meetings[idx].setdefault("insights", {})
    # Allow updating known fields
    for k in ["summary", "chapters", "highlights", "keyQuestions", "extractedActions"]:
        if k in payload:
            ins[k] = payload[k]
    ins["edited"] = True
    ins["updatedAt"] = _now_iso()
    meetings[idx]["insights"] = ins
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True, "insights": ins}

# Notes/Agenda/Actions basic edit/delete
@app.put("/meetings/{meeting_id}/notes/{note_id}")
async def edit_note(meeting_id: str, note_id: str, payload: dict):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    items = meetings[idx].setdefault("notes", [])
    for it in items:
        if it.get("id") == note_id:
            if "text" in payload:
                it["text"] = str(payload["text"])
            meetings[idx]["updatedAt"] = _now_iso()
            _save_meetings(meetings)
            return {"ok": True, "note": it}
    return JSONResponse({"error": "note_not_found"}, status_code=404)

@app.delete("/meetings/{meeting_id}/notes/{note_id}")
async def delete_note(meeting_id: str, note_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    before = len(meetings[idx].get("notes", []))
    meetings[idx]["notes"] = [n for n in meetings[idx].get("notes", []) if n.get("id") != note_id]
    if len(meetings[idx]["notes"]) == before:
        return JSONResponse({"error": "note_not_found"}, status_code=404)
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True}

@app.put("/meetings/{meeting_id}/agenda/{agenda_id}")
async def edit_agenda(meeting_id: str, agenda_id: str, payload: dict):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    items = meetings[idx].setdefault("agenda", [])
    for it in items:
        if it.get("id") == agenda_id:
            if "item" in payload:
                it["item"] = str(payload["item"])
            meetings[idx]["updatedAt"] = _now_iso()
            _save_meetings(meetings)
            return {"ok": True, "agenda": it}
    return JSONResponse({"error": "agenda_not_found"}, status_code=404)

@app.delete("/meetings/{meeting_id}/agenda/{agenda_id}")
async def delete_agenda(meeting_id: str, agenda_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    before = len(meetings[idx].get("agenda", []))
    meetings[idx]["agenda"] = [a for a in meetings[idx].get("agenda", []) if a.get("id") != agenda_id]
    if len(meetings[idx]["agenda"]) == before:
        return JSONResponse({"error": "agenda_not_found"}, status_code=404)
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True}

@app.put("/meetings/{meeting_id}/actions/{action_id}")
async def edit_action(meeting_id: str, action_id: str, payload: dict):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    items = meetings[idx].setdefault("actions", [])
    for it in items:
        if it.get("id") == action_id:
            for k in ["title", "assignee", "due"]:
                if k in payload:
                    it[k] = payload[k]
            meetings[idx]["updatedAt"] = _now_iso()
            _save_meetings(meetings)
            return {"ok": True, "action": it}
    return JSONResponse({"error": "action_not_found"}, status_code=404)

@app.delete("/meetings/{meeting_id}/actions/{action_id}")
async def delete_action(meeting_id: str, action_id: str):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    before = len(meetings[idx].get("actions", []))
    meetings[idx]["actions"] = [a for a in meetings[idx].get("actions", []) if a.get("id") != action_id]
    if len(meetings[idx]["actions"]) == before:
        return JSONResponse({"error": "action_not_found"}, status_code=404)
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True}

