const API_BASE = window.location.origin;

// State
let currentMenu = null;
let allDishes = [];

// Elements
const weekDateEl = document.getElementById('weekDate');
const dishGridEl = document.getElementById('dishGrid');
const searchInput = document.getElementById('dishSearch');
const modal = document.getElementById('dishModal');
const closeModalBtn = document.getElementById('closeModal');

// Modal Elements
const modalTitle = document.getElementById('modalTitle');
const modalDescription = document.getElementById('modalDescription');
const modalIngredients = document.getElementById('modalIngredients');
const modalSteps = document.getElementById('modalSteps');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    // Try to get user ID from localStorage (set during onboarding)
    // For now, we might need a way to mock this or get it from URL parameters
    const urlParams = new URLSearchParams(window.location.search);
    let userId = urlParams.get('user_id');

    if (!userId) {
        // Fallback to localStorage if available (assuming app.js sets it)
        userId = localStorage.getItem('menu_master_user_id');
    }

    // Temporary: If still no user ID, show error or prompt
    // For dev ease, let's look for a hardcoded one or prompt
    if (!userId) {
        dishGridEl.innerHTML = '<div class="error-msg">No user ID found. Please go through onboarding first.</div>';
        return;
    }

    fetchMenu(userId);
});

// Fetch Menu
async function fetchMenu(userId) {
    try {
        const response = await fetch(`${API_BASE}/menus/${userId}/current`);

        if (!response.ok) {
            if (response.status === 404) {
                renderEmptyState();
                return;
            }
            throw new Error('Failed to fetch menu');
        }

        const data = await response.json();
        currentMenu = data.menu;
        weekDateEl.textContent = `Week of ${data.week_start_date}`;

        processMenuData(currentMenu);
        renderDishes(allDishes);

    } catch (error) {
        console.error(error);
        dishGridEl.innerHTML = `<div class="error-msg">Error loading menu: ${error.message}</div>`;
    }
}

// Process Menu into Flat List
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

// Render Grid
function renderDishes(dishes) {
    if (dishes.length === 0) {
        dishGridEl.innerHTML = '<div class="empty-msg">No dishes found matching your search.</div>';
        return;
    }

    dishGridEl.innerHTML = dishes.map(dish => `
        <div class="dish-card" onclick="openDishModal('${dish.day}', '${dish.type}')">
            <div class="dish-header">
                <span class="dish-day">${capitalize(dish.day)}</span>
                <span class="dish-type">${capitalize(dish.type)}</span>
            </div>
            <h3>${dish.name}</h3>
            <p>${dish.description}</p>
            <div class="dish-tags">
                <span>${dish.ingredients.length} ingredients</span>
            </div>
        </div>
    `).join('');
}

// Search Handler
searchInput.addEventListener('input', (e) => {
    const term = e.target.value.toLowerCase();

    const filtered = allDishes.filter(dish => {
        return (
            dish.name.toLowerCase().includes(term) ||
            dish.description.toLowerCase().includes(term) ||
            dish.ingredients.some(ing => ing.toLowerCase().includes(term))
        );
    });

    renderDishes(filtered);
});

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
