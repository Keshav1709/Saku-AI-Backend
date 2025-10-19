#!/usr/bin/env python3
"""
Startup script for Saku AI Backend
"""
import os
import sys
import uvicorn

def main():
    # Set default port
    port = int(os.getenv("PORT", 8000))
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

