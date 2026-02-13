"""
FastAPI Application for Onboarding Agent

Provides REST API endpoints for the conversational onboarding flow.
Designed for deployment on Cloud Run.
"""

import os
import json
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from auth import (
    AuthService,
    Token,
    SignupRequest,
    LoginRequest,
    GoogleAuthRequest,
    UserResponse,
    get_current_user,
    create_access_token,
)

from onboarding_agent import (
    OnboardingConversationHandler,
    UserProfile,
    LocationData,
)
try:
    from menu_generator import MenuGenerator, GeneratedMenuDocument, WeeklyMenu, DailyMenu, MenuSlot
except ImportError:
    MenuGenerator = None
    print("Warning: Failed to import MenuGenerator (missing dependencies?)")

# Configure logging
logger = logging.getLogger(__name__)

# =============================================================================
# Mock Generator (Fallback)
# =============================================================================
class MockMenuGenerator:
    """Fallback generator for local testing/verification without GCP credentials."""
    
    def __init__(self, project_id, location):
        self.project_id = project_id
    
    def get_latest_menu(self, user_id: str):
        print(f"Using MockMenuGenerator for user {user_id}")
        # Return a dummy menu
        return GeneratedMenuDocument(
            user_id=user_id,
            week_start_date="2024-01-01",
            created_at=datetime.utcnow(),
            menu=WeeklyMenu(
                monday=DailyMenu(
                    dinner=MenuSlot(
                        name="Grilled Salmon with Asparagus",
                        description="Fresh Atlantic salmon fillet grilled to perfection with lemon butter sauce.",
                        ingredients=["Salmon fillet", "Asparagus", "Lemon", "Butter", "Garlic"],
                        preparation_steps=["Season salmon", "Grill for 5 mins each side", "Sauté asparagus", "Serve with lemon butter"]
                    )
                ),
                tuesday=DailyMenu(
                    dinner=MenuSlot(
                        name="Chicken Stir-Fry",
                        description="Quick and healthy chicken vegetable stir-fry with soy glaze.",
                        ingredients=["Chicken breast", "Broccoli", "Carrots", "Soy sauce", "Ginger"],
                        preparation_steps=["Slice chicken", "Stir-fry veggies", "Cook chicken", "Combine with sauce"]
                    )
                ),
                wednesday=DailyMenu(),
                thursday=DailyMenu(),
                friday=DailyMenu(),
                saturday=DailyMenu(),
                sunday=DailyMenu()
            )
        )
    
    def generate_menu_for_user(self, user_id: str) -> bool:
        """Mock menu generation - returns True immediately for testing."""
        print(f"MockMenuGenerator: Generating menu for user {user_id}")
        # In mock mode, get_latest_menu always returns a dummy menu,
        # so we don't need to actually persist anything
        print(f"MockMenuGenerator: Mock menu ready for user {user_id}")
        return True


# =============================================================================
# Request/Response Models
# =============================================================================

class StartConversationRequest(BaseModel):
    """Request to start a new onboarding conversation."""
    city: str = Field(..., description="User's city from Google Maps")
    country: str = Field(..., description="User's country from Google Maps")


class StartConversationResponse(BaseModel):
    """Response with session ID and initial message."""
    session_id: str
    message: str


class SendMessageRequest(BaseModel):
    """Request to send a message in an active conversation."""
    session_id: str
    message: str


class SendMessageResponse(BaseModel):
    """Response with assistant message and completion status."""
    message: str
    is_complete: bool


class FinalizeProfileRequest(BaseModel):
    """Request to finalize and save the user profile."""
    session_id: str
    user_id: str


class FinalizeProfileResponse(BaseModel):
    """Response with the created user profile."""
    profile: dict
    success: bool
    access_token: Optional[str] = None  # New token with is_onboarded=true


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str = "1.0.0"


class DishRatingRequest(BaseModel):
    """Request to rate a dish with optional feedback."""
    user_id: str
    dish_name: str
    rating: str = Field(..., description="Rating type: 'thumbs_up' or 'thumbs_down'")
    feedback_text: Optional[str] = Field(None, description="Optional user feedback about the dish")


class DishRatingResponse(BaseModel):
    """Response after submitting a dish rating."""
    success: bool
    rating: str
    dish_name: str
    extracted_preferences: Optional[dict] = None
    message: str = "Rating saved successfully"


