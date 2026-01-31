"""
Local Verification Script for Menu Generation logic.
Mocks a user profile and runs the generator for a single user.
"""

import os
import asyncio
from datetime import datetime, timezone
from menu_generator import MenuGenerator, UserProfile, LocationData, HouseholdInfo, WeeklySchedule, DailyMeals

# Mock user profile
MOCK_USER = UserProfile(
    user_id="test_user_local",
    location=LocationData(city="San Francisco", country="United States"),
    household=HouseholdInfo(adults=2, children=1),
    dietary_preferences=["Vegetarian"],
    allergies_dislikes=["Mushrooms"],
    meal_schedule=WeeklySchedule(
        monday=DailyMeals(breakfast=True, lunch=False, dinner=True),
        tuesday=DailyMeals(breakfast=False, lunch=False, dinner=True),
        # ... other days default to empty/default
    )
)

async def test_generation():
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        print("Error: GOOGLE_CLOUD_PROJECT env var not set.")
        return

    print("Initializing MenuGenerator...")
    # Note: We are not mocking Firestore/Vertex calls here, so this will attempt real calls.
    # To run this locally, the user must have 'gcloud auth application-default login' set up
    # and be valid for the project.
    
    try:
        generator = MenuGenerator(project_id=project_id)
        
        print(f"Generating menu for {MOCK_USER.user_id}...")
        menu = generator.generate_weekly_menu(MOCK_USER, "2023-11-06")
        
        if menu:
            print("\nSUCCESS: Menu Generated!")
            print(f"Monday Dinner: {menu.monday.dinner}")
            if menu.monday.dinner:
                print(f"  Name: {menu.monday.dinner.name}")
                print(f"  Prep Steps: {menu.monday.dinner.preparation_steps[:1]} ...") 
        else:
            print("\nFAILURE: Menu generation returned None.")
            
    except Exception as e:
        print(f"\nEXCEPTION: {e}")

if __name__ == "__main__":
    asyncio.run(test_generation())
