"""
Menu Generator Service

Generates weekly meal plans for users based on their profiles and preferences
using Gemini 1.5 Flash via Vertex AI.
"""

import logging
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.cloud import firestore
import vertexai
from vertexai.generative_models import (
    GenerativeModel,
    GenerationConfig,
    HarmCategory,
    HarmBlockThreshold,
)
from pydantic import BaseModel, Field

from onboarding_agent import UserProfile, LocationData, HouseholdInfo, WeeklySchedule, DailyMeals


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models for Menu
# =============================================================================

class MenuSlot(BaseModel):
    """Details for a specific meal."""
    name: str = Field(..., description="Name of the dish")
    description: str = Field(..., description="Brief description of the dish")
    ingredients: list[str] = Field(..., description="List of main ingredients")
    preparation_steps: list[str] = Field(..., description="Step-by-step preparation instructions")


class DailyMenu(BaseModel):
    """Menu for a specific day."""
    breakfast: Optional[MenuSlot] = None
    lunch: Optional[MenuSlot] = None
    dinner: Optional[MenuSlot] = None


class WeeklyMenu(BaseModel):
    """Weekly meal plan."""
    monday: DailyMenu
    tuesday: DailyMenu
    wednesday: DailyMenu
    thursday: DailyMenu
    friday: DailyMenu
    saturday: DailyMenu
    sunday: DailyMenu


