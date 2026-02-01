/**
 * Authentication utilities for Menu Master
 * Handles JWT tokens, Google Sign-In, and auth state management
 */

const API_BASE = window.location.origin;
const TOKEN_KEY = 'menu_master_token';

// =============================================================================
// Token Management
// =============================================================================

function getToken() {
    return localStorage.getItem(TOKEN_KEY);
}

function setToken(token) {
    localStorage.setItem(TOKEN_KEY, token);
}

function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
}

function isAuthenticated() {
    const token = getToken();
    if (!token) return false;

    // Check if token is expired
    try {
        const payload = parseJwt(token);
        const expiry = payload.exp * 1000; // Convert to milliseconds
        return Date.now() < expiry;
    } catch (e) {
        return false;
    }
}

function parseJwt(token) {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(atob(base64).split('').map(function (c) {
        return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
    }).join(''));
    return JSON.parse(jsonPayload);
}

function getUserFromToken() {
    const token = getToken();
    if (!token) return null;

    try {
        const payload = parseJwt(token);
        return {
            user_id: payload.sub,
            email: payload.email,
            is_onboarded: payload.is_onboarded || false
        };
    } catch (e) {
        return null;
    }
}

// =============================================================================
// API Calls
// =============================================================================

async function signup(email, password) {
    const response = await fetch(`${API_BASE}/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
    });

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Signup failed');
    }

    return response.json();
}

async function login(email, password) {
    const response = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
    });

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Login failed');
    }

    return response.json();
}

async function googleAuth(credential) {
    const response = await fetch(`${API_BASE}/auth/google`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential })
    });

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Google authentication failed');
    }

    return response.json();
}

async function getCurrentUser() {
    const token = getToken();
    if (!token) return null;

    const response = await fetch(`${API_BASE}/auth/me`, {
        headers: { 'Authorization': `Bearer ${token}` }
    });

    if (!response.ok) {
        if (response.status === 401) {
            clearToken();
            return null;
        }
        throw new Error('Failed to get user info');
    }

    return response.json();
}

// =============================================================================
// Redirect Logic
// =============================================================================

function redirectBasedOnStatus(user) {
    const currentPath = window.location.pathname;

    if (!user) {
        // Not authenticated - go to login
        if (currentPath !== '/login.html') {
            window.location.href = '/login.html';
        }
        return;
    }

    if (!user.is_onboarded) {
        // Authenticated but not onboarded - go to onboarding
        if (currentPath !== '/' && currentPath !== '/index.html') {
            window.location.href = '/';
        }
        return;
    }

    // Fully onboarded - go to menu
    if (currentPath === '/login.html' || currentPath === '/' || currentPath === '/index.html') {
        window.location.href = '/menu.html';
    }
}

function checkAuthAndRedirect() {
    const user = getUserFromToken();
    redirectBasedOnStatus(user);
}

function logout() {
    clearToken();
    window.location.href = '/login.html';
}

// =============================================================================
// Google Sign-In Callback
// =============================================================================

// This function is called by Google Identity Services
async function handleGoogleSignIn(response) {
    showLoading(true);
    hideError();

    try {
        const tokenResponse = await googleAuth(response.credential);
        setToken(tokenResponse.access_token);

        // Redirect based on onboarding status
        if (tokenResponse.is_onboarded) {
            window.location.href = '/menu.html';
        } else {
            window.location.href = '/';
        }
    } catch (error) {
        showError(error.message);
        showLoading(false);
    }
}

// Make it available globally for Google callback
window.handleGoogleSignIn = handleGoogleSignIn;

// =============================================================================
// Login Page Logic
// =============================================================================

// Only run if we're on the login page
if (document.getElementById('authForm')) {
    let isSignupMode = false;

    const authForm = document.getElementById('authForm');
    const authTitle = document.getElementById('authTitle');
    const authSubtitle = document.getElementById('authSubtitle');
    const submitBtn = document.getElementById('submitBtn');
    const switchText = document.getElementById('switchText');
    const switchBtn = document.getElementById('switchBtn');
    const errorMessage = document.getElementById('errorMessage');

    // Check if already authenticated
    if (isAuthenticated()) {
        const user = getUserFromToken();
        redirectBasedOnStatus(user);
    }

    // Toggle between login and signup
    switchBtn.addEventListener('click', () => {
        isSignupMode = !isSignupMode;
        updateAuthMode();
    });

    function updateAuthMode() {
        if (isSignupMode) {
            authTitle.textContent = 'Create Account';
            authSubtitle.textContent = 'Start your personalized meal planning journey';
            submitBtn.textContent = 'Sign Up';
            switchText.textContent = 'Already have an account?';
            switchBtn.textContent = 'Sign In';
        } else {
            authTitle.textContent = 'Welcome Back';
            authSubtitle.textContent = 'Sign in to access your personalized meal plans';
            submitBtn.textContent = 'Sign In';
            switchText.textContent = "Don't have an account?";
            switchBtn.textContent = 'Sign Up';
        }
        hideError();
    }

    // Handle form submission
    authForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const email = document.getElementById('emailInput').value;
        const password = document.getElementById('passwordInput').value;

        showLoading(true);
        hideError();

        try {
            let tokenResponse;

            if (isSignupMode) {
                tokenResponse = await signup(email, password);
            } else {
                tokenResponse = await login(email, password);
            }

            setToken(tokenResponse.access_token);

            // Redirect based on onboarding status
            if (tokenResponse.is_onboarded) {
                window.location.href = '/menu.html';
            } else {
                window.location.href = '/';
            }
        } catch (error) {
            showError(error.message);
            showLoading(false);
        }
    });

    function showLoading(loading) {
        submitBtn.disabled = loading;
        submitBtn.textContent = loading ? 'Please wait...' : (isSignupMode ? 'Sign Up' : 'Sign In');
    }

    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.classList.remove('hidden');
    }

    function hideError() {
        errorMessage.classList.add('hidden');
    }
}

// Export functions for other scripts
window.authUtils = {
    getToken,
    setToken,
    clearToken,
    isAuthenticated,
    getUserFromToken,
    getCurrentUser,
    checkAuthAndRedirect,
    logout,
    parseJwt
};
