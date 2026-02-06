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
    profileResult.textContent = JSON.stringify(data, null, 2);

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
