
from onboarding_agent import UserProfile, LocationData, HouseholdInfo

def test_user_profile():
    # Test strict typing and new field
    profile = UserProfile(
        user_id="test_user",
        location=LocationData(city="Test", country="Test"),
        household=HouseholdInfo(adults=2, children=0),
        dietary_preferences=["Vegetarian"],
        cuisine_preferences=["Italian", "Thai"],
        allergies_dislikes=["Peanuts"]
    )
    
    data = profile.to_firestore_dict()
    print(f"Serialized Profile: {data}")
    
    assert "cuisine_preferences" in data
    assert data["cuisine_preferences"] == ["Italian", "Thai"]
    
    print("\nModel Test Passed")

if __name__ == "__main__":
    test_user_profile()
