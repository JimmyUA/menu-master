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

# Delayed imports for google.cloud and vertexai to allow module loading without dependencies
# from google.cloud import firestore
# import vertexai
# ...
from pydantic import BaseModel, Field

from onboarding_agent import UserProfile, LocationData, HouseholdInfo, WeeklySchedule, DailyMeals


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models for Menu
# =============================================================================

# =============================================================================
# Pydantic Models for Menu (Strict for Generation)
# =============================================================================

class MenuSlot(BaseModel):
    """Details for a specific meal."""
    name: str = Field(..., description="Name of the dish or 'SKIPPED'")
    description: str = Field(..., description="Brief description of the dish")
    ingredients: list[str] = Field(..., description="List of main ingredients")
    preparation_steps: list[str] = Field(..., description="Step-by-step preparation instructions")


class StrictDailyMenu(BaseModel):
    """Menu for a specific day (Strict for generation)."""
    breakfast: MenuSlot
    lunch: MenuSlot
    dinner: MenuSlot


class StrictWeeklyMenu(BaseModel):
    """Weekly meal plan (Strict for generation)."""
    monday: StrictDailyMenu
    tuesday: StrictDailyMenu
    wednesday: StrictDailyMenu
    thursday: StrictDailyMenu
    friday: StrictDailyMenu
    saturday: StrictDailyMenu
    sunday: StrictDailyMenu


# =============================================================================
# Pydantic Models for Persistence (With Optionals)
# =============================================================================

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
2. IMPORTANT: You must return a strict JSON object where EVERY meal slot (breakfast, lunch, dinner) is present.
3. If a meal slot is NOT marked as "Cook at home" in the schedule:
    - Set "name" to "SKIPPED"
    - Set "description" to "Skipped"
    - Set "ingredients" to []
    - Set "preparation_steps" to []
4. For valid meals, provide full details including step-by-step preparation instructions.
5. Use local ingredients available in {city}, {country} where possible.

Return the result as a JSON object adhering to the schema provided.
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

        self.menu_collection = menu_collection
        self.user_collection = user_collection

        # Initialize Vertex AI
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel
            vertexai.init(project=project_id, location=location)
            self.model = GenerativeModel("gemini-2.0-flash-001")
            logger.info("Vertex AI initialized successfully")
        except ImportError:
             logger.error("Vertex AI libraries not installed.")
             if os.environ.get("MOCK_MODE") != "true":
                 raise
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI: {e}")
            raise

        # Initialize Firestore
        try:
            from google.cloud import firestore
            self.db = firestore.Client(project=project_id)
            logger.info("Firestore client initialized successfully")
        except ImportError:
             logger.error("Firestore libraries not installed.")
             if os.environ.get("MOCK_MODE") != "true":
                 raise
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
            from vertexai.generative_models import GenerationConfig
            response = self.model.generate_content(
                prompt,
                generation_config=GenerationConfig(
                    temperature=0.7,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                    response_schema=StrictWeeklyMenu.model_json_schema(),
                ),
            )
            
            # Parse response into Strict models
            menu_data = json.loads(response.text)
            strict_menu = StrictWeeklyMenu(**menu_data)
            
            # Convert to nullable WeeklyMenu
            return self._convert_strict_to_weekly_menu(strict_menu)

        except Exception as e:
            logger.error(f"Error generating menu for user {user_profile.user_id}: {e}")
            return None

    def _convert_strict_to_weekly_menu(self, strict_menu: StrictWeeklyMenu) -> WeeklyMenu:
        """Convert StrictWeeklyMenu (with SKIPPED) to WeeklyMenu (with None)."""
        
        def convert_daily(strict_daily: StrictDailyMenu) -> DailyMenu:
            return DailyMenu(
                breakfast=strict_daily.breakfast if strict_daily.breakfast.name != "SKIPPED" else None,
                lunch=strict_daily.lunch if strict_daily.lunch.name != "SKIPPED" else None,
                dinner=strict_daily.dinner if strict_daily.dinner.name != "SKIPPED" else None,
            )

        return WeeklyMenu(
            monday=convert_daily(strict_menu.monday),
            tuesday=convert_daily(strict_menu.tuesday),
            wednesday=convert_daily(strict_menu.wednesday),
            thursday=convert_daily(strict_menu.thursday),
            friday=convert_daily(strict_menu.friday),
            saturday=convert_daily(strict_menu.saturday),
            sunday=convert_daily(strict_menu.sunday),
        )

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

    def get_latest_menu(self, user_id: str) -> Optional[GeneratedMenuDocument]:
        """Retrieve the latest generated menu for a user."""
        try:
            # Query for user's menus
            from google.cloud import firestore
            docs = self.db.collection(self.menu_collection).where("user_id", "==", user_id).stream()
            
            all_menus = []
            for doc in docs:
                try:
                    all_menus.append(GeneratedMenuDocument(**doc.to_dict()))
                except Exception as parse_error:
                    logger.warning(f"Skipping invalid menu document {doc.id}: {parse_error}")
                    continue
            
            if not all_menus:
                return None
            
            # Sort by week_start_date descending (YYYY-MM-DD string sort works)
            all_menus.sort(key=lambda x: x.week_start_date, reverse=True)
            
            return all_menus[0]
            
        except Exception as e:
            logger.error(f"Error retrieving latest menu for {user_id}: {e}")
            return None

    def generate_menu_for_user(self, user_id: str) -> bool:
        """
        Generate a menu for a single user by fetching their profile.
        
        This is triggered after onboarding is complete.
        Returns True if successful, False otherwise.
        """
        logger.info(f"Generating menu for newly onboarded user: {user_id}")
        
        try:
            # Fetch user profile from Firestore
            user_doc = self.db.collection(self.user_collection).document(user_id).get()
            
            if not user_doc.exists:
                logger.error(f"User profile not found: {user_id}")
                return False
            
            user_data = user_doc.to_dict()
            
            # Reconstruct UserProfile from Firestore data
            user_profile = UserProfile(
                user_id=user_data.get("user_id", user_id),
                location=LocationData(**user_data.get("location", {})),
                household=HouseholdInfo(**user_data.get("household", {"adults": 1, "children": 0})),
                dietary_preferences=user_data.get("dietary_preferences", []),
                allergies_dislikes=user_data.get("allergies_dislikes", []),
                meal_schedule=WeeklySchedule(**user_data.get("meal_schedule", {})),
            )
            
            # Calculate this week's start date (current Monday)
            today = datetime.now()
            current_monday = today - timedelta(days=today.weekday())
            week_start_date = current_monday.strftime("%Y-%m-%d")
            
            # Generate the menu
            menu = self.generate_weekly_menu(user_profile, week_start_date)
            
            if menu:
                self.save_menu(user_id, week_start_date, menu)
                logger.info(f"Successfully generated first menu for user {user_id}")
                return True
            else:
                logger.error(f"Failed to generate menu content for user {user_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error generating menu for user {user_id}: {e}")
            return False

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
