// API_BASE is defined in auth.js
// const API_BASE = window.location.origin;

// State
let currentMenu = null;
let allDishes = [];
let currentUser = null;
let userRatings = {}; // Store user's dish ratings { dish_name: { rating: 'thumbs_up', feedback_text: '...', extracted_preferences: {} } }
let currentDay = 'monday'; // Track current selected day

// Elements
const weekDateEl = document.getElementById('weekDate');
const dishGridEl = document.getElementById('dishGrid');
const daysNavEl = document.getElementById('daysNav');
const modal = document.getElementById('dishModal');
const closeModalBtn = document.getElementById('closeModal');

// Modal Elements
const modalTitle = document.getElementById('modalTitle');
const modalDescription = document.getElementById('modalDescription');
const modalIngredients = document.getElementById('modalIngredients');
const modalSteps = document.getElementById('modalSteps');

// Initialize
console.log("Menu.js: Script Execution Started");

function initMenu() {
    console.log("Menu.js: initMenu called");

    // Auth check
    if (!window.authUtils || !window.authUtils.isAuthenticated()) {
        console.log("Menu.js: Not authenticated, redirecting...");
        window.location.href = '/login.html';
        return;
    }

    console.log("Menu.js: Authenticated");
    currentUser = window.authUtils.getUserFromToken();
    console.log("Menu.js: Current user", currentUser);

    // If not onboarded, redirect to onboarding
    if (currentUser && !currentUser.is_onboarded) {
        console.log("Menu.js: Not onboarded, redirecting...");
        window.location.href = '/';
        return;
    }

    // Get user ID from auth token
    const userId = currentUser.user_id;

    console.log(`Menu.js: Fetching menu for ${userId}`);
    fetchMenu(userId);
}

// Robust initialization
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initMenu);
} else {
    // DOM is already ready
    initMenu();
}

console.log("Menu.js: Script Loaded (Listeners attached)");

