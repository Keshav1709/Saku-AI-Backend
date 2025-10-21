## Saku-AI Backend Phased Plan (Refreshed)

### Phase A — Persistence & Conversation APIs
- Conversation model: done (create/load/append).
- SSE chat writes: assistant final persisted (pending: persist user message pre-stream).
- Session scoping: pending (userId on RAG records + filters).

### Phase B — Workflows Service
- CRUD: done.
- Runs: run stub + list runs: done (status/logs/SSE pending).
- Link runs to conversations: pending.

### Phase B2 — Meetings Service (Core)
- Meetings CRUD: done.
- Notes/Agenda/Actions endpoints: done.
- Recording ingest:
  - POST `/meetings/{id}/upload-url`: done (signed URL; returns `objectUri`).
  - POST `/meetings/{id}/recording`: done (save `recordingUri`, set status `uploaded`).
- Transcription stub: POST `/meetings/{id}/transcribe`: done (stores `transcriptDocId`, indexes transcript).
- Insights stub: POST `/meetings/{id}/insights/run`, GET `/meetings/{id}/insights`: done.

### Phase C — Storage & Ingestion Pipeline
- GCS raw text storage for docs: done (with local fallback).
- Re-embed endpoint from stored raw text: done.
- Delete hygiene: done.

### Phase D — Search & Ranking
- Blended ranking (embedding + keyword + recency): done (`/search`).
- Next: add userId scoping to rank filters.

---

## Meetings Backend Roadmap to Figma

### M1. Upload & Metadata (stabilize)
- Ensure server-side upload route is single-read safe (no double body reads).
- Save `duration`, `size`, and `contentType` alongside `recordingUri`.
- Return a processing token for client-side polling.

Status: Implemented in FastAPI
- Added PUT `/uploads/{token}` single-read handler. Token created by POST `/meetings/{id}/upload-url`.
- Persist `recording.objectUri`, `size`, `contentType` (duration stubbed `null` until FFmpeg is added).
- Frontend uses signed URL flow; processing reflected via `recording.status` transitions.

### M2. Transcription (real pipeline)
- Extract audio from video (FFmpeg on Cloud Run) → `audioUri`.
- Transcribe with Vertex STT/Whisper. Store `TranscriptChunk {startSec,endSec,text}` in GCS + registry.
- Index chunks in RAG with `ref = {meetingId,chunk_index,startSec,endSec}`.

Status: Stub implemented
- POST `/meetings/{id}/transcribe` builds a transcript placeholder from agenda/notes and indexes into RAG with `meetingId` metadata. Stores `transcriptDocId`.

### M3. Insights (grounded)
- Build insights job: use ranked transcript chunks to generate:
  - `summary`
  - `chapters[] {title,startSec}`
  - `highlights[] {label,startSec,text}`
  - `keyQuestions[]`
  - `extractedActions[] {title,assignee,due}`
- Persist in `meeting.insights` with `edited: boolean`.
- GET supports partial availability and progress states.

Status: Implemented (stubbed analysis)
- POST `/meetings/{id}/insights/run` generates `summary`, `chapters`, `highlights`, `keyQuestions`, `extractedActions` and marks `status` → `ready`.
- GET `/meetings/{id}/insights` returns latest insights; supports `status` transitions.
 - Next: PUT `/meetings/{id}/insights` to save edits (`edited: true`).

### M4. Agenda & Actions Automation
- Map agenda items to transcript spans; update endpoints for ordering and linkage.
- Calendar integration: Add-to-Calendar endpoint for actions with due date.

Status: Partially implemented
- POST endpoints for notes/agenda/actions added: `/meetings/{id}/notes`, `/agenda`, `/actions`.
- Calendar integration pending (future increment).

### M5. Meetings Search & Retrieval
- `/meetings/search?q=&tags=&participants=&provider=&dateRange=` blending keyword + embedding across transcript + metadata.

Status: Implemented (basic blend)
- GET `/meetings/search` blends keyword over metadata with transcript hits from RAG (meetingId tagged chunks).

Additional implemented since last update
- ffprobe-based `duration` extraction (best-effort if `ffprobe` present).
- Transcription indexing writes timecoded chunks `{startSec,endSec,chunk_index}` to RAG.
- GET `/meetings/{id}/transcript` returns full text and timecoded `chunks[]`.
- GET `/meetings/{id}/progress` publishes recording + insights states.
- POST `/meetings/{id}/actions/{action_id}/toggle` flips done/open.

### M6. Security & Observability
- Auth scoping (userId/orgId on meetings/chunks/embeddings).
- Structured logs with `correlationId` across upload → transcribe → insights.
- Quotas and rate limits (minutes/day, jobs/user).

---

## Immediate Next Actions (to unblock UI)
1) Upload endpoint hardening: never read `request.body` twice; prefer `formData()` with fallback to `arrayBuffer()` only when not parsed. [done]
2) Return a processing state and surface `status` transitions: `uploaded → transcribing → analyzing → ready`. [done]
3) Minimal insights renderer contract: make sure GET `/meetings/{id}/insights` returns Summary/Chapters/Highlights/Actions consistently (even stubbed) for UI. [done]
4) Add `/meetings/{id}/progress` endpoint (optional) or embed progress in GET meeting. [done]

## Remaining / Next Steps
- Real transcription pipeline: ffmpeg audio extract → STT (Vertex/Whisper) → chunk `{startSec,endSec,text}` → store in GCS + index (meetingId, chunk_index, timecodes).
- Media storage hygiene: move recordings to GCS (signed PUT/GET); delete transcript chunks and objects on meeting delete.
- Auth/org scoping: add `userId/orgId` to meetings and RAG chunks; filter on all endpoints/search/transcript.
- Search upgrades: add participants filter; robust `dateRange`; include agenda/actions text; tune blending weights.
- Insights robustness: PUT `/meetings/{id}/insights` to save edits and set `edited:true`; idempotent re-runs.
- Calendar integration: Add-to-Calendar for actions with due date using Google auth.
- Agenda/actions UX: map agenda items to transcript spans; ordering endpoints; edit/delete endpoints for notes/agenda/actions.
- Observability & limits: correlationId across upload→transcribe→insights; structured logs/metrics; quotas/rate limits.
- Frontend wiring: show transcript chunks/timecodes; consume `/progress`; toggle action; enhanced search filters.
- Deployability: ensure ffmpeg in container; env/keys for GCS; update cloudbuild.yaml.


