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

