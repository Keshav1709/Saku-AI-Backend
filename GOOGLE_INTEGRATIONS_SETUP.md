# Google Integrations Setup Guide

This guide will help you set up Google OAuth integrations for Gmail, Google Drive, and Google Calendar in your SakuAI application.

## Prerequisites

1. A Google Cloud Platform account
2. Python backend dependencies installed
3. Node.js frontend dependencies installed

## Step 1: Google Cloud Console Setup

### 1.1 Create a Google Cloud Project
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Note down your project ID

### 1.2 Enable Required APIs
1. Navigate to "APIs & Services" > "Library"
2. Enable the following APIs:
   - Gmail API
   - Google Drive API
   - Google Calendar API

### 1.3 Create OAuth 2.0 Credentials
1. Go to "APIs & Services" > "Credentials"
2. Click "Create Credentials" > "OAuth 2.0 Client IDs"
3. Choose "Web application" as the application type
4. Add authorized redirect URIs:
   - `http://localhost:8000/connectors/callback` (for development)
   - `https://yourdomain.com/connectors/callback` (for production)
5. Download the credentials JSON file

## Step 2: Backend Configuration

### 2.1 Install Dependencies
```bash
cd Saku-AI-Backend
pip install -r requirements.txt
```

### 2.2 Environment Variables
Create a `.env` file in the `Saku-AI-Backend` directory:

```env
# Google OAuth Configuration
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here
GOOGLE_REDIRECT_URI=http://localhost:8000/connectors/callback

# Frontend URL
FRONTEND_URL=http://localhost:3000 or 5000
# Backend URL
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
```

### 2.3 Start the Backend
```bash
cd Saku-AI-Backend
python main.py
```

## Step 3: Frontend Configuration

### 3.1 Environment Variables
Create a `.env.local` file in the `Saku-AI-Frontend` directory:

```env
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
```

### 3.2 Start the Frontend
```bash
cd Saku-AI-Frontend
npm run dev
```

## Step 4: Testing the Integration

### 4.1 Access Settings
1. Navigate to `http://localhost:3000/settings`
2. Click on the "Integrations" tab

### 4.2 Connect Google Services
1. Find the "Google Services" section
2. Click "Connect" next to Gmail, Google Drive, or Google Calendar
3. You'll be redirected to Google's OAuth consent screen
4. Grant the necessary permissions
5. You'll be redirected back to the settings page with the service connected

### 4.3 View Integration Data
1. Navigate to `http://localhost:3000/integrations`
2. Use the tabs to view data from connected services:
   - Gmail: View recent emails
   - Drive: View files and folders
   - Calendar: View upcoming events

## Step 5: Production Deployment

### 5.1 Update OAuth Credentials
1. In Google Cloud Console, update the redirect URIs to include your production domain
2. Update the environment variables with production URLs

### 5.2 Security Considerations
- Store OAuth credentials securely (use environment variables or secret management)
- Use HTTPS in production
- Implement proper error handling and logging
- Consider implementing token refresh logic

## API Endpoints

### Backend Endpoints
- `GET /connectors` - List all available connectors
- `GET /connectors/{key}/auth-url` - Get OAuth URL for a service
- `GET /connectors/callback` - OAuth callback handler
- `GET /integrations/gmail/messages` - Fetch Gmail messages
- `GET /integrations/drive/files` - Fetch Drive files
- `GET /integrations/calendar/events` - Fetch Calendar events
- `POST /integrations/disconnect` - Disconnect a service

### Frontend API Routes
- `GET /api/connectors` - Proxy to backend connectors endpoint
- `POST /api/connectors` - Toggle connector status
- `GET /api/connectors/auth-url` - Get OAuth URL
- `GET /api/integrations` - Fetch integration data

## Troubleshooting

### Common Issues

1. **OAuth Error: redirect_uri_mismatch**
   - Ensure the redirect URI in Google Cloud Console matches exactly
   - Check that the environment variable `GOOGLE_REDIRECT_URI` is correct

