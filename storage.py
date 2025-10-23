import json
import os
import time
from typing import Any, Dict, List


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CONNECTORS_PATH = os.path.join(DATA_DIR, "connectors.json")
DOCS_REGISTRY_PATH = os.path.join(DATA_DIR, "docs.json")
CONVERSATIONS_PATH = os.path.join(DATA_DIR, "conversations.json")
GOOGLE_CREDENTIALS_PATH = os.path.join(DATA_DIR, "google_credentials.json")
MEETINGS_PATH = os.path.join(DATA_DIR, "meetings.json")


def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_connectors() -> List[Dict[str, Any]]:
    ensure_dirs()
    data = _read_json(
        CONNECTORS_PATH,
        [
            {"key": "gmail", "name": "Gmail", "connected": True},
            {"key": "slack", "name": "Slack", "connected": False},
            {"key": "drive", "name": "Google Drive", "connected": False},
            {"key": "notion", "name": "Notion", "connected": False},
            {"key": "calendar", "name": "Google Calendar", "connected": False},
            {"key": "discord", "name": "Discord", "connected": False},
        ],
    )
    return data


def save_connectors(connectors: List[Dict[str, Any]]) -> None:
    ensure_dirs()
    _write_json(CONNECTORS_PATH, connectors)


def load_docs_registry() -> List[Dict[str, Any]]:
    ensure_dirs()
    return _read_json(DOCS_REGISTRY_PATH, [])


def save_docs_registry(registry: List[Dict[str, Any]]) -> None:
    ensure_dirs()
    _write_json(DOCS_REGISTRY_PATH, registry)


def load_google_credentials() -> Dict[str, Any]:
    ensure_dirs()
    return _read_json(GOOGLE_CREDENTIALS_PATH, {})


def save_google_credentials(credentials: Dict[str, Any]) -> None:
    ensure_dirs()
    _write_json(GOOGLE_CREDENTIALS_PATH, credentials)


def get_current_timestamp() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# ------- Meetings -------

def load_meetings() -> List[Dict[str, Any]]:
    ensure_dirs()
    return _read_json(MEETINGS_PATH, [])


def save_meetings(meetings: List[Dict[str, Any]]) -> None:
    ensure_dirs()
    _write_json(MEETINGS_PATH, meetings)


# ------- Conversations -------

def load_conversations() -> List[Dict[str, Any]]:
    ensure_dirs()
    data = _read_json(CONVERSATIONS_PATH, [])
    # Expect list of { id, title, createdAt, updatedAt, messages: [{role, content, createdAt}] }
    return data


def save_conversations(conversations: List[Dict[str, Any]]) -> None:
    ensure_dirs()
    _write_json(CONVERSATIONS_PATH, conversations)

