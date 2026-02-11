
from onboarding_agent import MockOnboardingConversationHandler, UserProfile

def test_mock_flow():
    handler = MockOnboardingConversationHandler()
    session_id, initial_msg = handler.start_conversation({"city": "Test City", "country": "Test Country"})
    print(f"Assistant: {initial_msg}")

    # 1. Household size
    response, _ = handler.send_message(session_id, "Two adults")
    print(f"User: Two adults")
    print(f"Assistant: {response}")
    assert "dietary restrictions" in response.lower()

    # 2. Dietary restrictions
    response, _ = handler.send_message(session_id, "None")
    print(f"User: None")
    print(f"Assistant: {response}")
    # This is the new question
    assert "cuisines" in response.lower() or "dishes" in response.lower()

    # 3. Cuisine preferences (New)
    response, _ = handler.send_message(session_id, "Italian and Mexican")
    print(f"User: Italian and Mexican")
    print(f"Assistant: {response}")
    assert "dislike" in response.lower()

    # 4. Dislikes
    response, _ = handler.send_message(session_id, "Mushrooms")
    print(f"User: Mushrooms")
    print(f"Assistant: {response}")
    assert "cooking schedule" in response.lower()

    # 5. Schedule
    response, is_complete = handler.send_message(session_id, "Dinner every night")
    print(f"User: Dinner every night")
    print(f"Assistant: {response}")
    assert is_complete
    
    print("\nTest Passed: Mock flow follows expectations.")

if __name__ == "__main__":
    test_mock_flow()
