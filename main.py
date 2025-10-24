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
from google.cloud import storage as gcs_storage
import mimetypes
from fastapi.responses import FileResponse
import requests

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
    return {"message": "SakuAI Backend is running!", "endpoints": ["/health", "/connectors", "/documents"]}

@app.get("/chat/stream")
async def chat_stream(prompt: str = "", docIds: str | None = None, convId: str | None = None):
    async def event_generator():
        import json as _json
        # initial meta
        yield f"data: {{\"type\": \"meta\", \"prompt\": {prompt!r} }}\n\n"
        
        # Retrieve context using RAG (optionally filtered by uploaded document IDs)
        selected_ids = None
        if docIds:
            if docIds.strip() == "*":
                selected_ids = None  # all docs
            else:
                selected_ids = [d.strip() for d in docIds.split(",") if d.strip()]
        top = rag.query(prompt, top_k=6, doc_ids=selected_ids)
        context = "\n\n".join([d for d, _ in top])
        citations = rag.format_citations(top)
        
        # Send context information
        yield f"data: {{\"type\": \"context\", \"citations\": {_json.dumps(citations)} }}\n\n"
        
        # Create enhanced prompt with context
        enhanced_prompt = f"""You are SakuAI, an intelligent AI assistant.
You will receive:
- User question
- Optional retrieved context chunks from user's uploaded documents and indexed artifacts

Guidelines:
- First, derive a concise plan for answering the question.
- Use the context snippets only when relevant; cite key facts succinctly.
- If context is weak or irrelevant, explicitly say so and answer from general knowledge.
- Provide a short, direct answer first, followed by brief supporting details.

Context (may be empty):
{context}

User Question: {prompt}
"""

        # Persist user message to conversation if provided
        if convId:
            try:
                items = storage.load_conversations()
                for c in items:
                    if c.get("id") == convId:
                        c.setdefault("messages", []).append({
                            "role": "user",
                            "content": prompt,
                            "createdAt": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        })
                        # If title is empty, set it from the first user message
                        title = (c.get("title") or "").strip()
                        if not title:
                            # Shorten to first ~8 words
                            words = prompt.split()
                            c["title"] = " ".join(words[:8])[:120]
                        c["updatedAt"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                        break
                storage.save_conversations(items)
            except Exception:
                pass

        # Stream the AI response
        assistant_accum = []
        for token in genai.stream_completion(enhanced_prompt):
            yield f"data: {{\"type\": \"token\", \"value\": {token!r}}}\n\n"
            assistant_accum.append(token)
            await asyncio.sleep(0.01)  # Small delay for smooth streaming
        
        yield "data: {\"type\": \"done\"}\n\n"
        # Save assistant message at the end
        if convId:
            try:
                text = "".join(assistant_accum)
                items = storage.load_conversations()
                for c in items:
                    if c.get("id") == convId:
                        c.setdefault("messages", []).append({
                            "role": "assistant",
                            "content": text,
                            "createdAt": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        })
                        c["updatedAt"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                        break
                storage.save_conversations(items)
            except Exception:
                pass
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


@app.post("/integrations/gmail/send")
async def gmail_send(payload: dict):
    """Send an email via Gmail API.
    Expected payload: { to: str, subject: str, body: str, threadId?: str }
    """
    try:
        to = str(payload.get("to") or "").strip()
        subject = str(payload.get("subject") or "")
        body = str(payload.get("body") or "")
        thread_id = payload.get("threadId")
        if not to:
            return JSONResponse({"error": "'to' is required"}, status_code=400)
        result = gmail_service.send_message(to=to, subject=subject, body=body, thread_id=thread_id)
        if not result:
            return JSONResponse({"error": "send_failed"}, status_code=500)
        return {"ok": True, "messageId": result.get("id"), "threadId": result.get("threadId")}
    except Exception as e:
        return JSONResponse({"error": f"Failed to send email: {str(e)}"}, status_code=500)


@app.post("/integrations/gmail/draft")
async def gmail_draft(payload: dict):
    """Create a Gmail draft.
    Expected payload: { to: str, subject: str, body: str, threadId?: str }
    """
    try:
        to = str(payload.get("to") or "").strip()
        subject = str(payload.get("subject") or "")
        body = str(payload.get("body") or "")
        thread_id = payload.get("threadId")
        if not to:
            return JSONResponse({"error": "'to' is required"}, status_code=400)
        result = gmail_service.create_draft(to=to, subject=subject, body=body, thread_id=thread_id)
        if not result:
            return JSONResponse({"error": "draft_failed"}, status_code=500)
        return {"ok": True, "draftId": result.get("id")}
    except Exception as e:
        return JSONResponse({"error": f"Failed to create draft: {str(e)}"}, status_code=500)


@app.get("/integrations/status")
async def integrations_status():
    """Lightweight connectivity and fetch check for Gmail/Drive/Calendar."""
    _ensure_connectors_loaded()
    resp: Dict[str, Any] = {"gmail": {}, "drive": {}, "calendar": {}}

    # Gmail
    try:
        connected = bool(CONNECTORS.get("gmail", {}).get("connected"))
        ok = False
        count = 0
        if connected:
            msgs = gmail_service.get_messages(max_results=3)
            ok = isinstance(msgs, list)
            count = len(msgs)
        resp["gmail"] = {"connected": connected, "ok": ok, "sampleCount": count}
    except Exception:
        resp["gmail"] = {"connected": False, "ok": False, "sampleCount": 0}

    # Drive
    try:
        connected = bool(CONNECTORS.get("drive", {}).get("connected"))
        ok = False
        count = 0
        if connected:
            files = drive_service.get_files(max_results=3)
            ok = isinstance(files, list)
            count = len(files)
        resp["drive"] = {"connected": connected, "ok": ok, "sampleCount": count}
    except Exception:
        resp["drive"] = {"connected": False, "ok": False, "sampleCount": 0}

    # Calendar
    try:
        connected = bool(CONNECTORS.get("calendar", {}).get("connected"))
        ok = False
        count = 0
        if connected:
            events = calendar_service.get_events(max_results=3)
            ok = isinstance(events, list)
            count = len(events)
        resp["calendar"] = {"connected": connected, "ok": ok, "sampleCount": count}
    except Exception:
        resp["calendar"] = {"connected": False, "ok": False, "sampleCount": 0}

    return {"ok": True, "status": resp}


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


@app.post("/integrations/zoom/import")
async def import_zoom_recordings(since: str = None, until: str = None, limit: int = 10):
    """Import recent Zoom recordings and run the pipeline."""
    try:
        acct = os.getenv("ZOOM_ACCOUNT_ID"); cid = os.getenv("ZOOM_CLIENT_ID"); csec = os.getenv("ZOOM_CLIENT_SECRET")
        if not all([acct, cid, csec]):
            return JSONResponse({"error": "zoom_env_missing"}, status_code=400)
        token_resp = requests.post(
            "https://zoom.us/oauth/token",
            params={"grant_type": "account_credentials", "account_id": acct},
            auth=(cid, csec), timeout=30
        )
        token_json = token_resp.json()
        access = token_json.get("access_token")
        if not access:
            return JSONResponse({"error": "zoom_auth_failed"}, status_code=401)
        headers = {"Authorization": f"Bearer {access}"}
        params = {}
        if since: params["from"] = since
        if until: params["to"] = until
        rec_json = requests.get("https://api.zoom.us/v2/users/me/recordings", headers=headers, params=params, timeout=30).json()
        meetings_created: List[str] = []
        for m in (rec_json.get("meetings") or [])[:limit]:
            title = m.get("topic") or f"Zoom Meeting {m.get('uuid')}"
            for f in (m.get("recording_files") or []):
                file_type = (f.get("file_type") or "").lower()
                if file_type not in ("mp4", "m4a", "mkv", "mov"):
                    continue
                download_url = f.get("download_url")
                if not download_url:
                    continue
                dl = f"{download_url}?access_token={access}"
                object_uri = _ingest_blob_from_url("tmp", dl, f.get("id", "zoom.mp4"), file_type)
                mid = _create_and_process_meeting_from_uri(title, object_uri, provider="Zoom")
                meetings_created.append(mid)
        return {"ok": True, "created": meetings_created}
    except Exception as e:
        return JSONResponse({"error": "zoom_import_failed", "message": str(e)}, status_code=500)


@app.post("/integrations/google/meet/import")
async def import_google_meet_recordings(query: str = "mimeType contains 'video/' and name contains 'Meet'", limit: int = 10):
    """Import recent Google Meet recordings from Drive and run the pipeline."""
    try:
        _ensure_connectors_loaded()
        if not CONNECTORS.get("drive", {}).get("connected"):
            return JSONResponse({"error": "drive_not_connected"}, status_code=400)
        try:
            files = drive_service.get_files(limit, query)
        except Exception:
            files = []
        meetings_created: List[str] = []
        for f in files:
            file_id = f.get("id"); name = f.get("name") or "Google Meet Recording"
            # Using authenticated download endpoint; DriveService manages auth
            dl_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
            object_uri = _ingest_blob_from_url("tmp", dl_url, name, f.get("mimeType"))
            mid = _create_and_process_meeting_from_uri(name, object_uri, provider="Google Meet")
            meetings_created.append(mid)
        return {"ok": True, "created": meetings_created}
    except Exception as e:
        return JSONResponse({"error": "meet_import_failed", "message": str(e)}, status_code=500)


@app.post("/integrations/disconnect")
async def disconnect_integration(payload: dict):
    """Disconnect an integration"""
    service_type = payload.get("service_type")
    if not service_type:
        return JSONResponse({"error": "service_type required"}, status_code=400)
    
    try:
        # Revoke credentials if it's a Google connector we manage
        if service_type in ["gmail", "drive", "calendar"]:
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

@app.get("/documents")
async def docs():
    return JSONResponse({"docs": storage.load_docs_registry()})

# ---------------------- Conversations ----------------------

@app.get("/conversations")
async def list_conversations():
    items = storage.load_conversations()
    # return only metadata
    meta = [
        {"id": c.get("id"), "title": c.get("title"), "createdAt": c.get("createdAt"), "updatedAt": c.get("updatedAt")}
        for c in items
    ]
    return {"conversations": meta}


@app.post("/conversations")
async def create_conversation(user_id: str = Form("default")):
    items = storage.load_conversations()
    cid = uuid.uuid4().hex
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    items.append({
        "id": cid,
        "title": "",
        "userId": user_id,
        "createdAt": now,
        "updatedAt": now,
        "messages": [],
    })
    storage.save_conversations(items)
    return {"ok": True, "id": cid}


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    items = storage.load_conversations()
    for c in items:
        if c.get("id") == conversation_id:
            return {"conversation": c}
    return JSONResponse({"error": "not_found"}, status_code=404)


@app.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str):
    items = storage.load_conversations()
    for c in items:
        if c.get("id") == conversation_id:
            return {"messages": c.get("messages", [])}
    return {"messages": []}


# --- Insights endpoint ---
@app.get("/insights")
async def get_insights(range: str = "weekly"):
    """Get workspace insights and analytics data"""
    try:
        # Mock data - in production, this would query actual analytics
        data = {
            "data": {
                "stats": {
                    "tasksAutomated": {
                        "value": 47,
                        "change": 12,
                        "period": "from last week"
                    },
                    "avgResponseTime": {
                        "value": 47,
                        "change": 0.4,
                        "period": "from last month"
                    },
                    "successRate": {
                        "value": 98.5,
                        "change": 2.1,
                        "period": "from this month"
                    },
                    "activeWorkflows": {
                        "value": 156,
                        "pending": 8
                    }
                },
                "recentActivities": [
                    {
                        "id": "1",
                        "type": "success",
                        "title": "Email Summarization completed",
                        "description": "Processed 15 emails, created 3 action items",
                        "timestamp": "5 minutes ago",
                        "icon": "mail"
                    },
                    {
                        "id": "2",
                        "type": "success",
                        "title": "Client response approved",
                        "description": "Email sent to john.client@company.com",
                        "timestamp": "15 minutes ago",
                        "icon": "mail"
                    },
                    {
                        "id": "3",
                        "type": "info",
                        "title": "Meeting summary generated",
                        "description": "Product Strategy Review - 4 action items created",
                        "timestamp": "1 hour ago",
                        "icon": "file"
                    },
                    {
                        "id": "4",
                        "type": "error",
                        "title": "Slack integration failed",
                        "description": "Unable to post to #product-updates channel",
                        "timestamp": "2 hours ago",
                        "icon": "alert"
                    },
                    {
                        "id": "5",
                        "type": "info",
                        "title": "Meeting summary generated",
                        "description": "Product Strategy Review - 4 action items created",
                        "timestamp": "1 hour ago",
                        "icon": "copy"
                    }
                ],
                "workflowPerformance": [
                    {
                        "name": "Slack Meeting",
                        "executions": 23,
                        "date": "22/23",
                        "color": "#f59e0b"
                    },
                    {
                        "name": "Meeting Summarization",
                        "executions": 23,
                        "date": "22/23",
                        "color": "#8b5cf6"
                    },
                    {
                        "name": "Forwarding Email",
                        "executions": 23,
                        "date": "22/23",
                        "color": "#6366f1"
                    },
                    {
                        "name": "Meeting Follow-up",
                        "executions": 23,
                        "date": "22/23",
                        "color": "#3b82f6"
                    },
                    {
                        "name": "Document Review",
                        "executions": 23,
                        "date": "22/23",
                        "color": "#10b981"
                    },
                    {
                        "name": "Document Review",
                        "executions": 23,
                        "date": "22/23",
                        "color": "#22c55e"
                    }
                ],
                "weeklyTrends": {
                    "executionVolume": {
                        "value": 47,
                        "change": 34
                    },
                    "approvalRate": {
                        "value": 89,
                        "change": 5
                    },
                    "traceUsage": {
                        "value": 23,
                        "change": 0
                    }
                }
            }
        }
        return data
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
        
        print(f"DEBUG: Callback params - code: {code[:20] if code else None}, state: {state}, error: {error}")
        print(f"DEBUG: Full state value: '{state}'")
        
        if error:
            print(f"DEBUG: OAuth error: {error}")
            return RedirectResponse(f"{FRONTEND_URL}/settings?error={error}&state={state}")
        
        if not code:
            print("DEBUG: No authorization code received")
            return RedirectResponse(f"{FRONTEND_URL}/settings?error=no_code&state={state}")
        
        # Parse service type from state parameter (we encode it as "service_type:random_string")
        # The state parameter is more reliable than scopes since Google returns ALL granted scopes
        service_type = None
        
        # Try to extract service type from state first (most reliable)
        if state and ':' in state:
            # State format: "service_type:random_string"
            service_from_state = state.split(':')[0]
            if service_from_state in ['gmail', 'drive', 'calendar']:
                service_type = service_from_state
                print(f"DEBUG: Service type from state parameter: {service_type}")
        
        # Fallback to scope-based detection if state parsing failed
        if not service_type:
            scope = query_params.get('scope', [None])[0]
            print(f"DEBUG: State parsing failed, falling back to scope detection. Scope: {scope}")
            
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
                print("DEBUG: No scope or state parameter, defaulting to gmail")
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
            print(f"ERROR: Token exchange failed: {str(token_error)}")
            print(f"ERROR: Service type: {service_type}")
            print(f"ERROR: Code (first 20 chars): {code[:20] if code else 'None'}")
            import traceback
            traceback.print_exc()
            # Remove from processed codes if it failed
            connector_callback._processed_codes.discard(code_key)
            # Return more specific error to frontend
            error_msg = "token_exchange_failed"
            if "invalid_grant" in str(token_error).lower():
                error_msg = "code_expired_or_reused"
            elif "redirect_uri" in str(token_error).lower():
                error_msg = "redirect_uri_mismatch"
            return RedirectResponse(f"{FRONTEND_URL}/settings?error={error_msg}&details={str(token_error)[:100]}")
        
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
        print(f"ERROR: OAuth callback error: {e}")
        print(f"ERROR: Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(f"{FRONTEND_URL}/settings?error=oauth_failed&details={str(e)[:100]}")


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


def _ingest_blob_from_url(meeting_id: str, url: str, filename: str, content_type: str | None = None) -> str:
    """Download a remote file into GCS (if configured) or local uploads and return objectUri."""
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    if bucket_name:
        try:
            client = gcs_storage.Client()
            bucket = client.bucket(bucket_name)
            token = uuid.uuid4().hex
            safe_name = filename.replace("/", "_").replace("\\", "_")
            blob_path = f"meetings/{meeting_id}/{token}_{safe_name}"
            blob = bucket.blob(blob_path)
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                blob.upload_from_file(r.raw, rewind=True, content_type=content_type or r.headers.get("content-type") or "application/octet-stream")
            return f"gs://{bucket_name}/{blob_path}"
        except Exception:
            pass
    # Local fallback
    uploads_dir = _uploads_dir()
    os.makedirs(uploads_dir, exist_ok=True)
    safe_name = filename.replace("/", "_").replace("\\", "_")
    path = os.path.join(uploads_dir, f"{uuid.uuid4().hex}_{safe_name}")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    return f"file://{path}"


def _create_and_process_meeting_from_uri(title: str, object_uri: str, provider: str = "Upload") -> str:
    """Create a meeting with a given file, then transcribe and run insights synchronously."""
    meetings = _load_meetings()
    mid = uuid.uuid4().hex
    now = _now_iso()
    m: Dict[str, Any] = {
        "id": mid,
        "title": title,
        "provider": provider,
        "date": now,
        "tags": [],
        "owner": "",
        "participants": [],
        "userId": "",
        "orgId": "",
        "createdAt": now,
        "updatedAt": now,
        "notes": [],
        "agenda": [],
        "actions": [],
        "recording": {"status": "uploaded", "objectUri": object_uri},
        "insights": {"summary": "", "chapters": [], "highlights": [], "keyQuestions": [], "extractedActions": [], "edited": False, "status": "idle"},
    }
    meetings.append(m)
    _save_meetings(meetings)
    # Kick pipeline
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    loop = asyncio.get_event_loop()
    loop.run_until_complete(transcribe_meeting(mid))
    loop.run_until_complete(run_insights(mid))
    return mid

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
async def list_meetings(userId: str | None = None, orgId: str | None = None):
    meetings = _load_meetings()
    if userId or orgId:
        def match(m: Dict[str, Any]) -> bool:
            if userId and (m.get("userId") or "") != userId:
                return False
            if orgId and (m.get("orgId") or "") != orgId:
                return False
            return True
        meetings = [m for m in meetings if match(m)]
    # Lightweight list shape is fine; return full objects for now
    return {"meetings": meetings}


@app.post("/meetings")
async def create_meeting(
    title: str = Form(...),
    provider: str = Form("Zoom"),
    date: str = Form(None),
    tags: str = Form("[]"),
    owner: str = Form(None),
    participants: str = Form("[]"),
    userId: str = Form(None),
    orgId: str = Form(None),
):
    try:
        tag_list = json.loads(tags) if tags else []
    except Exception:
        tag_list = []
    try:
        participants_list = json.loads(participants) if participants else []
    except Exception:
        participants_list = []
    meetings = _load_meetings()
    mid = uuid.uuid4().hex
    now = _now_iso()
    meeting: Dict[str, Any] = {
        "id": mid,
        "title": title,
        "provider": provider or "Zoom",
        "date": date or now,
        "tags": tag_list,
        "owner": owner or "",
        "participants": participants_list,
        "userId": userId or "",
        "orgId": orgId or "",
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
    owner: str | None = Form(None),
    participants: str | None = Form(None),
    userId: str | None = Form(None),
    orgId: str | None = Form(None),
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
    if owner is not None:
        m["owner"] = owner
    if participants is not None:
        try:
            m["participants"] = json.loads(participants)
        except Exception:
            pass
    if userId is not None:
        m["userId"] = userId
    if orgId is not None:
        m["orgId"] = orgId
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

    # Prefer GCS signed URL if bucket configured
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    if bucket_name:
        try:
            client = gcs_storage.Client()
            bucket = client.bucket(bucket_name)
            token = uuid.uuid4().hex
            safe_name = filename.replace("/", "_").replace("\\", "_")
            blob_path = f"meetings/{meeting_id}/{token}_{safe_name}"
            blob = bucket.blob(blob_path)
            upload_url = blob.generate_signed_url(
                version="v4",
                expiration=600,
                method="PUT",
                content_type=contentType,
            )
            object_uri = f"gs://{bucket_name}/{blob_path}"
            # Keep minimal local map to validate after PUT if needed
            UPLOAD_TOKENS[token] = {
                "gcs": True,
                "bucket": bucket_name,
                "blob_path": blob_path,
                "contentType": contentType,
                "meetingId": meeting_id,
                "objectUri": object_uri,
                "expiresAt": time.time() + 600,
                "consumed": False,
            }
            return {"uploadUrl": upload_url, "objectUri": object_uri}
        except Exception as e:
            # Fall back to local upload if GCS fails
            pass

    # Local fallback
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


@app.get("/uploads/serve")
async def serve_uploaded(objectUri: str):
    """Serve a previously uploaded object by URI.

    Supports:
    - file://<path>     (local dev fallback)
    - gs://bucket/key   (GCS via signed URL redirect)
    """
    try:
        # GCS object handling: redirect to a short-lived signed URL for inline playback
        if objectUri.startswith("gs://"):
            # Expect format: gs://bucket/key
            try:
                _, rest = objectUri.split("gs://", 1)
                bucket_name, blob_path = rest.split("/", 1)
                client = gcs_storage.Client()
                bucket = client.bucket(bucket_name)
                blob = bucket.blob(blob_path)
                signed = blob.generate_signed_url(version="v4", expiration=600, method="GET")
                # Let the browser fetch directly from GCS
                return RedirectResponse(signed, status_code=302)
            except Exception as ge:
                return JSONResponse({"error": "gcs_serve_failed", "message": str(ge)}, status_code=500)

        # Local file path handling
        path = objectUri
        if objectUri.startswith("file://"):
            path = objectUri[len("file://"):]
        if not isinstance(path, str) or not os.path.exists(path):
            return JSONResponse({"error": "not_found"}, status_code=404)
        media_type, _ = mimetypes.guess_type(path)
        return FileResponse(path, media_type=media_type or "application/octet-stream")
    except Exception as e:
        return JSONResponse({"error": "serve_failed", "message": str(e)}, status_code=500)


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
    """Generate comprehensive AI insights for a meeting"""
    from services.meeting_ai_insights import get_insights_service
    
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
    
    # Get full transcript
    transcript_text = m.get("recording", {}).get("transcriptText", "")
    
    # If no full transcript, query RAG chunks
    if not transcript_text:
        transcript_doc = m.get("recording", {}).get("transcriptDocId")
        transcript_snips: List[str] = []
        if transcript_doc:
            # Query chunks to reconstruct transcript
            for doc, _meta in rag.query(f"summary of meeting {m.get('title','')}", top_k=10):
                transcript_snips.append(doc)
        transcript_text = "\n\n".join(transcript_snips)
    
    # Prepare metadata
    metadata = {
        "title": m.get("title", ""),
        "participants": m.get("participants", []),
        "duration": m.get("recording", {}).get("duration") or 3600,
        "date": m.get("date", "")
    }
    
    # Combine transcript and notes into grounded context
    grounded = (transcript_text + "\n\n" + notes_text).strip()

    # If we have a model key, use real LLM to extract structured insights
    try:
        from genai import configure
        import google.generativeai as genai
        configure()
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY"):
            # Resolve a working model; avoid hard-failing on unavailable names
            preferred = os.getenv("GENAI_MODEL") or os.getenv("GOOGLE_GENAI_MODEL")
            # Prefer gemini-2.5-flash by default
            candidates = [c for c in [preferred, "gemini-2.5-flash", "gemini-2.0-flash-exp", "gemini-1.5-flash", "gemini-1.5-pro"] if c]
            model = None
            last_err = None
            for name in candidates:
                try:
                    model = genai.GenerativeModel(name, generation_config={"response_mime_type": "application/json"})
                    break
                except Exception as e:
                    last_err = e
                    model = None
            if model is None:
                raise last_err or Exception("Model resolution failed")

            system = (
                "You are a meeting notes analyst. Given transcript and notes, extract strictly-typed JSON: "
                "{summary:string, chapters:[{title:string,startSec:int}], highlights:[{label:string,startSec:int,text:string}], "
                "keyQuestions:[string], extractedActions:[{title:string,assignee:string,due:string}]} "
                "Rules: output ONLY JSON, no prose."
            )
            payload = (
                f"System Instructions:\n{system}\n\n"
                f"Transcript+Notes:\n{grounded or '(empty)'}\n\n"
                f"Existing actions (may be empty):\n" + str([
                    {"title": a.get("title"), "assignee": a.get("assignee"), "due": a.get("due")}
                    for a in m.get("actions", [])[:6]
                ])
            )
            resp = model.generate_content(payload)

            # Robust JSON extraction
            raw_text = (getattr(resp, "text", None) or "").strip()
            if not raw_text and getattr(resp, "candidates", None):
                try:
                    raw_text = resp.candidates[0].content.parts[0].text  # type: ignore
                except Exception:
                    raw_text = ""
            import json as _json, re as _re
            def _try_parse(s: str):
                try:
                    return _json.loads(s)
                except Exception:
                    return None
            parsed = _try_parse(raw_text) or _try_parse("".join(_re.findall(r"\{[\s\S]*\}", raw_text)))
            if not parsed:
                raise Exception("parse_failed")

            summary = str(parsed.get("summary") or "").strip() or (grounded[:400] + ("…" if len(grounded) > 400 else ""))
            chapters = parsed.get("chapters") or []
            highlights = parsed.get("highlights") or []
            key_questions = parsed.get("keyQuestions") or []
            extracted_actions = parsed.get("extractedActions") or []
        else:
            raise Exception("No model key")
    except Exception:
        # Deterministic fallback
        summary = (grounded[:400] + ("…" if len(grounded) > 400 else "")) or f"Meeting '{m.get('title','')}' with provider {m.get('provider','')} on {m.get('date','')}"
        chapters = [
            {"title": "Introduction", "startSec": 0},
            {"title": "Discussion", "startSec": 60},
            {"title": "Decisions", "startSec": 120},
        ]
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
    userId: str | None = None,
    orgId: str | None = None,
):
    meetings = _load_meetings()
    if userId or orgId:
        def scope(m: Dict[str, Any]) -> bool:
            if userId and (m.get("userId") or "") != userId:
                return False
            if orgId and (m.get("orgId") or "") != orgId:
                return False
            return True
        meetings = [m for m in meetings if scope(m)]
    # Basic blended search over metadata + transcript index
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
        # dateRange placeholder
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
        if participants:
            parts = [p.strip().lower() for p in participants.split(",") if p.strip()]
            if parts:
                owner = (m.get("owner") or "").strip().lower()
                plist = [str(p).strip().lower() for p in (m.get("participants") or [])]
                in_owner = owner in parts if owner else False
                in_participants = any(p in plist for p in parts)
                if not (in_owner or in_participants):
                    return False
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

@app.post("/meetings/{meeting_id}/actions/{action_id}/calendar")
async def create_calendar_event_for_action(
    meeting_id: str,
    action_id: str,
    start: str = Form(...),
    end: str = Form(...),
):
    meetings = _load_meetings()
    idx = _find_meeting_idx(meetings, meeting_id)
    if idx < 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    actions = meetings[idx].get("actions", [])
    action = next((a for a in actions if a.get("id") == action_id), None)
    if not action:
        return JSONResponse({"error": "action_not_found"}, status_code=404)
    title = action.get("title") or f"Meeting Action {action_id}"
    description = f"Action from meeting {meeting_id}"
    attendees = []
    # Try to include owner and participants as attendees if they look like emails
    owner = meetings[idx].get("owner") or ""
    if "@" in owner:
        attendees.append(owner)
    for p in (meetings[idx].get("participants") or []):
        if isinstance(p, str) and "@" in p:
            attendees.append(p)
    result = calendar_service.create_event(summary=title, start_iso=start, end_iso=end, description=description, attendees=attendees)
    if not result:
        return JSONResponse({"error": "calendar_create_failed"}, status_code=500)
    # Optionally store calendar link on action
    action["calendarEventId"] = result.get("id")
    action["calendarLink"] = result.get("htmlLink")
    meetings[idx]["updatedAt"] = _now_iso()
    _save_meetings(meetings)
    return {"ok": True, "event": result, "action": action}

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

