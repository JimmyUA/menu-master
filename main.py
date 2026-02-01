"""
FastAPI Application for Onboarding Agent

Provides REST API endpoints for the conversational onboarding flow.
Designed for deployment on Cloud Run.
"""

import os
import json
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str = "1.0.0"


# =============================================================================
# Application Setup
# =============================================================================

# Global handler instance
handler: Optional[OnboardingConversationHandler] = None
menu_generator: Optional[MenuGenerator] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup."""
    global handler, menu_generator
    
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT environment variable is required")
    
    location = os.environ.get("VERTEX_AI_LOCATION", "us-central1")
    
    try:
        handler = OnboardingConversationHandler(
            project_id=project_id,
            location=location,
        )
    except Exception as e:
        print(f"Failed to initialize OnboardingConversationHandler: {e}. Onboarding endpoints will be unavailable.")
        handler = None

    try:
        menu_generator = MenuGenerator(
            project_id=project_id,
            location=location,
        )
    except Exception as e:
        print(f"Failed to initialize real MenuGenerator: {e}. Using Mock.")
        menu_generator = MockMenuGenerator(project_id, location)
    
    yield
    
    # Cleanup if needed
    handler = None
    menu_generator = None


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
        
        return FinalizeProfileResponse(
            profile=profile.to_firestore_dict(),
            success=True,
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
