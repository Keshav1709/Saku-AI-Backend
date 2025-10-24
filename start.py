#!/usr/bin/env python3
"""
Startup script for Saku AI Backend
"""
import os
import sys
import uvicorn
from dotenv import load_dotenv

def main():
    # Load .env so PORT and other vars can be set there
    try:
        load_dotenv()
    except Exception:
        pass
    # Default to 8080 for local development
    port = int(os.getenv("PORT", 8080))
    host = os.getenv("HOST", "0.0.0.0")
    
    print(f"Starting Saku AI Backend on {host}:{port}")
    print("Make sure to set GEMINI_API_KEY environment variable for AI functionality")
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=True,
        log_level="info"
    )

if __name__ == "__main__":
    main()

