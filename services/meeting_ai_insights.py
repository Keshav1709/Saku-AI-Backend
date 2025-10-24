"""
AI-Powered Meeting Insights Service
Generates comprehensive summaries, action items, and insights from meeting transcripts
"""

import os
from typing import Dict, Any, List, Optional
import google.generativeai as genai
import json


class MeetingAIInsights:
    """Generate AI insights from meeting transcripts and notes"""
    
    def __init__(self):
        self.configure_genai()
        
    def configure_genai(self):
        """Configure Gemini API"""
        api_key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY")
        )
        if api_key:
            genai.configure(api_key=api_key)
    
    def get_model(self) -> genai.GenerativeModel:
        """Get the best available Gemini model"""
        preferred = os.getenv("GENAI_MODEL") or os.getenv("GOOGLE_GENAI_MODEL")
        candidates = [
            c for c in [
                preferred,
                "gemini-2.0-flash-exp",
                "gemini-1.5-flash-latest",
                "gemini-1.5-pro-latest",
                "gemini-1.5-flash",
                "gemini-1.5-pro"
            ] if c
        ]
        
        last_error = None
        for model_name in candidates:
            try:
                model = genai.GenerativeModel(
                    model_name,
                    generation_config={
                        "response_mime_type": "application/json",
                        "temperature": 0.2,  # Lower for more consistent output
                        "top_p": 0.8,
                    }
                )
                print(f"INFO: Using Gemini model: {model_name}")
                return model
            except Exception as e:
                last_error = e
                continue
        
        raise Exception(f"Failed to initialize Gemini model: {last_error}")
    
    async def generate_comprehensive_insights(
        self,
        transcript: str,
        notes: str = "",
        meeting_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate comprehensive AI insights from meeting content
        
        Args:
            transcript: Full meeting transcript
            notes: Additional meeting notes
            meeting_metadata: Meeting title, participants, duration, etc.
        
        Returns:
            Dictionary containing summary, action items, topics, sentiment, etc.
        """
        metadata = meeting_metadata or {}
        title = metadata.get("title", "Meeting")
        participants = metadata.get("participants", [])
        duration = metadata.get("duration", "Unknown")
        
        # Build context
        content = f"""Meeting Title: {title}
Participants: {', '.join(participants) if participants else 'Unknown'}
Duration: {duration}

TRANSCRIPT:
{transcript[:15000]}  # Limit to ~15k chars to stay within context limits

ADDITIONAL NOTES:
{notes[:2000] if notes else 'None'}
"""

        # Generate insights with structured output
        try:
            model = self.get_model()
            
            prompt = f"""You are an expert meeting analyst. Analyze the following meeting and generate comprehensive insights.

{content}

Generate a JSON response with the following structure:
{{
    "executiveSummary": "2-3 sentence overview of the meeting",
    "keyDecisions": ["decision 1", "decision 2", ...],
    "actionItems": [
        {{"task": "description", "assignee": "person or team", "priority": "high|medium|low", "dueDate": "YYYY-MM-DD or 'Not specified'"}},
        ...
    ],
    "mainTopics": ["topic 1", "topic 2", ...],
    "keyQuestions": ["important question 1", "question 2", ...],
    "chapters": [
        {{"title": "Chapter title", "timestamp": "MM:SS", "summary": "Brief summary"}},
        ...
    ],
    "sentiment": {{
        "overall": "positive|neutral|negative",
        "reasoning": "Brief explanation"
    }},
    "nextSteps": ["recommended next step 1", "step 2", ...],
    "participantInsights": [
        {{"name": "Participant name", "contribution": "What they discussed", "sentiment": "positive|neutral|negative"}},
        ...
    ]
}}

Rules:
1. Extract ONLY factual information from the transcript
2. For action items, be specific and actionable
3. Identify explicit decisions made during the meeting
4. Keep summaries concise but informative
5. Output ONLY valid JSON, no additional text
"""

            response = model.generate_content(prompt)
            
            # Extract JSON from response
            raw_text = self._extract_text(response)
            insights = self._parse_json_response(raw_text)
            
            return insights
            
        except Exception as e:
            print(f"ERROR: AI insights generation failed: {e}")
            return self._fallback_insights(transcript, notes, metadata)
    
    async def generate_action_items(self, transcript: str, existing_actions: List[Dict] = None) -> List[Dict[str, Any]]:
        """Generate action items from transcript"""
        try:
            model = self.get_model()
            
            existing = existing_actions or []
            existing_text = json.dumps(existing) if existing else "None"
            
            prompt = f"""Extract action items from this meeting transcript.

TRANSCRIPT:
{transcript[:10000]}

EXISTING ACTION ITEMS:
{existing_text}

Generate a JSON array of action items:
[
    {{
        "task": "Clear, actionable task description",
        "assignee": "Person or team responsible",
        "priority": "high|medium|low",
        "dueDate": "YYYY-MM-DD or 'Not specified'",
        "context": "Brief context from discussion"
    }},
    ...
]

Rules:
1. Extract ONLY explicit action items mentioned
2. Include task owner if mentioned
3. Infer priority from urgency cues
4. Output ONLY valid JSON array
"""

            response = model.generate_content(prompt)
            raw_text = self._extract_text(response)
            action_items = json.loads(raw_text)
            
            return action_items if isinstance(action_items, list) else []
            
        except Exception as e:
            print(f"ERROR: Action items extraction failed: {e}")
            return []
    
    async def generate_summary(self, transcript: str, meeting_title: str = "") -> str:
        """Generate executive summary"""
        try:
            model = self.get_model()
            
            prompt = f"""Generate a concise 2-3 sentence executive summary of this meeting.

Meeting: {meeting_title}

TRANSCRIPT:
{transcript[:8000]}

Output ONLY the summary text, no JSON or other formatting.
Make it informative and highlight key decisions/outcomes.
"""

            response = model.generate_content(prompt)
            summary = self._extract_text(response).strip()
            
            # Remove JSON artifacts if any
            summary = summary.replace('```json', '').replace('```', '').replace('"', '').strip()
            
            return summary or "Meeting summary unavailable"
            
        except Exception as e:
            print(f"ERROR: Summary generation failed: {e}")
            return f"Meeting: {meeting_title}. Transcript length: {len(transcript)} characters."
    
    async def extract_key_topics(self, transcript: str, max_topics: int = 5) -> List[str]:
        """Extract main discussion topics"""
        try:
            model = self.get_model()
            
            prompt = f"""Identify the {max_topics} main topics discussed in this meeting.

TRANSCRIPT:
{transcript[:10000]}

Output a JSON array of topic strings:
["Topic 1", "Topic 2", ...]

Be concise and specific.
"""

            response = model.generate_content(prompt)
            raw_text = self._extract_text(response)
            topics = json.loads(raw_text)
            
            return topics if isinstance(topics, list) else []
            
        except Exception as e:
            print(f"ERROR: Topic extraction failed: {e}")
            return []
    
    async def analyze_sentiment(self, transcript: str) -> Dict[str, Any]:
        """Analyze overall meeting sentiment"""
        try:
            model = self.get_model()
            
            prompt = f"""Analyze the sentiment and tone of this meeting.

TRANSCRIPT:
{transcript[:8000]}

Output JSON:
{{
    "overall": "positive|neutral|negative|mixed",
    "confidence": 0.0-1.0,
    "reasoning": "Brief explanation",
    "tensionPoints": ["point 1", ...],  # If any
    "positiveAspects": ["aspect 1", ...]
}}
"""

            response = model.generate_content(prompt)
            raw_text = self._extract_text(response)
            sentiment = json.loads(raw_text)
            
            return sentiment
            
        except Exception as e:
            print(f"ERROR: Sentiment analysis failed: {e}")
            return {
                "overall": "neutral",
                "confidence": 0.5,
                "reasoning": "Analysis unavailable",
                "tensionPoints": [],
                "positiveAspects": []
            }
    
    async def generate_chapters(self, transcript: str, duration_seconds: int = 3600) -> List[Dict[str, Any]]:
        """Generate meeting chapters/segments"""
        try:
            model = self.get_model()
            
            prompt = f"""Divide this {duration_seconds} second meeting into logical chapters/segments.

TRANSCRIPT:
{transcript[:12000]}

Output JSON array:
[
    {{
        "title": "Chapter title",
        "timestamp": "MM:SS",
        "timestampSeconds": 0,
        "summary": "Brief 1-sentence summary of this section"
    }},
    ...
]

Aim for 4-6 chapters based on topic shifts.
"""

            response = model.generate_content(prompt)
            raw_text = self._extract_text(response)
            chapters = json.loads(raw_text)
            
            return chapters if isinstance(chapters, list) else []
            
        except Exception as e:
            print(f"ERROR: Chapter generation failed: {e}")
            return self._fallback_chapters(duration_seconds)
    
    async def extract_participant_insights(
        self, 
        transcript: str, 
        participants: List[str]
    ) -> List[Dict[str, Any]]:
        """Analyze individual participant contributions"""
        try:
            model = self.get_model()
            
            participants_text = ", ".join(participants) if participants else "Unknown"
            
            prompt = f"""Analyze the participation and contributions of each person in this meeting.

Participants: {participants_text}

TRANSCRIPT:
{transcript[:10000]}

Output JSON array:
[
    {{
        "name": "Participant name",
        "talkTimePercent": 0-100,
        "keyContributions": ["contribution 1", "contribution 2"],
        "sentiment": "positive|neutral|negative",
        "role": "leader|contributor|listener"
    }},
    ...
]
"""

            response = model.generate_content(prompt)
            raw_text = self._extract_text(response)
            insights = json.loads(raw_text)
            
            return insights if isinstance(insights, list) else []
            
        except Exception as e:
            print(f"ERROR: Participant insights failed: {e}")
            return []
    
    # Helper methods
    
    def _extract_text(self, response) -> str:
        """Extract text from Gemini response"""
        raw_text = (getattr(response, "text", None) or "").strip()
        if not raw_text and getattr(response, "candidates", None):
            try:
                raw_text = response.candidates[0].content.parts[0].text
            except Exception:
                raw_text = ""
        return raw_text
    
    def _parse_json_response(self, raw_text: str) -> Dict[str, Any]:
        """Parse JSON from potentially messy response"""
        import re
        
        # Try direct parsing first
        try:
            return json.loads(raw_text)
        except:
            pass
        
        # Try extracting JSON from code blocks
        try:
            json_match = re.search(r'```json\n([\s\S]*?)\n```', raw_text)
            if json_match:
                return json.loads(json_match.group(1))
        except:
            pass
        
        # Try finding JSON object
        try:
            json_match = re.search(r'\{[\s\S]*\}', raw_text)
            if json_match:
                return json.loads(json_match.group(0))
        except:
            pass
        
        # Fallback
        return {}
    
    def _fallback_insights(self, transcript: str, notes: str, metadata: Dict) -> Dict[str, Any]:
        """Fallback insights when AI generation fails"""
        return {
            "executiveSummary": f"Meeting: {metadata.get('title', 'Untitled')}. Transcript length: {len(transcript)} characters.",
            "keyDecisions": [],
            "actionItems": [],
            "mainTopics": ["Discussion", "Planning", "Review"],
            "keyQuestions": [],
            "chapters": self._fallback_chapters(metadata.get('duration', 3600)),
            "sentiment": {
                "overall": "neutral",
                "reasoning": "Automated fallback - AI analysis unavailable"
            },
            "nextSteps": [],
            "participantInsights": []
        }
    
    def _fallback_chapters(self, duration_seconds: int) -> List[Dict[str, Any]]:
        """Generate basic chapters based on time"""
        num_chapters = min(max(3, duration_seconds // 900), 6)  # 3-6 chapters
        chapters = []
        interval = duration_seconds // num_chapters
        
        chapter_names = ["Introduction", "Discussion", "Planning", "Review", "Decisions", "Wrap-up"]
        
        for i in range(num_chapters):
            timestamp_sec = i * interval
            minutes = timestamp_sec // 60
            seconds = timestamp_sec % 60
            
            chapters.append({
                "title": chapter_names[i] if i < len(chapter_names) else f"Segment {i+1}",
                "timestamp": f"{minutes:02d}:{seconds:02d}",
                "timestampSeconds": timestamp_sec,
                "summary": ""
            })
        
        return chapters


# Global instance
_insights_service = None

def get_insights_service() -> MeetingAIInsights:
    """Get or create the global insights service instance"""
    global _insights_service
    if _insights_service is None:
        _insights_service = MeetingAIInsights()
    return _insights_service

