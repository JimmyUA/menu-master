// API_BASE is defined in auth.js
// const API_BASE = window.location.origin;

// State
let currentMenu = null;
let allDishes = [];
let currentUser = null;

// Elements
const weekDateEl = document.getElementById('weekDate');
const dishGridEl = document.getElementById('dishGrid');
const daysNavEl = document.getElementById('daysNav');
const searchInput = document.getElementById('dishSearch');
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
            // Reconstruct dish object for modal
            // We need to ensure it's in allDishes or accessible for the modal
            // For simplicity, let's just make it globally accessible via a lookup
            // Or just pass the data directly if we refactor openDishModal (skip for now to minimize changes)

            // NOTE: We need to populate allDishes or similar for the modal to work if we use the old logic
            // Let's populate a temporary list for the current day to make search work if needed, 
            // but for now let's just render the view.

            // Let's make sure our dish lookup works.
            // We'll update the global allDishes with the CURRENT day's dishes for now, 
            // OR we can just keep allDishes populated with everything for search, 
            // and just filter display for the tab.

            // Let's populate allDishes with everything once (like before) so search works globally?
            // If we do that, we need to handle the "Search" view vs "Tab" view.
            // Simple approach: Search overrides tabs.

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

    // Also update allDishes so the modal works!
    // We need to make sure the modal can find the dish.
    // Let's run processMenuData(currentMenu) once after fetching to populate allDishes for the modal lookups.

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

// Search Handler
searchInput.addEventListener('input', (e) => {
    const term = e.target.value.toLowerCase();

    if (term === '') {
        // Restore tab view when search is cleared
        daysNavEl.style.display = 'flex';
        renderMenuForDay(currentDay);
        return;
    }

    // Hide tabs when searching
    daysNavEl.style.display = 'none';

    const filtered = allDishes.filter(dish => {
        return (
            dish.name.toLowerCase().includes(term) ||
            dish.description.toLowerCase().includes(term)
        );
    });

    renderDishesFlat(filtered);
});

// Helper to render a flat list of dishes (used for search results)
function renderDishesFlat(dishes) {
    if (dishes.length === 0) {
        dishGridEl.innerHTML = '<div class="empty-msg">No dishes found matching your search.</div>';
        return;
    }

    dishGridEl.innerHTML = `
        <div class="dish-grid">
            ${dishes.map(dish => `
                <div class="dish-card" onclick="openDishModal('${dish.day}', '${dish.type}')">
                    <div class="dish-header">
                        <span class="dish-day">${capitalize(dish.day)}</span>
                        <span class="dish-type">${capitalize(dish.type)}</span>
                    </div>
                    <h3>${dish.name}</h3>
                    <p>${dish.description}</p>
                </div>
            `).join('')}
        </div>
    `;
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
