from __future__ import annotations

import os
from typing import Optional

try:
    from google.cloud import documentai
except Exception:  # library may be missing in some environments
    documentai = None  # type: ignore


def extract_text_with_document_ai(file_bytes: bytes,
                                  project_id: Optional[str] = None,
                                  location: Optional[str] = None,
                                  processor_id: Optional[str] = None,
                                  mime_type: str = "application/pdf") -> str:
    """Run OCR via Google Document AI. Returns plain text or empty string on failure.

    This function is intentionally defensive: if Document AI is not configured
    or credentials are missing/insufficient, it will return an empty string
    rather than raising, allowing callers to gracefully fall back.
    """
    try:
        if documentai is None:
            return ""

        effective_project_id = (
            project_id
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("PROJECT_ID")
        )
        # Support multiple env var names for location
        effective_location = (
            location
            or os.getenv("DOCAI_LOCATION")
            or os.getenv("DOC_AI_LOCATION")
            or os.getenv("DOCUMENTAI_LOCATION")
            or os.getenv("GOOGLE_CLOUD_LOCATION")
            or "us"
        )
        # Accept either ID or full resource name; multiple env keys
        env_processor_name = (
            os.getenv("DOCAI_PROCESSOR_NAME")
            or os.getenv("DOC_AI_PROCESSOR_NAME")
            or os.getenv("DOCUMENTAI_PROCESSOR_NAME")
            or os.getenv("DOCUMENT_AI_PROCESSOR_NAME")
        )
        env_processor_id = (
            processor_id
            or os.getenv("DOCAI_PROCESSOR_ID")
            or os.getenv("DOC_AI_PROCESSOR_ID")
            or os.getenv("DOCUMENTAI_PROCESSOR_ID")
            or os.getenv("DOCUMENT_AI_PROCESSOR_ID")
        )

        # If full processor name provided, use it; else require project and id
        processor_path_override = None
        if env_processor_name and "/processors/" in env_processor_name:
            processor_path_override = env_processor_name

        if not processor_path_override and (not effective_project_id or not env_processor_id):
            return ""

        # Important: set regional API endpoint
        api_endpoint = f"{effective_location}-documentai.googleapis.com"
        try:
            client = documentai.DocumentProcessorServiceClient(
                client_options={"api_endpoint": api_endpoint}
            )
        except Exception:
            # Fallback without explicit endpoint (older libs); still try
            client = documentai.DocumentProcessorServiceClient()
        
        print(
            f"INFO: Running Document AI OCR (project={effective_project_id}, "
            f"location={effective_location}, processor={env_processor_id or processor_path_override})"
        )
        if processor_path_override:
            name = processor_path_override
        else:
            name = client.processor_path(effective_project_id, effective_location, env_processor_id)  # type: ignore[arg-type]

        raw_document = documentai.RawDocument(content=file_bytes, mime_type=mime_type)
        request = documentai.ProcessRequest(name=name, raw_document=raw_document)
        result = client.process_document(request=request)
        doc = result.document
        text = (doc.text or "").strip()
        print(f"INFO: Document AI OCR extracted {len(text)} chars")
        return text
    except Exception as e:
        # Non-fatal: surface as empty string so the pipeline keeps working
        print(f"WARN: Document AI OCR failed: {e}")
        return ""