2. **API Errors: insufficient_permissions**
   - Verify that the required APIs are enabled in Google Cloud Console
   - Check that the OAuth scopes are correctly configured

3. **Connection Issues**
   - Ensure both backend and frontend are running
   - Check that environment variables are properly set
   - Verify network connectivity

4. **Token Expiration**
   - The application handles token refresh automatically
   - If issues persist, try disconnecting and reconnecting the service

### Debug Mode
To enable debug logging, set the following environment variable:
```env
DEBUG=true
```

## Security Notes

- Never commit OAuth credentials to version control
- Use environment variables for all sensitive configuration
- Implement proper CORS settings for production
- Consider implementing rate limiting for API endpoints
- Use HTTPS in production environments

## Gmail Data Extraction for RAG Integration

This section provides detailed guidance on extracting Gmail data and integrating it with a Retrieval-Augmented Generation (RAG) system for enhanced AI responses.

### 6.1 Gmail Data Extraction Process

#### 6.1.1 Authentication and Service Setup
Once Gmail is connected through the OAuth flow, you can extract emails using the following process:

```python
# In your backend service (e.g., rag.py or a new gmail_extractor.py)
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import base64
import json
from typing import List, Dict, Any

class GmailDataExtractor:
    def __init__(self, credentials: Credentials):
        self.service = build('gmail', 'v1', credentials=credentials)
    
    def extract_emails(self, query: str = '', max_results: int = 100) -> List[Dict[str, Any]]:
        """
        Extract emails from Gmail based on query parameters
        
        Args:
            query: Gmail search query (e.g., 'is:unread', 'from:example@gmail.com')
            max_results: Maximum number of emails to retrieve
            
        Returns:
            List of processed email data
        """
        try:
            # Get list of message IDs
            results = self.service.users().messages().list(
                userId='me', 
                q=query, 
                maxResults=max_results
            ).execute()
            
            messages = results.get('messages', [])
            email_data = []
            
            for msg in messages:
                msg_id = msg['id']
                # Get full message details
                message = self.service.users().messages().get(
                    userId='me', 
                    id=msg_id, 
                    format='full'
                ).execute()
                
                # Process and extract email content
                processed_email = self._process_email_message(message)
                if processed_email:
                    email_data.append(processed_email)
            
            return email_data
            
        except Exception as e:
            print(f"Error extracting emails: {str(e)}")
            return []
    
    def _process_email_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Process individual email message and extract relevant data"""
        try:
            payload = message['payload']
            headers = payload.get('headers', [])
            
            # Extract basic email information
            email_data = {
                'id': message['id'],
                'thread_id': message['threadId'],
                'snippet': message.get('snippet', ''),
                'timestamp': int(message['internalDate']) / 1000,  # Convert to seconds
                'subject': self._get_header_value(headers, 'Subject'),
                'from': self._get_header_value(headers, 'From'),
                'to': self._get_header_value(headers, 'To'),
                'date': self._get_header_value(headers, 'Date'),
                'body_text': '',
                'body_html': '',
                'labels': message.get('labelIds', [])
            }
            
            # Extract email body (both text and HTML)
            body_data = self._extract_email_body(payload)
            email_data.update(body_data)
            
            return email_data
            
        except Exception as e:
            print(f"Error processing email message: {str(e)}")
            return None
    
    def _get_header_value(self, headers: List[Dict], name: str) -> str:
        """Extract header value by name"""
        for header in headers:
            if header['name'].lower() == name.lower():
                return header['value']
        return ''
    
    def _extract_email_body(self, payload: Dict[str, Any]) -> Dict[str, str]:
        """Extract text and HTML body from email payload"""
        body_text = ''
        body_html = ''
        
        if 'parts' in payload:
            # Multipart message
            for part in payload['parts']:
                mime_type = part.get('mimeType', '')
                body_data = part.get('body', {}).get('data', '')
                
                if mime_type == 'text/plain' and body_data:
                    body_text = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
                elif mime_type == 'text/html' and body_data:
                    body_html = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
        else:
            # Single part message
            mime_type = payload.get('mimeType', '')
            body_data = payload.get('body', {}).get('data', '')
            
            if body_data:
                decoded_body = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
                if mime_type == 'text/plain':
                    body_text = decoded_body
                elif mime_type == 'text/html':
                    body_html = decoded_body
        
        return {
            'body_text': body_text,
            'body_html': body_html
        }
```

