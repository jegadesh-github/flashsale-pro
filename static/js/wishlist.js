const WISHLIST_STORAGE_KEY = 'dropdeals_wishlist_v2';

function wishlistItems() {
    try {
        return JSON.parse(localStorage.getItem(WISHLIST_STORAGE_KEY)) || [];
    } catch {
        return [];
    }
}

function setWishlistItems(items) {
    localStorage.setItem(WISHLIST_STORAGE_KEY, JSON.stringify(items));
    document.dispatchEvent(new CustomEvent('wishlist:changed'));
}

function formatCurrency(value) {
    return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
        maximumFractionDigits: 0,
    }).format(Number(value || 0));
}

async function fetchWishlistDeals(ids) {
    if (!ids.length) {
        return [];
    }
    try {
        const response = await fetch('/api/wishlist/deals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        });
        if (!response.ok) {
            return [];
        }
        const data = await response.json();
        return data.items || [];
    } catch {
        return [];
    }
}

function renderWishlistSummary(items) {
    const total = items.length;
    const totalSavings = items.reduce((sum, item) => sum + Math.max((item.original_price || 0) - (item.price || 0), 0), 0);
    const totalNode = document.getElementById('wishlist-total');
    const savingsNode = document.getElementById('wishlist-savings');
    if (totalNode) totalNode.textContent = total;
    if (savingsNode) savingsNode.textContent = formatCurrency(totalSavings);
}

function removeWishlistItem(id) {
    const nextItems = wishlistItems().filter((item) => String(item.id) !== String(id));
    setWishlistItems(nextItems);
    renderWishlistPage();
}

function renderWishlistGrid(items) {
    const grid = document.getElementById('wishlist-grid');
    const empty = document.getElementById('wishlist-empty');
    if (!grid || !empty) return;

    if (!items.length) {
        grid.innerHTML = '';
        empty.classList.remove('d-none');
        return;
    }

    empty.classList.add('d-none');
    grid.innerHTML = items.map((item) => `
        <article class="wishlist-card">
            <div class="wishlist-card-media">
                <img src="${item.image_url}" alt="${item.name}">
            </div>
            <div class="wishlist-card-body">
                <p class="eyebrow mb-2">${item.category || 'Saved deal'}</p>
                <h3>${item.name}</h3>
                <p>${item.store_name || ''}</p>
                <div class="price-row">
                    <strong>${formatCurrency(item.price)}</strong>
                    <span>${formatCurrency(item.original_price)}</span>
                </div>
                <div class="wishlist-card-footer">
                    <a href="${item.outbound_url || '#'}" class="btn btn-dark rounded-pill" target="_blank" rel="noopener noreferrer">View deal</a>
                    <button class="btn btn-outline-dark rounded-pill remove-wishlist" data-id="${item.id}">Remove</button>
                </div>
            </div>
        </article>
    `).join('');

    grid.querySelectorAll('.remove-wishlist').forEach((button) => {
        button.addEventListener('click', () => removeWishlistItem(button.dataset.id));
    });
}

async function renderWishlistPage() {
    const localItems = wishlistItems();
    const ids = localItems.map((item) => item.id);
    const fetchedItems = await fetchWishlistDeals(ids);
    const merged = ids.map((id) => fetchedItems.find((item) => String(item.id) === String(id)) || localItems.find((item) => String(item.id) === String(id))).filter(Boolean);
    renderWishlistGrid(merged);
    renderWishlistSummary(merged);
    const count = document.getElementById('wishlist-count');
    if (count) count.textContent = merged.length;
}

function initWishlistPage() {
    const clearButton = document.getElementById('clear-wishlist');
    if (clearButton) {
        clearButton.addEventListener('click', () => {
            setWishlistItems([]);
            renderWishlistPage();
        });
    }
    document.addEventListener('wishlist:changed', renderWishlistPage);
    renderWishlistPage();
}

document.addEventListener('DOMContentLoaded', initWishlistPage);
