from __future__ import annotations

import io
import os
from typing import Iterable, List, Tuple, Dict

import chromadb
from chromadb.utils import embedding_functions
from pypdf import PdfReader
import trafilatura


CHROMA_DIR = os.path.join(os.path.dirname(__file__), "data", "chroma")
COLLECTION_NAME = "docs"


def get_client() -> chromadb.Client:
    os.makedirs(CHROMA_DIR, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_DIR)


def get_collection():
    client = get_client()
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    try:
        return client.get_collection(COLLECTION_NAME, embedding_function=embedding_fn)
    except Exception:
        return client.create_collection(COLLECTION_NAME, embedding_function=embedding_fn)


def _split_paragraphs(text: str) -> List[str]:
    parts = [p.strip() for p in text.split("\n")]
    out = []
    buf: List[str] = []
    for p in parts:
        if not p:
            if buf:
                out.append(" ".join(buf))
                buf = []
            continue
        buf.append(p)
    if buf:
        out.append(" ".join(buf))
    return out


def _chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> List[str]:
    # Try paragraph-aware packing first
    paras = _split_paragraphs(text)
    chunks: List[str] = []
    cur = ""
    for para in paras:
        if not cur:
            cur = para
            continue
        if len(cur) + 1 + len(para) <= chunk_size:
            cur = cur + " " + para
        else:
            chunks.append(cur)
            # overlap tail of previous chunk
            tail = cur[-overlap:]
            cur = tail + " " + para if tail else para
    if cur:
        chunks.append(cur)
    # Fallback simple slicing if nothing produced
    if not chunks:
        text = " ".join(text.split())
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_size)
            chunks.append(text[start:end])
            start += chunk_size - overlap
    return [c.strip() for c in chunks if c.strip()]


def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    out = []
    for page in reader.pages:
        out.append(page.extract_text() or "")
    return "\n".join(out)


def extract_text_from_url(url: str) -> str:
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""
    return trafilatura.extract(downloaded) or ""


def upsert_document(doc_id: str, text: str, metadata: dict | None = None) -> int:
    collection = get_collection()
    chunks = _chunk_text(text)
    ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
    metadatas = [{"doc_id": doc_id, "chunk_index": i, **(metadata or {})} for i, _ in enumerate(chunks)]
    collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)
    return len(chunks)


def query(text: str, top_k: int = 5) -> List[Tuple[str, Dict]]:
    collection = get_collection()
    res = collection.query(query_texts=[text], n_results=top_k)
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    return list(zip(docs, metas))


def format_citations(pairs: List[Tuple[str, Dict]]) -> List[Dict]:
    citations: List[Dict] = []
    for doc, meta in pairs:
        citations.append({
            "doc_id": meta.get("doc_id"),
            "chunk_index": meta.get("chunk_index"),
            "snippet": doc[:200] + ("…" if len(doc) > 200 else "")
        })
    return citations


