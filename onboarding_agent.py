"""
Onboarding Agent Service

AI-powered conversational onboarding that collects user preferences
for personalized meal planning using Gemini 1.5 Flash via Vertex AI.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from google.cloud import firestore
from google.cloud.firestore_v1.base_document import DocumentSnapshot
from pydantic import BaseModel, Field
import vertexai
from vertexai.generative_models import (
    GenerativeModel,
    GenerationConfig,
    Content,
    Part,
    HarmCategory,
    HarmBlockThreshold,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models
# =============================================================================

class LocationData(BaseModel):
    """Location data from Google Maps for cold-start strategy."""
    city: str
    country: str


class HouseholdInfo(BaseModel):
    """Household composition."""
    adults: int = Field(default=1, ge=0)
    children: int = Field(default=0, ge=0)


class DailyMeals(BaseModel):
    """Meals to plan for a specific day."""
    breakfast: bool = Field(default=False, description="Cook at home")
    lunch: bool = Field(default=False, description="Cook at home")
    dinner: bool = Field(default=True, description="Cook at home")


class WeeklySchedule(BaseModel):
    """Weekly meal plan schedule."""
    monday: DailyMeals = Field(default_factory=DailyMeals)
    tuesday: DailyMeals = Field(default_factory=DailyMeals)
    wednesday: DailyMeals = Field(default_factory=DailyMeals)
    thursday: DailyMeals = Field(default_factory=DailyMeals)
    friday: DailyMeals = Field(default_factory=DailyMeals)
    saturday: DailyMeals = Field(default_factory=DailyMeals)
    sunday: DailyMeals = Field(default_factory=DailyMeals)


class UserProfile(BaseModel):
    """Complete user profile for meal planning."""
    user_id: str
    location: LocationData
    household: HouseholdInfo
    dietary_preferences: list[str] = Field(default_factory=list)
    allergies_dislikes: list[str] = Field(default_factory=list)
    meal_schedule: WeeklySchedule = Field(default_factory=WeeklySchedule)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_firestore_dict(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        return {
            "user_id": self.user_id,
            "location": {
                "city": self.location.city,
                "country": self.location.country,
            },
            "household": {
                "adults": self.household.adults,
                "children": self.household.children,
            },
            "dietary_preferences": self.dietary_preferences,
            "allergies_dislikes": self.allergies_dislikes,
            "meal_schedule": self.meal_schedule.model_dump(),
            "created_at": self.created_at,
        }


class ChatMessage(BaseModel):
    """A single message in the conversation."""
    role: str  # "user" or "assistant"
    content: str


class ConversationState(BaseModel):
    """Tracks the state of an onboarding conversation."""
    session_id: str
    location: LocationData
    messages: list[ChatMessage] = Field(default_factory=list)
    is_complete: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_firestore_dict(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        return {
            "session_id": self.session_id,
            "location": {
                "city": self.location.city,
                "country": self.location.country,
            },
            "messages": [msg.model_dump() for msg in self.messages],
            "is_complete": self.is_complete,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_firestore_dict(cls, data: dict) -> "ConversationState":
        """Create from Firestore document."""
        return cls(
            session_id=data["session_id"],
            location=LocationData(**data["location"]),
            messages=[ChatMessage(**msg) for msg in data.get("messages", [])],
            is_complete=data.get("is_complete", False),
            created_at=data.get("created_at", datetime.now(timezone.utc)),
            updated_at=data.get("updated_at", datetime.now(timezone.utc)),
        )


# =============================================================================
# Prompt Templates
# =============================================================================

SYSTEM_INSTRUCTION = """You are a friendly, concise culinary assistant helping new users set up their meal planning preferences.

Your goal is to naturally collect the following information through conversation:
1. Household size (how many adults and children)
2. Dietary constraints (allergies, vegetarian, vegan, keto, etc.)
3. Specific dislikes (ingredients they want to avoid)
4. Cooking Routine (which specific days and meals they cook at home, e.g., "dinners on weekdays", "only weekends", etc.)