#### 6.1.2 Email Preprocessing for RAG
Before integrating with RAG, emails need to be preprocessed:

```python
import re
from datetime import datetime
from typing import List, Dict, Any

class EmailPreprocessor:
    def __init__(self):
        self.clean_patterns = [
            (r'<[^>]+>', ''),  # Remove HTML tags
            (r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', ''),  # Remove URLs
            (r'\s+', ' '),  # Replace multiple spaces with single space
        ]
    
    def preprocess_emails(self, emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Preprocess emails for RAG integration"""
        processed_emails = []
        
        for email in emails:
            processed_email = {
                'id': email['id'],
                'thread_id': email['thread_id'],
                'subject': self._clean_text(email['subject']),
                'from': email['from'],
                'to': email['to'],
                'date': email['date'],
                'timestamp': email['timestamp'],
                'content': self._create_rag_content(email),
                'metadata': {
                    'labels': email['labels'],
                    'snippet': email['snippet'],
                    'word_count': len(email['body_text'].split()),
                    'is_html': bool(email['body_html'])
                }
            }
            processed_emails.append(processed_email)
        
        return processed_emails
    
    def _clean_text(self, text: str) -> str:
        """Clean text by removing unwanted patterns"""
        cleaned = text
        for pattern, replacement in self.clean_patterns:
            cleaned = re.sub(pattern, replacement, cleaned)
        return cleaned.strip()
    
    def _create_rag_content(self, email: Dict[str, Any]) -> str:
        """Create content suitable for RAG processing"""
        content_parts = []
        
        # Add subject
        if email['subject']:
            content_parts.append(f"Subject: {email['subject']}")
        
        # Add sender information
        if email['from']:
            content_parts.append(f"From: {email['from']}")
        
        # Add date
        if email['date']:
            content_parts.append(f"Date: {email['date']}")
        
        # Add body content (prefer text over HTML)
        body_content = email['body_text'] if email['body_text'] else email['body_html']
        if body_content:
            cleaned_body = self._clean_text(body_content)
            content_parts.append(f"Content: {cleaned_body}")
        
        return '\n\n'.join(content_parts)
```

### 6.2 RAG Integration Implementation

#### 6.2.1 Vector Database Setup
Use a vector database to store and retrieve email embeddings:

```python
# Add to requirements.txt
# faiss-cpu
# langchain
# openai

from langchain.vectorstores import FAISS
from langchain.embeddings import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.docstore.document import Document
import faiss
import numpy as np

class GmailRAGIntegration:
    def __init__(self, openai_api_key: str):
        self.embeddings = OpenAIEmbeddings(openai_api_key=openai_api_key)
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
        )
        self.vectorstore = None
    
    def create_email_vectorstore(self, processed_emails: List[Dict[str, Any]]) -> FAISS:
        """Create vector store from processed emails"""
        documents = []
        
        for email in processed_emails:
            # Create document for each email
            doc = Document(
                page_content=email['content'],
                metadata={
                    'email_id': email['id'],
                    'thread_id': email['thread_id'],
                    'subject': email['subject'],
                    'from': email['from'],
                    'date': email['date'],
                    'timestamp': email['timestamp'],
                    'word_count': email['metadata']['word_count'],
                    'labels': email['metadata']['labels']
                }
            )
            documents.append(doc)
        
        # Split documents into chunks
        split_docs = self.text_splitter.split_documents(documents)
        
        # Create vector store
        self.vectorstore = FAISS.from_documents(split_docs, self.embeddings)
        
        return self.vectorstore
    
    def search_emails(self, query: str, k: int = 5) -> List[Document]:
        """Search for relevant emails based on query"""
        if not self.vectorstore:
            raise ValueError("Vector store not initialized. Call create_email_vectorstore first.")
        
        return self.vectorstore.similarity_search(query, k=k)
    
    def search_with_scores(self, query: str, k: int = 5) -> List[tuple]:
        """Search for relevant emails with similarity scores"""
        if not self.vectorstore:
            raise ValueError("Vector store not initialized. Call create_email_vectorstore first.")
        
        return self.vectorstore.similarity_search_with_score(query, k=k)
```

