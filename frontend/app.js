// API_BASE is already defined in auth.js, so we just use it from there
let sessionId = null;
let profile = null;
let currentUser = null;

// Auth check at startup - run immediately
(function checkAuth() {
    // Check if authUtils exists
    if (typeof window.authUtils === 'undefined') {
        console.error('authUtils not loaded, redirecting to login');
        window.location.href = '/login.html';
        return;
    }

    // Check if user is authenticated
    if (!window.authUtils.isAuthenticated()) {
        console.log('User not authenticated, redirecting to login');
        window.location.href = '/login.html';
        return;
    }

    // Get user info from token
    currentUser = window.authUtils.getUserFromToken();

    // If already onboarded, redirect to menu
    if (currentUser && currentUser.is_onboarded) {
        console.log('User already onboarded, redirecting to menu');
        window.location.href = '/menu.html';
        return;
    }

    console.log('User authenticated and not onboarded, showing onboarding');
})();

// DOM Elements
const startModal = document.getElementById('startModal');
const profileModal = document.getElementById('profileModal');
const startForm = document.getElementById('startForm');
const chatContainer = document.getElementById('chatContainer');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const profileResult = document.getElementById('profileResult');

// Start Conversation
startForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const city = document.getElementById('cityInput').value;
    const country = document.getElementById('countryInput').value;

    setLoading(true);

    try {
        const response = await fetch(`${API_BASE}/onboarding/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ city, country })
        });

        if (!response.ok) throw new Error('Failed to start session');

        const data = await response.json();
        sessionId = data.session_id;

        // Hide modal and enable chat
        startModal.classList.add('hidden');
        enableChat();

        // Show welcome message
        addMessage(data.message, 'assistant');

    } catch (error) {
        alert('Error starting session: ' + error.message);
    } finally {
        setLoading(false);
    }
});

// Send Message
async function handleSendMessage() {
    const text = messageInput.value.trim();
    if (!text || !sessionId) return;

    // UI Updates
    addMessage(text, 'user');
    messageInput.value = '';
    setLoading(true);

    try {
        const response = await fetch(`${API_BASE}/onboarding/message`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId,
                message: text
            })
        });

        const data = await response.json();
        addMessage(data.message, 'assistant');

        if (data.is_complete) {
            await finalizeProfile();
        }

    } catch (error) {
        addMessage('Error: Could not send message. Please try again.', 'assistant');
        console.error(error);
    } finally {
        setLoading(false);
        messageInput.focus();
    }
}

sendBtn.addEventListener('click', handleSendMessage);
messageInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') handleSendMessage();
});

// Finalize Profile
async function finalizeProfile() {
    addMessage("Creating your profile...", 'assistant');

    try {
        // Get user ID from auth token
        const userId = currentUser ? currentUser.user_id : sessionId;

        const response = await fetch(`${API_BASE}/onboarding/finalize`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${window.authUtils.getToken()}`
            },
            body: JSON.stringify({
                session_id: sessionId,
                user_id: userId
            })
        });

        const data = await response.json();

        if (data.success) {
            profile = data.profile;

            // Save the new token with is_onboarded=true
            if (data.access_token) {
                window.authUtils.setToken(data.access_token);
                console.log('Saved new token with is_onboarded=true');
            }

            showProfileModal(profile);
        }

    } catch (error) {
        console.error("Finalization failed:", error);
        addMessage("Something went wrong creating your profile.", 'assistant');
    }
}


// Helpers
function addMessage(text, role) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.innerHTML = `<div class="bubble">${text}</div>`;
    chatContainer.appendChild(div);
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function enableChat() {
    messageInput.disabled = false;
    sendBtn.disabled = false;
    messageInput.focus();
}