Guidelines:
- Ask ONE question at a time, keeping it casual and conversational
- If the user mentions multiple facts at once (e.g., "I have a wife and two kids, we hate onions"), acknowledge all of them and move to the next topic
- Be encouraging and show genuine interest in their preferences
- Keep responses brief (1-2 sentences max for each question)
- Once you have gathered all 4 pieces of information, thank them and say you're all set

IMPORTANT: Do NOT make up or assume information the user hasn't provided. Only use what they explicitly tell you."""


def get_cold_start_prompt(location: LocationData) -> str:
    """Generate a location-aware welcome message prompt."""
    return f"""The user is located in {location.city}, {location.country}.

Generate a warm, personalized welcome message that:
1. Acknowledges their location in a friendly way
2. Briefly explains you'll ask a few quick questions to personalize their meal planning
3. Asks about their household size (first question)

Keep the message concise and conversational (2-3 sentences max)."""


EXTRACTION_PROMPT = """Analyze this conversation and extract the user's profile information.
Return a JSON object with the following structure. Only include information that was explicitly stated by the user.

Required JSON schema:
{
    "household": {
        "adults": <integer, default 1 if not specified>,
        "children": <integer, default 0 if not specified>
    },
    "dietary_preferences": [<list of dietary preferences like "vegetarian", "keto", "Mediterranean", etc.>],
    "allergies_dislikes": [<list of allergies and ingredient dislikes>],
    "meal_schedule": {
        "monday": {"breakfast": <bool>, "lunch": <bool>, "dinner": <bool>},
        "tuesday": {"breakfast": <bool>, "lunch": <bool>, "dinner": <bool>},
        "wednesday": {"breakfast": <bool>, "lunch": <bool>, "dinner": <bool>},
        "thursday": {"breakfast": <bool>, "lunch": <bool>, "dinner": <bool>},
        "friday": {"breakfast": <bool>, "lunch": <bool>, "dinner": <bool>},
        "saturday": {"breakfast": <bool>, "lunch": <bool>, "dinner": <bool>},
        "sunday": {"breakfast": <bool>, "lunch": <bool>, "dinner": <bool>}
    }
}
Infer the schedule based on the user's statements. E.g., "I work 9-5 so I eat out for lunch" -> Weekday Lunches = False. "I cook specific meals" -> Set those to True. Default to Dinner=True if unsure.

Conversation to analyze:
{conversation}

Extract and return ONLY the JSON object, no other text."""


# =============================================================================
# Location-based Defaults (Cold Start Strategy)
# =============================================================================

LOCATION_CUISINE_DEFAULTS: dict[str, list[str]] = {
    # Europe
    "Italy": ["Mediterranean", "Italian"],
    "Spain": ["Mediterranean", "Spanish"],
    "Greece": ["Mediterranean", "Greek"],
    "France": ["French", "Mediterranean"],
    "Germany": ["German", "European"],
    "United Kingdom": ["British", "European"],
    "Poland": ["Polish", "Eastern European"],
    "Ukraine": ["Ukrainian", "Eastern European"],
    
    # Asia
    "Japan": ["Japanese", "Asian"],
    "China": ["Chinese", "Asian"],
    "South Korea": ["Korean", "Asian"],
    "Thailand": ["Thai", "Asian"],
    "Vietnam": ["Vietnamese", "Asian"],
    "India": ["Indian", "South Asian"],
    
    # Americas
    "United States": ["American", "Diverse"],
    "Mexico": ["Mexican", "Latin American"],
    "Brazil": ["Brazilian", "Latin American"],
    "Argentina": ["Argentine", "Latin American"],
    "Canada": ["North American", "Diverse"],
    
    # Middle East & Africa
    "Turkey": ["Turkish", "Mediterranean"],
    "Israel": ["Israeli", "Mediterranean", "Middle Eastern"],
    "Morocco": ["Moroccan", "North African"],
    "Egypt": ["Egyptian", "Middle Eastern"],
}


def get_location_defaults(country: str) -> list[str]:
    """Get default cuisine preferences based on country."""
    return LOCATION_CUISINE_DEFAULTS.get(country, ["International"])


# =============================================================================
# Firestore Session Store
# =============================================================================