class GeneratedMenuDocument(BaseModel):
    """Firestore document structure for a generated menu."""
    user_id: str
    week_start_date: str
    created_at: datetime
    menu: WeeklyMenu

    def to_firestore_dict(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        return {
            "user_id": self.user_id,
            "week_start_date": self.week_start_date,
            "created_at": self.created_at,
            "menu": self.menu.model_dump(),
        }


# =============================================================================
# Prompt Templates
# =============================================================================

MENU_GENERATION_PROMPT = """
You are a professional chef and nutritionist creating a personalized weekly meal plan.

User Profile:
- Location: {city}, {country}
- Household Size: {adults} adults, {children} children
- Dietary Preferences: {dietary_preferences}
- Allergies/Dislikes: {allergies_dislikes}

Cooking Schedule (Only generate meals for these slots):
{schedule_description}

Instructions:
1. Create a diverse and balanced menu for the week based strictly on the user's schedule.
2. If a meal slot (e.g., Monday Lunch) is NOT marked as "Cook at home", do NOT generate a menu for it (leave it null/empty).
3. Ensure recipes respect all dietary preferences and allergies.
4. For each meal, provide:
    - Name of the dish
    - Brief description
    - List of ingredients
    - Step-by-step preparation instructions
5. Use local ingredients available in {city}, {country} where possible.

Return the result as a JSON object adhering to this schema:
{{
    "monday": {{ "breakfast": {{...}}, "lunch": {{...}}, "dinner": {{...}} }},
    "tuesday": {{...}},
    ...
    "sunday": {{...}}
}}
If a slot is skipped, set it to null.
"""


# =============================================================================
# Menu Generator Service
# =============================================================================

class MenuGenerator:
    """
    Service to generate weekly menus for users.
    """

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        menu_collection: str = "generated_menus",
        user_collection: str = "users",
    ):
        """
        Initialize the menu generator.

        Args:
            project_id: Google Cloud project ID
            location: Vertex AI region
            menu_collection: Firestore collection for generated menus
            user_collection: Firestore collection for user profiles
        """
        self.project_id = project_id
        self.location = location
        self.menu_collection = menu_collection
        self.user_collection = user_collection

        # Initialize Vertex AI
        try:
            vertexai.init(project=project_id, location=location)
            self.model = GenerativeModel("gemini-2.0-flash-001")
            logger.info("Vertex AI initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI: {e}")
            raise

        # Initialize Firestore
        try:
            self.db = firestore.Client(project=project_id)
            logger.info("Firestore client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Firestore: {e}")
            raise

    def generate_weekly_menu(self, user_profile: UserProfile, week_start_date: str) -> Optional[WeeklyMenu]:
        """
        Generate a weekly menu for a specific user using Gemini.
        """
        logger.info(f"Generating menu for user: {user_profile.user_id}")

        schedule_desc = self._format_schedule_description(user_profile.meal_schedule)
        
        prompt = MENU_GENERATION_PROMPT.format(
            city=user_profile.location.city,
            country=user_profile.location.country,
            adults=user_profile.household.adults,
            children=user_profile.household.children,
            dietary_preferences=", ".join(user_profile.dietary_preferences) or "None",
            allergies_dislikes=", ".join(user_profile.allergies_dislikes) or "None",
            schedule_description=schedule_desc
        )

        try:
            response = self.model.generate_content(
                prompt,
                generation_config=GenerationConfig(
                    temperature=0.7,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                ),
            )
            
            # Parse and validate response
            menu_data = json.loads(response.text)
            weekly_menu = WeeklyMenu(**menu_data)
            return weekly_menu

        except Exception as e:
            logger.error(f"Error generating menu for user {user_profile.user_id}: {e}")
            return None

    def _format_schedule_description(self, schedule: WeeklySchedule) -> str:
        """Helper to create a readable description of the cooking schedule."""
        lines = []
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        
        for day in days:
            daily_meals: DailyMeals = getattr(schedule, day)
            meals = []
            if daily_meals.breakfast: includes = meals.append("Breakfast")
            if daily_meals.lunch: includes = meals.append("Lunch")
            if daily_meals.dinner: includes = meals.append("Dinner")
            
            if meals:
                lines.append(f"- {day.capitalize()}: {', '.join(meals)}")
            else:
                lines.append(f"- {day.capitalize()}: No meals cooked at home")
                
        return "\n".join(lines)

    def save_menu(self, user_id: str, week_start_date: str, menu: WeeklyMenu) -> None:
        """Save the generated menu to Firestore."""
        try:
            doc_id = f"{user_id}_{week_start_date}"
            menu_doc = GeneratedMenuDocument(
                user_id=user_id,
                week_start_date=week_start_date,
                created_at=datetime.now(timezone.utc),
                menu=menu
            )
            
            doc_ref = self.db.collection(self.menu_collection).document(doc_id)
            doc_ref.set(menu_doc.to_firestore_dict())
            logger.info(f"Saved menu for user {user_id} (Week: {week_start_date})")
            
        except Exception as e:
            logger.error(f"Error saving menu for {user_id}: {e}")
            raise

    def process_all_users(self):
        """
        Batch process to generate menus for all users.
        """
        logger.info("Starting batch menu generation job")
        
        # Calculate next week's start date (next Monday)
        today = datetime.now()
        next_monday = today + timedelta(days=(7 - today.weekday()))
        week_start_date = next_monday.strftime("%Y-%m-%d")
        
        users_ref = self.db.collection(self.user_collection)
        
        success_count = 0
        error_count = 0
        
        for doc in users_ref.stream():
            try:
                user_data = doc.to_dict()
                # Reconstruct UserProfile from Firestore data
                # Note: We need to handle potential schema mismatches gracefully
                user_profile = UserProfile(
                    user_id=user_data["user_id"],
                    location=LocationData(**user_data["location"]),
                    household=HouseholdInfo(**user_data["household"]),
                    dietary_preferences=user_data.get("dietary_preferences", []),
                    allergies_dislikes=user_data.get("allergies_dislikes", []),
                    meal_schedule=WeeklySchedule(**user_data.get("meal_schedule", {})),
                )
                
                menu = self.generate_weekly_menu(user_profile, week_start_date)
                
                if menu:
                    self.save_menu(user_profile.user_id, week_start_date, menu)
                    success_count += 1
                else:
                    error_count += 1
                    
            except Exception as e:
                logger.error(f"Error processing user {doc.id}: {e}")
                error_count += 1
                continue

        logger.info(f"Job completed. Success: {success_count}, Errors: {error_count}")