function setLoading(isLoading) {
    if (isLoading) {
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<div class="loader">...</div>'; // Simple loader text
    } else {
        sendBtn.disabled = false;
        sendBtn.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="22" y1="2" x2="11" y2="13"></line>
                <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>`;
    }
}

function showProfileModal(data) {
    // profileResult.textContent = JSON.stringify(data, null, 2); // OLD
    const container = document.getElementById('profileDisplay');
    container.innerHTML = ''; // Clear previous

    // Helper to create card
    const createCard = (title, icon, content) => {
        const card = document.createElement('div');
        card.className = 'profile-card';
        card.innerHTML = `<h3>${icon} ${title}</h3>${content}`;
        return card;
    };

    // 1. Location & Household
    const loc = data.location || {};
    const house = data.household || {};
    const basicContent = `
        <div class="profile-row">
            <span class="profile-label">City</span>
            <span class="profile-value">${loc.city || '-'}</span>
        </div>
        <div class="profile-row">
            <span class="profile-label">Country</span>
            <span class="profile-value">${loc.country || '-'}</span>
        </div>
        <div class="profile-row">
            <span class="profile-label">Adults</span>
            <span class="profile-value">${house.adults || 0}</span>
        </div>
        <div class="profile-row">
            <span class="profile-label">Children</span>
            <span class="profile-value">${house.children || 0}</span>
        </div>
    `;
    container.appendChild(createCard('Basics', '📍', basicContent));

    // 2. Dietary Preferences
    const diet = data.dietary_preferences || [];
    const allergies = data.allergies_dislikes || [];

    let dietContent = '<div class="profile-row" style="display:block"><div class="profile-label" style="margin-bottom:0.5rem">Preferences</div><div class="tag-container">';
    if (diet.length === 0) dietContent += '<span class="profile-label">None</span>';
    diet.forEach(item => {
        dietContent += `<span class="profile-tag">${item}</span>`;
    });
    dietContent += '</div></div>';

    dietContent += '<div class="profile-row" style="display:block; border:none"><div class="profile-label" style="margin-bottom:0.5rem">Allergies / Dislikes</div><div class="tag-container">';
    if (allergies.length === 0) dietContent += '<span class="profile-label">None</span>';
    allergies.forEach(item => {
        dietContent += `<span class="profile-tag allergy">${item}</span>`;
    });
    dietContent += '</div></div>';

    container.appendChild(createCard('Dietary', '🥗', dietContent));

    // 3. Meal Schedule
    const schedule = data.meal_schedule || {};
    const days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

    let scheduleHtml = `
        <div class="schedule-grid">
            <div class="schedule-row schedule-header">
                <span>Day</span>
                <span style="text-align:center">B</span>
                <span style="text-align:center">L</span>
                <span style="text-align:center">D</span>
            </div>
    `;

    const checkIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
    const dashIcon = `<span style="opacity:0.3">•</span>`;

    days.forEach(day => {
        const dayData = schedule[day] || { breakfast: false, lunch: false, dinner: false };
        // Handle short day names
        const shortDay = day.charAt(0).toUpperCase() + day.slice(1, 3);

        scheduleHtml += `
            <div class="schedule-row">
                <span class="day-label">${shortDay}</span>
                <div class="meal-slot ${dayData.breakfast ? 'active' : ''}">${dayData.breakfast ? checkIcon : dashIcon}</div>
                <div class="meal-slot ${dayData.lunch ? 'active' : ''}">${dayData.lunch ? checkIcon : dashIcon}</div>
                <div class="meal-slot ${dayData.dinner ? 'active' : ''}">${dayData.dinner ? checkIcon : dashIcon}</div>
            </div>
        `;
    });
    scheduleHtml += '</div>';

    // Make the schedule card span full width if possible, or just normal
    const scheduleCard = createCard('Weekly Schedule', '📅', scheduleHtml);
    scheduleCard.style.gridColumn = '1 / -1'; // Span all columns
    container.appendChild(scheduleCard);


    // Add View Menu Button if not already there
    let menuBtn = document.getElementById('viewMenuBtn');
    if (!menuBtn) {
        menuBtn = document.createElement('a');
        menuBtn.id = 'viewMenuBtn';
        menuBtn.href = '/menu.html';
        menuBtn.className = 'primary-btn';
        menuBtn.style.textAlign = 'center';
        menuBtn.style.display = 'block';
        menuBtn.style.marginBottom = '1rem';
        menuBtn.style.textDecoration = 'none';
        menuBtn.textContent = 'View My Menu 🍲';

        // Insert before 'Start Over' button
        const startOverBtn = profileModal.querySelector('.secondary-btn');
        profileModal.querySelector('.modal').insertBefore(menuBtn, startOverBtn);
    }

    profileModal.classList.remove('hidden');
}

// Temporary helper to preview the profile modal
window.previewProfile = function () {
    const mockProfile = {
        "user_id": "test_user_123",
        "location": {
            "city": "Milano",
            "country": "Italy"
        },
        "household": {
            "adults": 2,
            "children": 2
        },
        "dietary_preferences": [
            "Mediterranean",
            "Italian"
        ],
        "allergies_dislikes": [
            "wheat",
            "tomatos"
        ],
        "meal_schedule": {
            "monday": { "breakfast": true, "lunch": true, "dinner": true },
            "tuesday": { "breakfast": true, "lunch": true, "dinner": true },
            "wednesday": { "breakfast": true, "lunch": true, "dinner": true },
            "thursday": { "breakfast": true, "lunch": true, "dinner": true },
            "friday": { "breakfast": true, "lunch": true, "dinner": true },
            "saturday": { "breakfast": false, "lunch": false, "dinner": false },
            "sunday": { "breakfast": false, "lunch": false, "dinner": false }
        },
        "created_at": new Date().toISOString()
    };

    console.log("Showing mock profile...");
    showProfileModal(mockProfile);
};

// Add a visible button for easy access during dev
const btn = document.createElement('button');
btn.textContent = '👁️ Preview Profile';
btn.style.position = 'fixed';
btn.style.bottom = '10px';
btn.style.right = '10px';
btn.style.zIndex = '9999';
btn.style.padding = '0.5rem 1rem';
btn.style.background = '#8b5cf6';
btn.style.color = 'white';
btn.style.border = 'none';
btn.style.borderRadius = '0.5rem';
btn.style.cursor = 'pointer';
btn.onclick = window.previewProfile;
document.body.appendChild(btn);