class FirestoreSessionStore:
    """
    Persistent session storage using Firestore.
    
    Provides scalable session management for Cloud Run deployment.
    Sessions are stored in a dedicated collection with TTL-based cleanup.
    """

    def __init__(
        self,
        db: firestore.Client,
        collection_name: str = "onboarding_sessions",
        session_ttl_hours: int = 1,
    ):
        """
        Initialize the session store.

        Args:
            db: Firestore client instance
            collection_name: Collection name for sessions
            session_ttl_hours: Session time-to-live in hours
        """
        self.db = db
        self.collection_name = collection_name
        self.session_ttl = timedelta(hours=session_ttl_hours)
        logger.info(f"Firestore session store initialized: {collection_name}")

    def save_session(self, session: ConversationState) -> None:
        """Save or update a session in Firestore."""
        try:
            session.updated_at = datetime.now(timezone.utc)
            doc_ref = self.db.collection(self.collection_name).document(session.session_id)
            doc_ref.set(session.to_firestore_dict())
            logger.debug(f"Saved session: {session.session_id}")
        except Exception as e:
            logger.error(f"Error saving session {session.session_id}: {e}")
            raise

    def get_session(self, session_id: str) -> Optional[ConversationState]:
        """Retrieve a session from Firestore."""
        try:
            doc_ref = self.db.collection(self.collection_name).document(session_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                return None
            
            data = doc.to_dict()
            session = ConversationState.from_firestore_dict(data)
            
            # Check if session has expired
            if datetime.now(timezone.utc) - session.updated_at > self.session_ttl:
                logger.info(f"Session expired: {session_id}")
                self.delete_session(session_id)
                return None
            
            return session
            
        except Exception as e:
            logger.error(f"Error retrieving session {session_id}: {e}")
            return None

    def delete_session(self, session_id: str) -> None:
        """Delete a session from Firestore."""
        try:
            doc_ref = self.db.collection(self.collection_name).document(session_id)
            doc_ref.delete()
            logger.debug(f"Deleted session: {session_id}")
        except Exception as e:
            logger.error(f"Error deleting session {session_id}: {e}")

    def cleanup_expired_sessions(self) -> int:
        """
        Clean up expired sessions. Returns count of deleted sessions.
        
        Note: For production, consider using Firestore TTL policies instead.
        """
        try:
            cutoff = datetime.now(timezone.utc) - self.session_ttl
            expired_query = (
                self.db.collection(self.collection_name)
                .where("updated_at", "<", cutoff)
                .limit(100)  # Batch limit
            )
            
            deleted_count = 0
            for doc in expired_query.stream():
                doc.reference.delete()
                deleted_count += 1
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} expired sessions")
            
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error cleaning up sessions: {e}")
            return 0


# =============================================================================
# Onboarding Conversation Handler
# =============================================================================