#### 6.2.2 RAG Pipeline Integration
Integrate Gmail data with your existing RAG pipeline:

```python
from langchain.chains import RetrievalQA
from langchain.llms import OpenAI
from langchain.prompts import PromptTemplate

class GmailRAGPipeline:
    def __init__(self, vectorstore: FAISS, openai_api_key: str):
        self.vectorstore = vectorstore
        self.llm = OpenAI(openai_api_key=openai_api_key, temperature=0.1)
        self.retriever = vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": 5}
        )
        self.qa_chain = self._create_qa_chain()
    
    def _create_qa_chain(self):
        """Create QA chain with custom prompt for email context"""
        prompt_template = """Use the following email context to answer the question. 
        If you don't know the answer based on the email context, say so.

        Email Context:
        {context}

        Question: {question}
        Answer:"""
        
        PROMPT = PromptTemplate(
            template=prompt_template,
            input_variables=["context", "question"]
        )
        
        return RetrievalQA.from_chain_type(
            llm=self.llm,
            chain_type="stuff",
            retriever=self.retriever,
            chain_type_kwargs={"prompt": PROMPT}
        )
    
    def query_emails(self, question: str) -> str:
        """Query the email database with a question"""
        return self.qa_chain.run(question)
    
    def get_relevant_emails(self, query: str, k: int = 5) -> List[Document]:
        """Get relevant emails for a query"""
        return self.vectorstore.similarity_search(query, k=k)
```

### 6.3 Complete Integration Example

Here's how to integrate everything together in your backend:

```python
# In your main.py or a new gmail_rag_service.py
from google.oauth2.credentials import Credentials
import json
import os

class GmailRAGService:
    def __init__(self, openai_api_key: str):
        self.openai_api_key = openai_api_key
        self.rag_pipeline = None
    
    def setup_gmail_rag(self, user_credentials: Credentials):
        """Set up Gmail RAG integration for a user"""
        try:
            # Extract emails
            extractor = GmailDataExtractor(user_credentials)
            emails = extractor.extract_emails(query='', max_results=100)
            
            # Preprocess emails
            preprocessor = EmailPreprocessor()
            processed_emails = preprocessor.preprocess_emails(emails)
            
            # Create RAG pipeline
            rag_integration = GmailRAGIntegration(self.openai_api_key)
            vectorstore = rag_integration.create_email_vectorstore(processed_emails)
            
            # Initialize RAG pipeline
            self.rag_pipeline = GmailRAGPipeline(vectorstore, self.openai_api_key)
            
            return {
                'status': 'success',
                'emails_processed': len(processed_emails),
                'vectorstore_created': True
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }
    
    def query_user_emails(self, question: str) -> dict:
        """Query user's emails using RAG"""
        if not self.rag_pipeline:
            return {
                'status': 'error',
                'message': 'Gmail RAG not initialized'
            }
        
        try:
            answer = self.rag_pipeline.query_emails(question)
            relevant_emails = self.rag_pipeline.get_relevant_emails(question, k=3)
            
            return {
                'status': 'success',
                'answer': answer,
                'relevant_emails': [
                    {
                        'subject': doc.metadata.get('subject', ''),
                        'from': doc.metadata.get('from', ''),
                        'date': doc.metadata.get('date', ''),
                        'snippet': doc.page_content[:200] + '...'
                    }
                    for doc in relevant_emails
                ]
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }

# Usage in your API endpoint
@app.post("/api/gmail/rag/setup")
async def setup_gmail_rag(request: Request):
    """Set up Gmail RAG for the current user"""
    # Get user credentials from your auth system
    user_credentials = get_user_credentials(request)
    
    rag_service = GmailRAGService(openai_api_key=os.getenv("OPENAI_API_KEY"))
    result = rag_service.setup_gmail_rag(user_credentials)
    
    return result

@app.post("/api/gmail/rag/query")
async def query_gmail_rag(request: Request):
    """Query user's emails using RAG"""
    data = await request.json()
    question = data.get("question", "")
    
    # Get user's RAG service instance
    rag_service = get_user_rag_service(request)
    result = rag_service.query_user_emails(question)
    
    return result
```