class GetRatingsResponse(BaseModel):
    """Response containing user's dish ratings."""
    ratings: list[dict]
    total_count: int


# =============================================================================
# Application Setup
# =============================================================================

# Global handler instances
handler: Optional[OnboardingConversationHandler] = None
menu_generator: Optional[MenuGenerator] = None
auth_service: Optional[AuthService] = None


# =============================================================================
# Feedback Analysis Service
# =============================================================================

FEEDBACK_ANALYSIS_PROMPT = """Analyze this dish rating feedback and extract structured preferences.

Dish Name: {dish_name}
Rating: {rating}
User Feedback: {feedback_text}

Extract specific preferences from the feedback and categorize them into:
1. flavor_preferences: taste-related preferences (e.g., "prefers less spicy", "loves garlic", "not sweet enough")
2. texture_preferences: texture-related feedback (e.g., "likes crispy", "too mushy", "perfect tenderness")
3. ingredient_preferences: ingredient-specific feedback (e.g., "wants more protein", "too much onion", "loves fresh herbs")
4. cooking_methods: cooking technique preferences (e.g., "prefers grilled", "not a fan of frying", "enjoys roasted")
5. nutritional_preferences: dietary/health preferences (e.g., "wants lighter meals", "needs more vegetables", "too heavy")

Return ONLY a JSON object with these categories as keys and lists of extracted preferences as values.
Only include categories that have relevant feedback. Use clear, actionable preference statements.

Example output format:
{
  "flavor_preferences": ["prefers milder spices", "loves garlic flavor"],
  "texture_preferences": ["enjoys crispy textures"],
  "ingredient_preferences": ["wants more vegetables"]
}

If the feedback is too short or vague (e.g., just "good" or "bad"), return an empty object: {}
"""


async def analyze_dish_feedback(
    dish_name: str,
    rating: str,
    feedback_text: Optional[str],
    model: Optional["GenerativeModel"] = None
) -> dict:
    """
    Analyze user feedback using Gemini to extract structured preferences.
    
    Args:
        dish_name: Name of the dish
        rating: Rating type ('thumbs_up' or 'thumbs_down')
        feedback_text: User's feedback text
        model: Gemini model instance (optional, will use global if not provided)
        
    Returns:
        Dictionary of extracted preferences by category
    """
    if not feedback_text or len(feedback_text.strip()) < 5:
        return {}
    
    try:
        # Use handler's model if available
        if handler and hasattr(handler, 'model'):
            gemini_model = handler.model
        elif model:
            gemini_model = model
        else:
            # No model available (mock mode or error)
            logger.warning("No Gemini model available for feedback analysis")
            return {}
        
        prompt = FEEDBACK_ANALYSIS_PROMPT.format(
            dish_name=dish_name,
            rating=rating,
            feedback_text=feedback_text
        )
        
        from vertexai.generative_models import GenerationConfig
        response = gemini_model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=0.1,  # Low temperature for consistent extraction
                max_output_tokens=512,
                response_mime_type="application/json",
            ),
        )
        
        import json
        extracted = json.loads(response.text)
        logger.info(f"Extracted preferences from feedback: {extracted}")
        return extracted
        
    except Exception as e:
        logger.error(f"Error analyzing feedback: {e}")
        return {}


