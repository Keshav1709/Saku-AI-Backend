import argparse
import json
import mimetypes
import os
import sys
import time
from datetime import datetime
from typing import Iterator

import httpx


def fail(msg: str, status: int | None = None, body: str | None = None) -> None:
    print(f"ERROR: {msg}")
    if status is not None:
        print(f"  HTTP status: {status}")
    if body:
        print(f"  Body: {body[:800]}")
    sys.exit(1)


def guess_content_type(path: str) -> str:
    ctype, _ = mimetypes.guess_type(path)
    # Ensure common types we care about
    if not ctype:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mov":
            return "video/quicktime"
        if ext in (".mp4", ".m4v"):
            return "video/mp4"
        if ext in (".wav",):
            return "audio/wav"
        if ext in (".mp3",):
            return "audio/mpeg"
        return "application/octet-stream"
    return ctype


def file_chunks(path: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk


def main() -> None:
    parser = argparse.ArgumentParser(description="Test SAKU-AI meetings pipeline end-to-end")
    parser.add_argument("--backend", default=os.environ.get("NEXT_PUBLIC_BACKEND_URL", "http://localhost:8000"), help="Backend base URL (default: http://localhost:8000)")
    parser.add_argument("--file", required=True, help="Path to local video/audio file to upload (.mov supported)")
    parser.add_argument("--title", default=None, help="Optional meeting title")
    parser.add_argument("--provider", default="Zoom", help="Meeting provider label (default: Zoom)")
    parser.add_argument("--timeout", type=float, default=180.0, help="HTTP timeout seconds (default: 180)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    backend = args.backend.rstrip("/")
    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        fail(f"File not found: {file_path}")

    filename = os.path.basename(file_path)
    content_type = guess_content_type(file_path)
    title = args.title or f"CLI Test Meeting {datetime.utcnow().isoformat(timespec='seconds')}"

    print(f"Using backend: {backend}")
    print(f"Uploading file: {file_path} (type: {content_type})")

    with httpx.Client(timeout=args.timeout) as client:
        # 1) Create meeting
        print("[1/7] Creating meeting …")
        form = {
            "title": title,
            "provider": args.provider,
            "tags": json.dumps(["cli", "test"]) or "[]",
        }
        resp = client.post(f"{backend}/meetings", data=form)
        if not resp.is_success:
            fail("Failed to create meeting", resp.status_code, resp.text)
        meeting_id = resp.json().get("id")
        if not meeting_id:
            fail("No meeting id returned", resp.status_code, resp.text)
        print(f"   → meeting_id = {meeting_id}")

        # 2) Request signed upload URL
        print("[2/7] Requesting upload URL …")
        sign_form = {"filename": filename, "contentType": content_type}
        resp = client.post(f"{backend}/meetings/{meeting_id}/upload-url", data=sign_form)
        if not resp.is_success:
            fail("Failed to get upload URL", resp.status_code, resp.text)
        meta = resp.json()
        upload_url = meta.get("uploadUrl")
        object_uri = meta.get("objectUri")
        if not upload_url or not object_uri:
            fail("Invalid sign response — missing uploadUrl/objectUri", resp.status_code, resp.text)
        print(f"   → upload_url = {upload_url}")
        print(f"   → object_uri = {object_uri}")

        # 3) PUT file to backend uploads endpoint (streamed)
        print("[3/7] Uploading file …")
        put_resp = client.put(upload_url, headers={"Content-Type": content_type}, content=file_chunks(file_path))
        if not put_resp.is_success:
            fail("Failed to upload file", put_resp.status_code, put_resp.text)
        put_json = put_resp.json()
        print(f"   → upload ok, size = {put_json.get('size')}")

        # 4) Save recording objectUri on meeting
        print("[4/7] Saving recording URI …")
        rec_form = {"objectUri": object_uri}
        resp = client.post(f"{backend}/meetings/{meeting_id}/recording", data=rec_form)
        if not resp.is_success:
            fail("Failed to save recording", resp.status_code, resp.text)
        print("   → recording saved")

        # 5) Transcribe
        print("[5/7] Transcribing …")
        resp = client.post(f"{backend}/meetings/{meeting_id}/transcribe")
        if not resp.is_success:
            fail("Failed to transcribe", resp.status_code, resp.text)
        transcript_doc_id = resp.json().get("transcriptDocId")
        print(f"   → transcript_doc_id = {transcript_doc_id}")

        # 6) Run insights
        print("[6/7] Running insights …")
        resp = client.post(f"{backend}/meetings/{meeting_id}/insights/run")
        if not resp.is_success:
            fail("Failed to run insights", resp.status_code, resp.text)
        print("   → insights ready")

        # 7) Retrieve results
        print("[7/7] Fetching results …")
        m_resp = client.get(f"{backend}/meetings/{meeting_id}")
        i_resp = client.get(f"{backend}/meetings/{meeting_id}/insights")
        if not m_resp.is_success:
            fail("Failed to fetch meeting", m_resp.status_code, m_resp.text)
        if not i_resp.is_success:
            fail("Failed to fetch insights", i_resp.status_code, i_resp.text)

        meeting = m_resp.json().get("meeting")
        insights = i_resp.json().get("insights")

    print("\n=== MEETING ===")
    if args.pretty:
        print(json.dumps(meeting, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(meeting))

    print("\n=== INSIGHTS ===")
    if args.pretty:
        print(json.dumps(insights, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(insights))


if __name__ == "__main__":
    main()

import argparse
import json
import mimetypes
import os
import sys
import time
from datetime import datetime

import httpx


def fail(msg: str, status: int | None = None, body: str | None = None) -> None:
    print(f"ERROR: {msg}")
    if status is not None:
        print(f"  HTTP status: {status}")
    if body:
        print(f"  Body: {body[:500]}")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test SAKU-AI meetings pipeline end-to-end")
    parser.add_argument("--backend", default=os.environ.get("NEXT_PUBLIC_BACKEND_URL", "http://localhost:8000"), help="Backend base URL (default: http://localhost:8000)")
    parser.add_argument("--file", required=True, help="Path to local video/audio file to upload")
    parser.add_argument("--title", default=None, help="Optional meeting title")
    parser.add_argument("--provider", default="Zoom", help="Meeting provider label (default: Zoom)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args()

    backend = args.backend.rstrip("/")
    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        fail(f"File not found: {file_path}")

    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    title = args.title or f"CLI Test Meeting {datetime.utcnow().isoformat(timespec='seconds')}"

    print(f"Using backend: {backend}")
    print(f"Uploading file: {file_path} (type: {content_type})")

    with httpx.Client(timeout=60) as client:
        # 1) Create meeting
        print("[1/7] Creating meeting …")
        form = {
            "title": title,
            "provider": args.provider,
            "tags": json.dumps(["cli", "test"]) or "[]",
        }
        resp = client.post(f"{backend}/meetings", data=form)
        if not resp.is_success:
            fail("Failed to create meeting", resp.status_code, resp.text)
        meeting_id = resp.json().get("id")
        if not meeting_id:
            fail("No meeting id returned", resp.status_code, resp.text)
        print(f"   → meeting_id = {meeting_id}")

        # 2) Request signed upload URL
        print("[2/7] Requesting upload URL …")
        sign_form = {"filename": filename, "contentType": content_type}
        resp = client.post(f"{backend}/meetings/{meeting_id}/upload-url", data=sign_form)
        if not resp.is_success:
            fail("Failed to get upload URL", resp.status_code, resp.text)
        meta = resp.json()
        upload_url = meta.get("uploadUrl")
        object_uri = meta.get("objectUri")
        if not upload_url or not object_uri:
            fail("Invalid sign response — missing uploadUrl/objectUri", resp.status_code, resp.text)
        print(f"   → upload_url = {upload_url}")
        print(f"   → object_uri = {object_uri}")

        # 3) PUT file to backend uploads endpoint
        print("[3/7] Uploading file …")
        with open(file_path, "rb") as f:
            put_resp = client.put(upload_url, content=f.read(), headers={"Content-Type": content_type})
        if not put_resp.is_success:
            fail("Failed to upload file", put_resp.status_code, put_resp.text)
        print(f"   → upload ok, size = {put_resp.json().get('size')}")

        # 4) Save recording objectUri on meeting
        print("[4/7] Saving recording URI …")
        rec_form = {"objectUri": object_uri}
        resp = client.post(f"{backend}/meetings/{meeting_id}/recording", data=rec_form)
        if not resp.is_success:
            fail("Failed to save recording", resp.status_code, resp.text)
        print("   → recording saved")

        # 5) Transcribe
        print("[5/7] Transcribing …")
        resp = client.post(f"{backend}/meetings/{meeting_id}/transcribe")
        if not resp.is_success:
            fail("Failed to transcribe", resp.status_code, resp.text)
        transcript_doc_id = resp.json().get("transcriptDocId")
        print(f"   → transcript_doc_id = {transcript_doc_id}")

        # Small wait for any async side-effects (not strictly needed for stub)
        time.sleep(0.2)

        # 6) Run insights
        print("[6/7] Running insights …")
        resp = client.post(f"{backend}/meetings/{meeting_id}/insights/run")
        if not resp.is_success:
            fail("Failed to run insights", resp.status_code, resp.text)
        print("   → insights ready")

        # 7) Retrieve results
        print("[7/7] Fetching results …")
        m_resp = client.get(f"{backend}/meetings/{meeting_id}")
        i_resp = client.get(f"{backend}/meetings/{meeting_id}/insights")
        if not m_resp.is_success:
            fail("Failed to fetch meeting", m_resp.status_code, m_resp.text)
        if not i_resp.is_success:
            fail("Failed to fetch insights", i_resp.status_code, i_resp.text)

        meeting = m_resp.json().get("meeting")
        insights = i_resp.json().get("insights")

    print("\n=== MEETING ===")
    if args.pretty:
        print(json.dumps(meeting, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(meeting))

    print("\n=== INSIGHTS ===")
    if args.pretty:
        print(json.dumps(insights, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(insights))


if __name__ == "__main__":
    main()