// Fetch Menu
async function fetchMenu(userId) {
    try {
        const token = localStorage.getItem('menu_master_token') || localStorage.getItem('token');
        if (!token) {
            throw new Error('No authentication token found');
        }

        const response = await fetch(`${API_BASE}/menus/${userId}/current`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (!response.ok) {
            if (response.status === 404) {
                renderEmptyState();
                return;
            }
            const errorText = await response.text();
            throw new Error(`Failed to fetch menu: ${response.status} ${errorText}`);
        }

        const data = await response.json();
        currentMenu = data.menu;
        weekDateEl.textContent = `Week of ${data.week_start_date}`;

        // Load user ratings
        await loadUserRatings(userId);

        // Set current day to today if possible
        const today = new Date().toLocaleDateString('en-US', { weekday: 'long' }).toLowerCase();
        if (currentMenu[today]) {
            currentDay = today;
        }

        processMenuData(currentMenu); // Populate allDishes for search and modal lookups
        renderTabs();
        renderMenuForDay(currentDay);

    } catch (error) {
        console.error('Error fetching menu:', error);
        dishGridEl.innerHTML = `<div class="error-msg">Error loading menu: ${error.message}</div>`;
    }
}

// Render Tabs
function renderTabs() {
    const days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

    daysNavEl.innerHTML = days.map(day => `
        <button class="day-tab ${day === currentDay ? 'active' : ''}" onclick="switchDay('${day}')">
            ${capitalize(day)}
        </button>
    `).join('');
}

// Switch Day
window.switchDay = (day) => {
    currentDay = day;
    renderTabs(); // Re-render to update active state
    renderMenuForDay(day);
};

// Render Menu for Specific Day
function renderMenuForDay(day) {
    const dailyMenu = currentMenu[day];

    if (!dailyMenu) {
        dishGridEl.innerHTML = '<div class="empty-state"><p>No menu planned for this day.</p></div>';
        return;
    }

    // Define meal order
    const mealOrder = ['breakfast', 'lunch', 'dinner'];
    const mealIcons = {
        'breakfast': '🍳',
        'lunch': '🥗',
        'dinner': '🍽️'
    };

    let html = '';

    mealOrder.forEach(mealType => {
        const dish = dailyMenu[mealType];
        if (dish) {
            const existingRating = getRatingForDish(dish.name);
            const thumbsUpActive = existingRating?.rating === 'thumbs_up' ? 'active' : '';
            const thumbsDownActive = existingRating?.rating === 'thumbs_down' ? 'active' : '';

            html += `
                <div class="meal-section">
                    <div class="meal-title">
                        <span class="meal-icon">${mealIcons[mealType]}</span>
                        ${capitalize(mealType)}
                    </div>
                    <div class="dish-grid" style="padding-bottom: 0;">
                        <div class="dish-card" onclick="openDishModal('${day}', '${mealType}')">
                            <h3>${dish.name}</h3>
                            <p>${dish.description}</p>
                            <div class="rating-buttons" data-dish-name="${dish.name}" onclick="event.stopPropagation()">
                                <button class="thumbs-up ${thumbsUpActive}" 
                                        onclick="handleRating('${dish.name}', 'thumbs_up')" 
                                        title="I liked this">
                                    👍
                                </button>
                                <button class="thumbs-down ${thumbsDownActive}" 
                                        onclick="handleRating('${dish.name}', 'thumbs_down')" 
                                        title="Not for me">
                                    👎
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }
    });

    if (html === '') {
        html = '<div class="empty-state"><p>No meals scheduled for this day.</p></div>';
    }

    dishGridEl.innerHTML = html;
}

// Process Menu to populate allDishes for Lookups/Search
function processMenuData(menu) {
    allDishes = [];
    const days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

    days.forEach(day => {
        const dailyMenu = menu[day];
        if (!dailyMenu) return;

        ['breakfast', 'lunch', 'dinner'].forEach(mealType => {
            const dish = dailyMenu[mealType];
            if (dish) {
                allDishes.push({
                    day: day,
                    type: mealType,
                    ...dish
                });
            }
        });
    });
}

// Modal Logic
window.openDishModal = (day, type) => {
    const dish = allDishes.find(d => d.day === day && d.type === type);
    if (!dish) return;

    modalTitle.textContent = dish.name;
    modalDescription.textContent = dish.description;

    modalIngredients.innerHTML = dish.ingredients.map(ing => `<li>${ing}</li>`).join('');
    modalSteps.innerHTML = dish.preparation_steps.map(step => `<li>${step}</li>`).join('');

    modal.classList.remove('hidden');
};

closeModalBtn.addEventListener('click', () => {
    modal.classList.add('hidden');
});

modal.addEventListener('click', (e) => {
    if (e.target === modal) {
        modal.classList.add('hidden');
    }
});

// Close modal on ESC key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
        modal.classList.add('hidden');
    }
});

// Helpers
function capitalize(str) {
    return str.charAt(0).toUpperCase() + str.slice(1);
}

function renderEmptyState() {
    dishGridEl.innerHTML = `
        <div class="empty-state">
            <h3>No Menu Yet</h3>
            <p>It looks like you haven't generated a menu yet.</p>
            <a href="/" class="primary-btn" style="display:inline-block; margin-top:1rem; width:auto;">Create a Meal Plan</a>
        </div>
    `;
    weekDateEl.textContent = "";
}

// =============================================================================
// Rating Functions
// =============================================================================

async function loadUserRatings(userId) {
    try {
        const token = window.authUtils.getToken();
        const response = await fetch(`${API_BASE}/ratings/${userId}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            // Convert array to object keyed by dish_name for easy lookup
            userRatings = {};
            data.ratings.forEach(rating => {
                userRatings[rating.dish_name] = rating;
            });
            console.log('Loaded user ratings:', userRatings);
        }
    } catch (error) {
        console.error('Error loading ratings:', error);
    }
}

async function rateDish(dishName, rating, feedbackText = '') {
    try {
        const token = window.authUtils.getToken();
        const response = await fetch(`${API_BASE}/ratings/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                user_id: currentUser.user_id,
                dish_name: dishName,
                rating: rating,
                feedback_text: feedbackText
            })
        });

        if (!response.ok) {
            throw new Error('Failed to submit rating');
        }

        const data = await response.json();

        // Update local ratings
        userRatings[dishName] = {
            dish_name: dishName,
            rating: rating,
            feedback_text: feedbackText,
            extracted_preferences: data.extracted_preferences || {}
        };

        // Update UI
        updateRatingUI(dishName, rating, data.extracted_preferences);

        // Show success message if preferences were extracted
        if (data.extracted_preferences && Object.keys(data.extracted_preferences).length > 0) {
            showSuccessToast(data.extracted_preferences);
        }

        return data;
    } catch (error) {
        console.error('Error submitting rating:', error);
        alert('Failed to save rating. Please try again.');
    }
}

function updateRatingUI(dishName, rating, extractedPrefs) {
    // Update all rating buttons for this dish (both in card and modal)
    const ratingContainers = document.querySelectorAll(`[data-dish-name="${dishName}"]`);

    ratingContainers.forEach(container => {
        const thumbsUpBtn = container.querySelector('.thumbs-up');
        const thumbsDownBtn = container.querySelector('.thumbs-down');

        if (thumbsUpBtn && thumbsDownBtn) {
            thumbsUpBtn.classList.remove('active');
            thumbsDownBtn.classList.remove('active');

            if (rating === 'thumbs_up') {
                thumbsUpBtn.classList.add('active');
            } else if (rating === 'thumbs_down') {
                thumbsDownBtn.classList.add('active');
            }
        }
    });
}

function getRatingForDish(dishName) {
    return userRatings[dishName] || null;
}

function showSuccessToast(preferences) {
    // Extract a summary of learned preferences
    const prefSummary = [];
    for (const [category, prefs] of Object.entries(preferences)) {
        if (prefs && prefs.length > 0) {
            prefSummary.push(prefs[0]); // Show first preference from each category
        }
    }

    if (prefSummary.length === 0) return;

    const toast = document.createElement('div');
    toast.className = 'success-toast';
    toast.innerHTML = `
        <strong>Thanks!</strong> We learned: ${prefSummary.join(', ')}
    `;
    document.body.appendChild(toast);

    // Animate in
    setTimeout(() => toast.classList.add('show'), 100);

    // Remove after 4 seconds
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function createRatingButtons(dishName) {
    const existingRating = getRatingForDish(dishName);

    const container = document.createElement('div');
    container.className = 'rating-buttons';
    container.setAttribute('data-dish-name', dishName);

    container.innerHTML = `
        <button class="thumbs-up ${existingRating?.rating === 'thumbs_up' ? 'active' : ''}" 
                onclick="handleRating('${dishName}', 'thumbs_up')" 
                title="I liked this">
            👍
        </button>
        <button class="thumbs-down ${existingRating?.rating === 'thumbs_down' ? 'active' : ''}" 
                onclick="handleRating('${dishName}', 'thumbs_down')" 
                title="Not for me">
            👎
        </button>
    `;

    return container;
}

window.handleRating = async function (dishName, rating) {
    // Submit rating immediately
    await rateDish(dishName, rating);

    // Show feedback input (optional)
    showFeedbackInput(dishName, rating);
};

function showFeedbackInput(dishName, rating) {
    const container = document.querySelector(`[data-dish-name="${dishName}"]`);
    if (!container) return;

    // Check if feedback input already exists
    if (container.querySelector('.feedback-input-container')) {
        return;
    }

    const feedbackContainer = document.createElement('div');
    feedbackContainer.className = 'feedback-input-container';
    feedbackContainer.innerHTML = `
        <textarea class="feedback-textarea" 
                  placeholder="What did you think? (optional)" 
                  rows="2"></textarea>
        <div class="feedback-actions">
            <button class="feedback-submit" onclick="submitFeedback('${dishName}', '${rating}')">Submit</button>
            <button class="feedback-skip" onclick="closeFeedbackInput('${dishName}')">Skip</button>
        </div>
    `;

    container.appendChild(feedbackContainer);

    // Focus textarea
    const textarea = feedbackContainer.querySelector('.feedback-textarea');
    textarea.focus();

    // Auto-close after 10 seconds if no input
    setTimeout(() => {
        if (textarea.value.trim() === '') {
            closeFeedbackInput(dishName);
        }
    }, 10000);
}

window.submitFeedback = async function (dishName, rating) {
    const container = document.querySelector(`[data-dish-name="${dishName}"]`);
    const textarea = container.querySelector('.feedback-textarea');
    const feedbackText = textarea.value.trim();

    if (!feedbackText) {
        closeFeedbackInput(dishName);
        return;
    }

    // Show loading state
    const submitBtn = container.querySelector('.feedback-submit');
    const originalText = submitBtn.textContent;
    submitBtn.textContent = 'Analyzing...';
    submitBtn.disabled = true;

    // Submit with feedback
    await rateDish(dishName, rating, feedbackText);

    // Close feedback input
    closeFeedbackInput(dishName);
};

window.closeFeedbackInput = function (dishName) {
    const container = document.querySelector(`[data-dish-name="${dishName}"]`);
    const feedbackContainer = container?.querySelector('.feedback-input-container');
    if (feedbackContainer) {
        feedbackContainer.classList.add('closing');
        setTimeout(() => feedbackContainer.remove(), 300);
    }
};
