#!/usr/bin/env python3
"""
Test script to verify frontend-backend connection
"""
import requests
import time

def test_frontend_backend_connection():
    """Test if frontend can connect to backend through the proxy"""
    try:
        # Test the frontend API proxy
        response = requests.get(
            "http://localhost:5000/api/chat/stream?prompt=Hello test",
            stream=True,
            headers={"Accept": "text/event-stream"},
            timeout=10
        )
        
        if response.status_code == 200:
            print("✅ Frontend-backend connection successful!")
            print("✅ API proxy is working correctly")
            
            # Read a few lines to verify streaming
            lines_read = 0
            for line in response.iter_lines(decode_unicode=True):
                if line.startswith("data: "):
                    print(f"  Received: {line}")
                    lines_read += 1
                    if lines_read >= 3:
                        break
            return True
        else:
            print(f"❌ Frontend-backend connection failed: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ Frontend is not running on localhost:5000")
        return False
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        return False

def main():
    print("Testing Frontend-Backend Connection...")
    print("=" * 50)
    
    if test_frontend_backend_connection():
        print("\n🎉 Integration is working!")
        print("\nNext steps:")
        print("1. Open http://localhost:5000 in your browser")
        print("2. Try uploading a document")
        print("3. Start chatting with the AI!")
    else:
        print("\n❌ Integration test failed.")
        print("Make sure both frontend and backend are running:")
        print("- Backend: cd Saku-AI-Backend && python start.py")
        print("- Frontend: cd Saku-AI-Frontend && npm run dev")

if __name__ == "__main__":
    main()