class OnboardingConversationHandler:
    """
    Handles the onboarding conversation flow using Gemini 1.5 Flash.
    
    This class manages:
    - Starting conversations with location-aware cold-start
    - Multi-turn conversation with natural question flow
    - Profile extraction from conversation history
    - Persistence to Firestore
    """

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        firestore_collection: str = "users",
    ):
        """
        Initialize the onboarding handler.

        Args:
            project_id: Google Cloud project ID
            location: Vertex AI region
            firestore_collection: Firestore collection name for user profiles
        """
        self.project_id = project_id
        self.location = location
        self.firestore_collection = firestore_collection
        
        # Initialize Vertex AI
        try:
            vertexai.init(project=project_id, location=location)
            self.model = GenerativeModel(
                "gemini-2.0-flash-001",
                system_instruction=SYSTEM_INSTRUCTION,
            )
            logger.info("Vertex AI initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI: {e}")
            raise

        # Initialize Firestore
        try:
            self.db = firestore.Client(project=project_id)
            self.session_store = FirestoreSessionStore(
                db=self.db,
                collection_name="onboarding_sessions",
                session_ttl_hours=1,
            )
            logger.info("Firestore client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Firestore: {e}")
            raise

        # Note: Sessions are now stored in Firestore via self.session_store

        # Safety settings for the model
        self._safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        }

    def start_conversation(self, location_data: dict) -> tuple[str, str]:
        """
        Start a new onboarding conversation.

        Args:
            location_data: Dictionary with 'city' and 'country' keys

        Returns:
            Tuple of (session_id, initial_message)
        """
        # Validate and parse location data
        location = LocationData(**location_data)
        
        # Create new session
        session_id = str(uuid.uuid4())
        session = ConversationState(
            session_id=session_id,
            location=location,
        )
        
        # Generate initial message using cold-start prompt
        try:
            cold_start_prompt = get_cold_start_prompt(location)
            
            response = self.model.generate_content(
                cold_start_prompt,
                generation_config=GenerationConfig(
                    temperature=0.7,
                    max_output_tokens=256,
                ),
                safety_settings=self._safety_settings,
            )
            
            initial_message = response.text.strip()
            
        except Exception as e:
            logger.error(f"Error generating initial message: {e}")
            # Fallback message if Gemini fails
            initial_message = (
                f"Hi! I see you're in {location.city}. "
                "I'd love to help personalize your meal planning experience. "
                "First, could you tell me about your household - how many people will you be cooking for?"
            )
        
        # Store the initial message in history
        session.messages.append(ChatMessage(role="assistant", content=initial_message))
        self.session_store.save_session(session)
        
        logger.info(f"Started new conversation session: {session_id}")
        return session_id, initial_message

    def send_message(self, session_id: str, user_message: str) -> tuple[str, bool]:
        """
        Send a user message and get the assistant's response.

        Args:
            session_id: The conversation session ID
            user_message: The user's message

        Returns:
            Tuple of (assistant_response, is_conversation_complete)
        """
        # Get session from Firestore
        session = self.session_store.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        
        if session.is_complete:
            return "We've already collected your preferences. Thank you!", True
        
        # Add user message to history
        session.messages.append(ChatMessage(role="user", content=user_message))
        
        # Build conversation history for the model
        contents = self._build_chat_history(session)
        
        try:
            # Generate response
            response = self.model.generate_content(
                contents,
                generation_config=GenerationConfig(
                    temperature=0.7,
                    max_output_tokens=256,
                ),
                safety_settings=self._safety_settings,
            )
            
            assistant_message = response.text.strip()
            
        except Exception as e:
            logger.error(f"Error generating response: {e}")
            raise RuntimeError(f"Failed to generate response: {e}")
        
        # Add assistant response to history
        session.messages.append(ChatMessage(role="assistant", content=assistant_message))
        
        # Check if conversation seems complete (simple heuristic)
        is_complete = self._check_conversation_complete(session)
        session.is_complete = is_complete
        
        # Save updated session to Firestore
        self.session_store.save_session(session)
        
        return assistant_message, is_complete

    def _build_chat_history(self, session: ConversationState) -> list[Content]:
        """Build Vertex AI Content objects from chat history."""
        contents = []
        
        for msg in session.messages:
            role = "user" if msg.role == "user" else "model"
            contents.append(
                Content(role=role, parts=[Part.from_text(msg.content)])
            )
        
        return contents

    def _check_conversation_complete(self, session: ConversationState) -> bool:
        """
        Check if the conversation has collected all necessary information.
        
        Uses simple heuristics - in production, could use Gemini for smarter detection.
        """
        # Count conversation turns (user messages)
        user_messages = [m for m in session.messages if m.role == "user"]
        
        # Check for completion phrases in the last assistant message
        if session.messages:
            last_message = session.messages[-1].content.lower()
            completion_phrases = [
                "all set",
                "you're all set",
                "that's everything",
                "have everything i need",
                "got everything",
                "perfect!",
                "thank you for sharing",
            ]
            if any(phrase in last_message for phrase in completion_phrases) and len(user_messages) >= 3:
                return True
        
        # Also complete after 4+ user turns as a fallback
        return len(user_messages) >= 4

    def is_conversation_complete(self, session_id: str) -> bool:
        """Check if a conversation session is complete."""
        session = self.session_store.get_session(session_id)
        return session.is_complete if session else False

    def get_chat_history(self, session_id: str) -> list[dict]:
        """Get the chat history for a session."""
        session = self.session_store.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        
        return [{"role": m.role, "content": m.content} for m in session.messages]

    async def finalize_profile(
        self,
        user_id: str,
        session_id: str,
    ) -> UserProfile:
        """
        Extract structured profile from conversation and save to Firestore.

        Args:
            user_id: The user's ID
            session_id: The conversation session ID

        Returns:
            The created UserProfile
        """
        session = self.session_store.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        
        # Format conversation for extraction
        conversation_text = self._format_conversation_for_extraction(session)
        
        # Extract structured data using Gemini with JSON mode
        extracted_data = await self._extract_profile_data(conversation_text)
        
        # Get location-based defaults for dietary preferences
        location_defaults = get_location_defaults(session.location.country)
        
        # Merge extracted preferences with location defaults
        dietary_prefs = extracted_data.get("dietary_preferences", [])
        if not dietary_prefs:
            dietary_prefs = location_defaults
        
        # Build the user profile
        profile = UserProfile(
            user_id=user_id,
            location=session.location,
            household=HouseholdInfo(
                adults=extracted_data.get("household", {}).get("adults", 1),
                children=extracted_data.get("household", {}).get("children", 0),
            ),
            dietary_preferences=dietary_prefs,
            allergies_dislikes=extracted_data.get("allergies_dislikes", []),
            meal_schedule=WeeklySchedule(**extracted_data.get("meal_schedule", {})),
        )
        
        # Save to Firestore
        await self._save_to_firestore(profile)
        
        # Clean up session from Firestore
        self.session_store.delete_session(session_id)
        
        logger.info(f"Created profile for user: {user_id}")
        return profile

    def _format_conversation_for_extraction(self, session: ConversationState) -> str:
        """Format conversation history for extraction prompt."""
        lines = []
        for msg in session.messages:
            prefix = "User" if msg.role == "user" else "Assistant"
            lines.append(f"{prefix}: {msg.content}")
        return "\n".join(lines)

    async def _extract_profile_data(self, conversation_text: str) -> dict:
        """
        Extract structured profile data from conversation using Gemini.
        
        Uses JSON mode for reliable structured output.
        """
        extraction_prompt = EXTRACTION_PROMPT.replace("{conversation}", conversation_text)
        
        try:
            response = self.model.generate_content(
                extraction_prompt,
                generation_config=GenerationConfig(
                    temperature=0.1,  # Low temperature for deterministic extraction
                    max_output_tokens=512,
                    response_mime_type="application/json",
                ),
                safety_settings=self._safety_settings,
            )
            
            import json
            extracted = json.loads(response.text)
            logger.info(f"Extracted profile data: {extracted}")
            return extracted
            
        except Exception as e:
            logger.error(f"Error extracting profile data: {e}")
            # Return defaults on error
            return {
                "household": {"adults": 1, "children": 0},
                "dietary_preferences": [],
                "allergies_dislikes": [],
                "meal_schedule": {},
            }

    async def _save_to_firestore(self, profile: UserProfile) -> None:
        """Save user profile to Firestore."""
        try:
            doc_ref = self.db.collection(self.firestore_collection).document(profile.user_id)
            doc_ref.set(profile.to_firestore_dict())
            logger.info(f"Saved profile to Firestore: {profile.user_id}")
        except Exception as e:
            logger.error(f"Error saving to Firestore: {e}")
            raise RuntimeError(f"Failed to save profile to Firestore: {e}")

    async def get_user_profile(self, user_id: str) -> Optional[UserProfile]:
        """
        Retrieve a user profile from Firestore.

        Args:
            user_id: The user's ID

        Returns:
            UserProfile if found, None otherwise
        """
        try:
            doc_ref = self.db.collection(self.firestore_collection).document(user_id)
            doc: DocumentSnapshot = doc_ref.get()
            
            if not doc.exists:
                return None
            
            data = doc.to_dict()
            return UserProfile(
                user_id=data["user_id"],
                location=LocationData(**data["location"]),
                household=HouseholdInfo(**data["household"]),
                dietary_preferences=data.get("dietary_preferences", []),
                allergies_dislikes=data.get("allergies_dislikes", []),
                meal_schedule=WeeklySchedule(**data.get("meal_schedule", {})),
                created_at=data.get("created_at", datetime.now(timezone.utc)),
            )
        except Exception as e:
            logger.error(f"Error retrieving profile: {e}")
            return None