### 6.4 Advanced Features

#### 6.4.1 Incremental Email Updates
```python
def update_email_vectorstore(self, new_emails: List[Dict[str, Any]]):
    """Add new emails to existing vector store"""
    if not self.vectorstore:
        return self.create_email_vectorstore(new_emails)
    
    # Process new emails
    preprocessor = EmailPreprocessor()
    processed_emails = preprocessor.preprocess_emails(new_emails)
    
    # Create documents for new emails
    new_docs = []
    for email in processed_emails:
        doc = Document(
            page_content=email['content'],
            metadata={
                'email_id': email['id'],
                'subject': email['subject'],
                'from': email['from'],
                'date': email['date'],
                'timestamp': email['timestamp']
            }
        )
        new_docs.append(doc)
    
    # Add to existing vector store
    self.vectorstore.add_documents(new_docs)
    return self.vectorstore
```

#### 6.4.2 Email Filtering and Search
```python
def extract_emails_with_filters(self, 
                               query: str = '', 
                               label_filters: List[str] = None,
                               date_range: tuple = None,
                               max_results: int = 100) -> List[Dict[str, Any]]:
    """Extract emails with advanced filtering"""
    
    # Build Gmail query
    gmail_query = query
    if label_filters:
        label_query = ' OR '.join([f'label:{label}' for label in label_filters])
        gmail_query = f"{gmail_query} ({label_query})" if gmail_query else label_query
    
    if date_range:
        start_date, end_date = date_range
        date_query = f"after:{start_date} before:{end_date}"
        gmail_query = f"{gmail_query} {date_query}" if gmail_query else date_query
    
    return self.extract_emails(gmail_query, max_results)
```

### 6.5 Environment Variables for RAG

Add these to your `.env` file:

```env
# OpenAI Configuration for RAG
OPENAI_API_KEY=your_openai_api_key_here

# Optional: Vector Database Configuration
VECTOR_DB_PATH=./data/vectorstore
CHUNK_SIZE=1000
CHUNK_OVERLAP=200

# Optional: Email Processing Configuration
MAX_EMAILS_PER_SYNC=1000
EMAIL_SYNC_INTERVAL=3600  # seconds
```

### 6.6 Best Practices for Gmail RAG Integration

1. **Data Privacy**: Always respect user privacy and data protection regulations
2. **Incremental Updates**: Implement incremental email syncing to avoid reprocessing
3. **Error Handling**: Robust error handling for API rate limits and failures
4. **Caching**: Cache processed emails to avoid repeated processing
5. **Monitoring**: Monitor API usage and costs
6. **User Consent**: Ensure users understand what data is being processed

## Support

If you encounter issues:
1. Check the browser console for frontend errors
2. Check the backend logs for server errors
3. Verify all environment variables are set correctly
4. Ensure Google Cloud Console configuration is complete
5. For RAG-specific issues, check OpenAI API key and vector database setup
