import os
from dotenv import load_dotenv

# Load primary .env, then optionally test.env (won't override already set vars)
load_dotenv()
load_dotenv('test.env', override=False)

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/connectors/callback")

# Frontend URL
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# Backend URL
BACKEND_URL = os.getenv("NEXT_PUBLIC_BACKEND_URL", "http://localhost:8000")

# Database and storage paths
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)