async def merge_preferences_to_profile(user_id: str, extracted_preferences: dict) -> bool:
    """
    Merge extracted preferences into user's profile.
    
    Args:
        user_id: User's ID
        extracted_preferences: Dictionary of preferences extracted from feedback
        
    Returns:
        True if successful, False otherwise
    """
    if not handler or not extracted_preferences:
        return False
    
    try:
        # Get current profile
        profile = await handler.get_user_profile(user_id)
        if not profile:
            logger.warning(f"Profile not found for user {user_id}")
            return False
        
        # Merge preferences - append new ones to existing lists
        current_prefs = profile.learned_preferences
        
        for category, new_prefs in extracted_preferences.items():
            if category not in current_prefs:
                current_prefs[category] = []
            
            # Add new preferences, avoiding duplicates
            for pref in new_prefs:
                if pref not in current_prefs[category]:
                    current_prefs[category].append(pref)
        
        # Update profile in Firestore
        doc_ref = handler.db.collection(handler.firestore_collection).document(user_id)
        doc_ref.update({"learned_preferences": current_prefs})
        
        logger.info(f"Updated preferences for user {user_id}: {current_prefs}")
        return True
        
    except Exception as e:
        logger.error(f"Error merging preferences for user {user_id}: {e}")
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup."""
    global handler, menu_generator, auth_service
    
    # Check for MOCK_MODE
    mock_mode = os.environ.get("MOCK_MODE", "false").lower() == "true"
    
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id and not mock_mode:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT environment variable is required")
    elif not project_id:
        project_id = "mock-project" # Fallback for local testing
    
    location = os.environ.get("VERTEX_AI_LOCATION", "us-central1")
    
    # helper to log and mock
    def use_mock_handler():
        global handler
        from onboarding_agent import MockOnboardingConversationHandler
        print("Using MockOnboardingConversationHandler")
        handler = MockOnboardingConversationHandler(project_id, location)

    def use_mock_auth():
        global auth_service
        from auth import MockAuthService
        print("Using MockAuthService")
        auth_service = MockAuthService()

    # Initialize Handler
    if mock_mode:
        use_mock_handler()
    else:
        try:
            handler = OnboardingConversationHandler(
                project_id=project_id,
                location=location,
            )
        except Exception as e:
            print(f"Failed to initialize OnboardingConversationHandler: {e}. ")
            if mock_mode: 
                 use_mock_handler()
            else:
                 print("Onboarding endpoints will be unavailable.")
                 handler = None

    # Initialize MenuGenerator
    if mock_mode:
        print("Using MockMenuGenerator")
        menu_generator = MockMenuGenerator(project_id, location)
    else:
        try:
            menu_generator = MenuGenerator(
                project_id=project_id,
                location=location,
            )
        except Exception as e:
            print(f"Failed to initialize real MenuGenerator: {e}. Using Mock.")
            menu_generator = MockMenuGenerator(project_id, location)
    
    # Initialize Auth Service
    if mock_mode:
        use_mock_auth()
    else:
        try:
            from google.cloud import firestore
            db = firestore.Client(project=project_id)
            auth_service = AuthService(db)
            print("AuthService initialized successfully")
        except Exception as e:
            print(f"Failed to initialize AuthService: {e}")
            if mock_mode:
                 use_mock_auth()
            else:
                 auth_service = None
    
    yield
    
    # Cleanup if needed
    handler = None
    menu_generator = None
    auth_service = None


app = FastAPI(
    title="Onboarding Agent API",
    description="AI-powered conversational onboarding for personalized meal planning",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for simplicity (or configure for Cloud Run domain)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the frontend static files
# Note: This checks for the 'frontend' directory. In Docker, valid. Locally, valid.
# API routes defined below will take precedence if order matters, but for mount it's usually fine.
# We mount it at the end to avoid conflicts, but here is fine too as specific routes match first.
# Actually, let's mount it at the end of the file or ensure it doesn't swallow API calls.
# FastAPI router matches specific paths first usually.



# =============================================================================
# API Endpoints
# =============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for Cloud Run."""
    return HealthResponse(status="healthy")


# =============================================================================
# Auth Endpoints
# =============================================================================

@app.post("/auth/signup", response_model=Token, status_code=status.HTTP_201_CREATED)
async def signup(request: SignupRequest):
    """Create a new user with email/password."""
    if not auth_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service not initialized"
        )
    
    return auth_service.signup_with_email(request.email, request.password)


@app.post("/auth/login", response_model=Token)
async def login(request: LoginRequest):
    """Authenticate with email/password."""
    if not auth_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service not initialized"
        )
    
    return auth_service.login_with_email(request.email, request.password)


@app.post("/auth/google", response_model=Token)
async def google_auth(request: GoogleAuthRequest):
    """Authenticate with Google ID token."""
    if not auth_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service not initialized"
        )
    
    return auth_service.auth_with_google(request.credential)


