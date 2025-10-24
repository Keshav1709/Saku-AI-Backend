import os
import json
import base64
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import storage
from email.mime.text import MIMEText
import base64 as _b64

# Import config to ensure environment variables are loaded
import config

# Google OAuth configuration
CLIENT_ID = config.GOOGLE_CLIENT_ID
CLIENT_SECRET = config.GOOGLE_CLIENT_SECRET
REDIRECT_URI = config.GOOGLE_REDIRECT_URI

# OAuth scopes
GMAIL_SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify'
]

DRIVE_SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/drive.file'
]

CALENDAR_SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/calendar.events'
]

class GoogleAuthService:
    def __init__(self):
        print(f"DEBUG: Initializing GoogleAuthService")
        print(f"DEBUG: CLIENT_ID: {CLIENT_ID}")
        print(f"DEBUG: CLIENT_SECRET: {'***' if CLIENT_SECRET else 'None'}")
        print(f"DEBUG: REDIRECT_URI: {REDIRECT_URI}")
        
        self.client_config = {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI]
            }
        }
        print(f"DEBUG: Client config created: {self.client_config}")
    
    def get_auth_url(self, service_type: str, state: str = None) -> str:
        """Generate Google OAuth authorization URL with service type in state"""
        if service_type == "gmail":
            scopes = GMAIL_SCOPES
        elif service_type == "drive":
            scopes = DRIVE_SCOPES
        elif service_type == "calendar":
            scopes = CALENDAR_SCOPES
        else:
            raise ValueError(f"Unknown service type: {service_type}")
        
        # Use the original redirect URI (single URI approach)
        flow = Flow.from_client_config(
            self.client_config,
            scopes=scopes,
            redirect_uri=REDIRECT_URI
        )
        
        # Encode service type in the state parameter
        # CRITICAL: Pass state to authorization_url() method, not flow.state
        if state:
            # Prepend service type to state for reliable detection
            custom_state = f"{service_type}:{state}"
        else:
            custom_state = f"{service_type}:{uuid.uuid4().hex}"
        
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            state=custom_state  # Pass state here to prevent override
        )
        
        print(f"DEBUG: Generated auth URL with state: {custom_state}")
        
        return auth_url
    
    def exchange_code_for_token(self, service_type: str, authorization_code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token"""
        import requests
        
        if service_type == "gmail":
            scopes = GMAIL_SCOPES
        elif service_type == "drive":
            scopes = DRIVE_SCOPES
        elif service_type == "calendar":
            scopes = CALENDAR_SCOPES
        else:
            raise ValueError(f"Unknown service type: {service_type}")
        
        # Manually exchange the authorization code for tokens
        # This bypasses the oauthlib scope validation that causes issues when Google adds OpenID scopes
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            'code': authorization_code,
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'redirect_uri': REDIRECT_URI,
            'grant_type': 'authorization_code'
        }
        
        print(f"DEBUG: Exchanging authorization code for tokens (service: {service_type})")
        
        try:
            response = requests.post(token_url, data=token_data)
            response.raise_for_status()
            token_response = response.json()
            
            print(f"DEBUG: Token exchange successful")
            print(f"DEBUG: Received token with scopes: {token_response.get('scope', 'N/A')}")
            
            # Create credentials object manually
            from google.oauth2.credentials import Credentials
            from datetime import datetime, timedelta
            
            expiry = None
            if 'expires_in' in token_response:
                expiry = datetime.utcnow() + timedelta(seconds=token_response['expires_in'])
            
            credentials = Credentials(
                token=token_response['access_token'],
                refresh_token=token_response.get('refresh_token'),
                token_uri=token_url,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                scopes=token_response.get('scope', '').split() if token_response.get('scope') else scopes,
                expiry=expiry
            )
            
        except requests.exceptions.HTTPError as e:
            print(f"ERROR: HTTP error during token exchange: {e}")
            print(f"ERROR: Response: {e.response.text if e.response else 'N/A'}")
            if "invalid_grant" in str(e).lower() or (e.response and "invalid_grant" in e.response.text.lower()):
                raise Exception("Authorization code expired or invalid. Please try connecting again.")
            raise Exception(f"Token exchange failed: {str(e)}")
        except Exception as e:
            print(f"ERROR: Token exchange failed: {str(e)}")
            raise e
        
        # Store credentials with all required fields for token refresh
        creds_data = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes,
            'expiry': credentials.expiry.isoformat() if credentials.expiry else None
        }
        
        self.save_credentials(service_type, creds_data)
        
        return {
            'access_token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'expires_in': 3600,  # Google tokens typically expire in 1 hour
            'token_type': 'Bearer'
        }
    
    def save_credentials(self, service_type: str, credentials: Dict[str, Any]):
        """Save credentials to storage"""
        creds_data = {
            'service': service_type,
            'credentials': credentials,
            'timestamp': storage.get_current_timestamp()
        }
        
        # Load existing credentials
        existing_creds = storage.load_google_credentials()
        existing_creds[service_type] = creds_data
        storage.save_google_credentials(existing_creds)
    
    def get_credentials(self, service_type: str) -> Optional[Credentials]:
        """Get stored credentials for a service"""
        creds_data = storage.load_google_credentials().get(service_type)
        if not creds_data:
            return None
        
        # Parse expiry if it exists
        expiry = None
        if 'expiry' in creds_data['credentials'] and creds_data['credentials']['expiry']:
            from datetime import datetime
            expiry = datetime.fromisoformat(creds_data['credentials']['expiry'])
        
        credentials = Credentials(
            token=creds_data['credentials']['token'],
            refresh_token=creds_data['credentials']['refresh_token'],
            token_uri=creds_data['credentials']['token_uri'],
            client_id=creds_data['credentials']['client_id'],
            client_secret=creds_data['credentials']['client_secret'],
            scopes=creds_data['credentials']['scopes'],
            expiry=expiry
        )
        
        # Refresh token if needed
        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                    # Update stored credentials
                    self.save_credentials(service_type, {
                        'token': credentials.token,
                        'refresh_token': credentials.refresh_token,
                        'token_uri': credentials.token_uri,
                        'client_id': credentials.client_id,
                        'client_secret': credentials.client_secret,
                        'scopes': credentials.scopes,
                        'expiry': credentials.expiry.isoformat() if credentials.expiry else None
                    })
                except Exception as e:
                    print(f"Failed to refresh credentials: {e}")
                    return None
        
        return credentials
    
    def revoke_credentials(self, service_type: str):
        """Revoke and remove credentials for a service"""
        credentials = self.get_credentials(service_type)
        if credentials:
            try:
                credentials.revoke(Request())
            except Exception as e:
                print(f"Failed to revoke credentials: {e}")
        
        # Remove from storage
        existing_creds = storage.load_google_credentials()
        if service_type in existing_creds:
            del existing_creds[service_type]
            storage.save_google_credentials(existing_creds)

# Gmail API service
class GmailService:
    def __init__(self, auth_service: GoogleAuthService):
        self.auth_service = auth_service
    
    def get_service(self):
        """Get Gmail API service"""
        credentials = self.auth_service.get_credentials('gmail')
        if not credentials:
            raise Exception("Gmail not authenticated")
        
        return build('gmail', 'v1', credentials=credentials)
    
    def get_messages(self, max_results: int = 10, query: str = None) -> list:
        """Get Gmail messages"""
        try:
            service = self.get_service()
            results = service.users().messages().list(
                userId='me',
                maxResults=max_results,
                q=query
            ).execute()
            
            messages = results.get('messages', [])
            
            detailed_messages = []
            for message in messages:
                msg = service.users().messages().get(
                    userId='me',
                    id=message['id']
                ).execute()
                
                headers = msg['payload'].get('headers', [])
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
                sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
                date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown Date')
                
                detailed_messages.append({
                    'id': message['id'],
                    'subject': subject,
                    'sender': sender,
                    'date': date,
                    'snippet': msg.get('snippet', ''),
                    'threadId': msg.get('threadId')
                })
            
            return detailed_messages
            
        except HttpError as error:
            print(f"Gmail API error: {error}")
            return []
        except Exception as e:
            print(f"Gmail service error: {e}")
            return []

    def _encode_message(self, to: str, subject: str, body: str, headers: dict | None = None) -> str:
        """Create base64url-encoded RFC-2822 message body."""
        mime = MIMEText(body or "")
        mime['To'] = to
        mime['Subject'] = subject or ''
        if headers:
            for k, v in headers.items():
                if k.lower() in {"to", "subject"}:
                    continue
                mime[k] = v
        raw_bytes = mime.as_bytes()
        return _b64.urlsafe_b64encode(raw_bytes).decode('utf-8')

    def send_message(self, to: str, subject: str, body: str, thread_id: str | None = None, headers: dict | None = None) -> dict:
        """Send an email via Gmail API. Optionally attach to a thread."""
        try:
            service = self.get_service()
            raw = self._encode_message(to, subject, body, headers)
            payload: Dict[str, Any] = { 'raw': raw }
            if thread_id:
                payload['threadId'] = thread_id
            sent = service.users().messages().send(userId='me', body=payload).execute()
            return { 'id': sent.get('id'), 'threadId': sent.get('threadId') }
        except HttpError as error:
            print(f"Gmail send error: {error}")
            return {}
        except Exception as e:
            print(f"Gmail send service error: {e}")
            return {}

    def create_draft(self, to: str, subject: str, body: str, thread_id: str | None = None, headers: dict | None = None) -> dict:
        """Create a draft email."""
        try:
            service = self.get_service()
            raw = self._encode_message(to, subject, body, headers)
            message: Dict[str, Any] = { 'raw': raw }
            if thread_id:
                message['threadId'] = thread_id
            draft = service.users().drafts().create(userId='me', body={'message': message}).execute()
            return { 'id': draft.get('id'), 'message': draft.get('message', {}) }
        except HttpError as error:
            print(f"Gmail draft error: {error}")
            return {}
        except Exception as e:
            print(f"Gmail draft service error: {e}")
            return {}

# Google Drive API service
class DriveService:
    def __init__(self, auth_service: GoogleAuthService):
        self.auth_service = auth_service
    
    def get_service(self):
        """Get Drive API service"""
        credentials = self.auth_service.get_credentials('drive')
        if not credentials:
            raise Exception("Google Drive not authenticated")
        
        return build('drive', 'v3', credentials=credentials)
    
    def get_files(self, max_results: int = 10, query: str = None) -> list:
        """Get Google Drive files"""
        try:
            service = self.get_service()
            results = service.files().list(
                pageSize=max_results,
                q=query,
                fields="nextPageToken, files(id, name, mimeType, size, createdTime, modifiedTime)"
            ).execute()
            
            files = results.get('files', [])
            return [
                {
                    'id': file['id'],
                    'name': file['name'],
                    'mimeType': file.get('mimeType', ''),
                    'size': file.get('size', '0'),
                    'createdTime': file.get('createdTime', ''),
                    'modifiedTime': file.get('modifiedTime', '')
                }
                for file in files
            ]
            
        except HttpError as error:
            print(f"Drive API error: {error}")
            return []
        except Exception as e:
            print(f"Drive service error: {e}")
            return []
    
    def download_file(self, file_id: str) -> bytes:
        """Download a file from Google Drive"""
        try:
            service = self.get_service()
            request = service.files().get_media(fileId=file_id)
            return request.execute()
        except HttpError as error:
            print(f"Drive download error: {error}")
            return b''
        except Exception as e:
            print(f"Drive download service error: {e}")
            return b''

# Calendar API service
class CalendarService:
    def __init__(self, auth_service: GoogleAuthService):
        self.auth_service = auth_service
    
    def get_service(self):
        """Get Calendar API service"""
        credentials = self.auth_service.get_credentials('calendar')
        if not credentials:
            raise Exception("Google Calendar not authenticated")
        
        return build('calendar', 'v3', credentials=credentials)
    
    def get_events(self, max_results: int = 10, time_min: str = None) -> list:
        """Get Google Calendar events"""
        try:
            service = self.get_service()
            now = datetime.utcnow().isoformat() + 'Z' if not time_min else time_min
            
            events_result = service.events().list(
                calendarId='primary',
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            events = events_result.get('items', [])
            return [
                {
                    'id': event['id'],
                    'summary': event.get('summary', 'No Title'),
                    'start': event.get('start', {}),
                    'end': event.get('end', {}),
                    'description': event.get('description', ''),
                    'location': event.get('location', '')
                }
                for event in events
            ]
            
        except HttpError as error:
            print(f"Calendar API error: {error}")
            return []

    def create_event(self, summary: str, start_iso: str, end_iso: str, description: str = "", attendees: list | None = None) -> dict:
        """Create a calendar event on the primary calendar.
        start_iso/end_iso should be RFC3339 timestamps, e.g. 2025-10-21T07:30:00Z
        """
        try:
            service = self.get_service()
            event_body = {
                'summary': summary or 'Untitled',
                'description': description or '',
                'start': {'dateTime': start_iso},
                'end': {'dateTime': end_iso},
            }
            if attendees:
                event_body['attendees'] = [{'email': a} if isinstance(a, str) else a for a in attendees]
            created = service.events().insert(calendarId='primary', body=event_body).execute()
            return {
                'id': created.get('id'),
                'htmlLink': created.get('htmlLink'),
                'status': created.get('status', 'confirmed')
            }
        except HttpError as error:
            print(f"Calendar create_event error: {error}")
            return {}
        except Exception as e:
            print(f"Calendar create_event service error: {e}")
            return {}
        except Exception as e:
            print(f"Calendar service error: {e}")
            return []