@app.get("/auth/me", response_model=UserResponse)
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user info."""
    return UserResponse(
        user_id=current_user["user_id"],
        email=current_user["email"],
        is_onboarded=current_user["is_onboarded"],
    )


@app.post("/test/mark-onboarded")
async def mark_user_onboarded(current_user: dict = Depends(get_current_user)):
    """
    TEST ONLY: Mark the current user as onboarded.
    This endpoint is for testing purposes only.
    """
    if not auth_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service not initialized"
        )
    
    auth_service.set_onboarded(current_user["user_id"])
    
    # Return a new token with updated onboarded status
    new_token = create_access_token(
        user_id=current_user["user_id"],
        email=current_user["email"],
        is_onboarded=True
    )
    
    return {
        "success": True,
        "access_token": new_token,
        "user_id": current_user["user_id"],
        "is_onboarded": True
    }


@app.post(
    "/onboarding/start",
    response_model=StartConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_conversation(request: StartConversationRequest):
    """
    Start a new onboarding conversation.
    
    Uses the user's location for cold-start personalization.
    Returns a session ID and the initial welcome message.
    """
    if not handler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized"
        )
    
    try:
        session_id, message = handler.start_conversation({
            "city": request.city,
            "country": request.country,
        })
        
        return StartConversationResponse(
            session_id=session_id,
            message=message,
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start conversation: {str(e)}"
        )


@app.post("/onboarding/message", response_model=SendMessageResponse)
async def send_message(request: SendMessageRequest):
    """
    Send a message in an active conversation.
    
    Returns the assistant's response and whether the conversation is complete.
    """
    if not handler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized"
        )
    
    try:
        message, is_complete = handler.send_message(
            session_id=request.session_id,
            user_message=request.message,
        )
        
        return SendMessageResponse(
            message=message,
            is_complete=is_complete,
        )
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@app.post("/onboarding/finalize", response_model=FinalizeProfileResponse)
async def finalize_profile(request: FinalizeProfileRequest):
    """
    Finalize the conversation and save the user profile.
    
    Extracts structured data from the conversation using Gemini
    and saves the profile to Firestore.
    Also marks the user as onboarded and triggers first menu generation.
    """
    if not handler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized"
        )
    
    try:
        profile = await handler.finalize_profile(
            user_id=request.user_id,
            session_id=request.session_id,
        )
        
        # Mark user as onboarded in auth system and get new token
        new_token = None
        if auth_service:
            try:
                auth_service.set_onboarded(request.user_id)
                print(f"User {request.user_id} marked as onboarded")
                
                # Get user email to create new token
                user = auth_service.get_user_by_id(request.user_id)
                if user:
                    new_token = create_access_token(
                        user_id=request.user_id,
                        email=user.email,
                        is_onboarded=True
                    )
                    print(f"Generated new token for user {request.user_id} with is_onboarded=True")
            except Exception as e:
                print(f"Failed to mark user as onboarded: {e}")
        
        # Trigger first menu generation for this user
        if menu_generator and hasattr(menu_generator, 'generate_menu_for_user'):
            try:
                print(f"Triggering first menu generation for user {request.user_id}")
                menu_generator.generate_menu_for_user(request.user_id)
                print(f"First menu generated for user {request.user_id}")
            except Exception as e:
                print(f"Failed to generate first menu: {e}")
                # Don't fail the request - menu can be generated later
        
        return FinalizeProfileResponse(
            profile=profile.to_firestore_dict(),
            success=True,
            access_token=new_token,
        )
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@app.get("/onboarding/history/{session_id}")
async def get_conversation_history(session_id: str):
    """
    Get the conversation history for a session.
    
    Useful for debugging or displaying the conversation in the UI.
    """
    if not handler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized"
        )
    
    try:
        history = handler.get_chat_history(session_id)
        return {"session_id": session_id, "messages": history}
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


@app.get("/menus/{user_id}/current")
async def get_latest_menu(user_id: str):
    """
    Retrieve the latest generated weekly menu for a user.
    """
    if not menu_generator:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized"
        )
    
    menu = menu_generator.get_latest_menu(user_id)
    
    if not menu:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No menu found for this user"
        )
    
    return menu.to_firestore_dict()


# =============================================================================
# Rating Endpoints
# =============================================================================

@app.post("/ratings/", response_model=DishRatingResponse)
async def submit_dish_rating(request: DishRatingRequest):
    """
    Submit or update a dish rating with optional feedback.
    
    If feedback is provided, it will be analyzed by Gemini to extract
    preferences which are merged into the user's profile.
    """
    if not handler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized"
        )
    
    try:
        # Normalize dish name for consistent storage
        dish_name_normalized = request.dish_name.lower().replace(" ", "_")
        doc_id = f"{request.user_id}_{dish_name_normalized}"
        
        # Get Firestore client
        db = handler.db
        
        # Analyze feedback if provided
        extracted_preferences = {}
        if request.feedback_text and request.feedback_text.strip():
            extracted_preferences = await analyze_dish_feedback(
                dish_name=request.dish_name,
                rating=request.rating,
                feedback_text=request.feedback_text
            )
            
            # Merge preferences to user profile
            if extracted_preferences:
                await merge_preferences_to_profile(request.user_id, extracted_preferences)
        
        # Save rating to Firestore
        rating_data = {
            "user_id": request.user_id,
            "dish_name": request.dish_name,
            "rating": request.rating,
            "feedback_text": request.feedback_text or "",
            "extracted_preferences": extracted_preferences,
            "updated_at": datetime.utcnow(),
        }
        
        # Check if rating exists to set created_at
        doc_ref = db.collection("dish_ratings").document(doc_id)
        existing_doc = doc_ref.get()
        
        if not existing_doc.exists:
            rating_data["created_at"] = datetime.utcnow()
        
        doc_ref.set(rating_data, merge=True)
        
        logger.info(f"Saved rating for user {request.user_id}: {request.dish_name} = {request.rating}")
        
        return DishRatingResponse(
            success=True,
            rating=request.rating,
            dish_name=request.dish_name,
            extracted_preferences=extracted_preferences if extracted_preferences else None,
            message="Rating saved successfully" + (
                f" and preferences learned!" if extracted_preferences else ""
            )
        )
        
    except Exception as e:
        logger.error(f"Error saving rating: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save rating: {str(e)}"
        )


@app.get("/ratings/{user_id}", response_model=GetRatingsResponse)
async def get_user_ratings(user_id: str):
    """
    Get all dish ratings for a user.
    """
    if not handler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized"
        )
    
    try:
        db = handler.db
        
        # Query all ratings for this user
        ratings_query = db.collection("dish_ratings").where("user_id", "==", user_id).stream()
        
        ratings = []
        for doc in ratings_query:
            rating_data = doc.to_dict()
            ratings.append(rating_data)
        
        return GetRatingsResponse(
            ratings=ratings,
            total_count=len(ratings)
        )
        
    except Exception as e:
        logger.error(f"Error retrieving ratings for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve ratings: {str(e)}"
        )


@app.get("/ratings/{user_id}/{dish_name}")
async def get_specific_dish_rating(user_id: str, dish_name: str):
    """
    Get rating for a specific dish.
    """
    if not handler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized"
        )
    
    try:
        db = handler.db
        
        # Normalize dish name
        dish_name_normalized = dish_name.lower().replace(" ", "_")
        doc_id = f"{user_id}_{dish_name_normalized}"
        
        doc_ref = db.collection("dish_ratings").document(doc_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Rating not found for this dish"
            )
        
        return doc.to_dict()
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving rating: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve rating: {str(e)}"
        )


@app.get("/users/{user_id}", response_model=dict)
async def get_user_profile(user_id: str):
    """
    Retrieve a user profile from Firestore.
    """
    if not handler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not initialized"
        )
    
    profile = await handler.get_user_profile(user_id)
    
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found: {user_id}"
        )
    
    return profile.to_firestore_dict()


@app.get("/debug/user/{user_id}")
async def debug_user_status(user_id: str):
    """
    Debug endpoint to check user status across all collections.
    """
    result = {
        "user_id": user_id,
        "auth_user": "Not checked",
        "profile": "Not checked", 
        "menu": "Not checked",
        "handlers": {
            "auth_service": auth_service is not None,
            "handler": handler is not None,
            "menu_generator": menu_generator is not None
        }
    }
    
    # Check Auth
    if auth_service:
        try:
            user = auth_service.get_user_by_id(user_id)
            result["auth_user"] = "Found" if user else "Not Found"
        except Exception as e:
            result["auth_user"] = f"Error: {e}"
            
    # Check Profile
    if handler:
        try:
            profile = await handler.get_user_profile(user_id)
            result["profile"] = "Found" if profile else "Not Found"
            if profile:
                result["profile_data_preview"] = str(profile.to_firestore_dict())[:100]
        except Exception as e:
            result["profile"] = f"Error: {e}"
            
    # Check Menu
    if menu_generator:
        try:
            menu = menu_generator.get_latest_menu(user_id)
            result["menu"] = "Found" if menu else "Not Found"
        except Exception as e:
            result["menu"] = f"Error: {e}"
            
    return result


# Mount static files (Frontend)
# html=True allows serving index.html at root /
app.mount("/", StaticFiles(directory="frontend", html=True), name="static")


# =============================================================================
# Local Development Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
