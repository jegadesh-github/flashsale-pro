const STORAGE_KEY = 'dropdeals_wishlist_v2';

function readWishlist() {
    try {
        return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
    } catch {
        return [];
    }
}

function writeWishlist(items) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    updateWishlistCount();
}

function updateWishlistCount() {
    const count = readWishlist().length;
    document.querySelectorAll('#wishlist-count').forEach((node) => {
        node.textContent = count;
    });
}

function normalizeButtonDeal(button) {
    return {
        id: String(button.dataset.id || ''),
        name: button.dataset.name || '',
        price: Number(button.dataset.price || 0),
        original_price: Number(button.dataset.originalPrice || 0),
        category: button.dataset.category || '',
        image_url: button.dataset.image || '',
        outbound_url: button.dataset.url || '#',
        store_name: button.dataset.store || '',
    };
}

function syncWishlistButtons() {
    const wishlistIds = new Set(readWishlist().map((item) => String(item.id)));
    document.querySelectorAll('.wishlist-toggle').forEach((button) => {
        const active = wishlistIds.has(String(button.dataset.id));
        button.classList.toggle('active', active);
        const icon = button.querySelector('i');
        if (icon) {
            icon.className = active ? 'fa-solid fa-heart' : 'fa-regular fa-heart';
        }
    });
}

function toggleWishlist(button) {
    const deal = normalizeButtonDeal(button);
    if (!deal.id) {
        return;
    }

    const wishlist = readWishlist();
    const index = wishlist.findIndex((item) => String(item.id) === deal.id);
    if (index >= 0) {
        wishlist.splice(index, 1);
    } else {
        wishlist.unshift(deal);
    }
    writeWishlist(wishlist);
    syncWishlistButtons();
    document.dispatchEvent(new CustomEvent('wishlist:changed'));
}

function initRevealCards() {
    const cards = document.querySelectorAll('.card-reveal');
    if (!cards.length || !('IntersectionObserver' in window)) {
        cards.forEach((card) => card.classList.add('visible'));
        return;
    }
    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.12 });
    cards.forEach((card) => observer.observe(card));
}

function initWishlistButtons() {
    document.querySelectorAll('.wishlist-toggle').forEach((button) => {
        button.addEventListener('click', (event) => {
            event.preventDefault();
            toggleWishlist(button);
        });
    });
    syncWishlistButtons();
    updateWishlistCount();
}

function initLoadMore() {
    const button = document.getElementById('load-more-btn');
    const cards = Array.from(document.querySelectorAll('.latest-deal-item'));
    if (!button || !cards.length) {
        return;
    }

    const step = 6;
    let visible = cards.filter((card) => !card.classList.contains('d-none')).length || step;

    button.addEventListener('click', () => {
        visible += step;
        cards.forEach((card, index) => {
            if (index < visible) {
                card.classList.remove('d-none');
                card.classList.add('visible');
            }
        });
        if (visible >= cards.length) {
            button.classList.add('d-none');
        }
    });
}

async function shareDeal(button) {
    const title = button.dataset.title || 'DropDeals';
    const url = button.dataset.url || window.location.href;
    const payload = { title, text: `${title} on DropDeals`, url };

    if (navigator.share) {
        try {
            await navigator.share(payload);
            return;
        } catch {
            return;
        }
    }

    try {
        await navigator.clipboard.writeText(url);
        button.classList.add('active');
        const icon = button.querySelector('i');
        if (icon) {
            icon.className = 'fa-solid fa-check';
        }
        setTimeout(() => {
            button.classList.remove('active');
            if (icon) {
                icon.className = 'fa-solid fa-share-nodes';
            }
        }, 1600);
    } catch {
        window.prompt('Copy this deal link:', url);
    }
}

function initShareButtons() {
    document.querySelectorAll('.share-btn').forEach((button) => {
        button.addEventListener('click', (event) => {
            event.preventDefault();
            shareDeal(button);
        });
    });
}

function appendChatMessage(type, html) {
    const body = document.getElementById('chatbot-body');
    if (!body) {
        return;
    }
    const wrapper = document.createElement('div');
    wrapper.className = `chat-msg ${type}`;
    wrapper.innerHTML = html;
    body.appendChild(wrapper);
    body.scrollTop = body.scrollHeight;
}

function renderChatDeals(deals) {
    if (!deals || !deals.length) {
        return '';
    }

    const cards = deals.map((deal) => `
        <a class="chat-deal-link" href="${deal.outbound_url || deal.affiliate_url || `/go/${deal.id}`}" target="_blank" rel="noopener noreferrer">
            <img src="${deal.image_url || ''}" alt="${deal.product_name || 'Deal'}">
            <div>
                <strong>${deal.product_name || 'Deal'}</strong>
                <small>${deal.sale_price ? `₹${Number(deal.sale_price).toLocaleString('en-IN')}` : ''}</small>
            </div>
        </a>
    `).join('');

    return `<div class="chat-deal-suggestions">${cards}</div>`;
}

async function sendChatMessage(message) {
    const input = document.getElementById('chatbot-input');
    if (!message) {
        return;
    }

    appendChatMessage('user', `<p>${message}</p>`);
    if (input) {
        input.value = '';
    }

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message }),
        });
        const data = await response.json();
        appendChatMessage('bot', `<p>${data.reply || 'Here are a few options.'}</p>${renderChatDeals(data.deals || [])}`);
    } catch {
        appendChatMessage('bot', '<p>Chat is unavailable right now. Try again in a moment.</p>');
    }
}

function initChatbot() {
    const fab = document.getElementById('chatbot-fab');
    const panel = document.getElementById('chatbot-panel');
    const close = document.getElementById('chatbot-close');
    const form = document.getElementById('chatbot-form');
    const input = document.getElementById('chatbot-input');

    if (!fab || !panel || !form || !input) {
        return;
    }

    fab.addEventListener('click', () => panel.classList.toggle('d-none'));
    close?.addEventListener('click', () => panel.classList.add('d-none'));

    document.querySelectorAll('.chat-starter').forEach((button) => {
        button.addEventListener('click', () => {
            panel.classList.remove('d-none');
            sendChatMessage(button.dataset.message || '');
        });
    });

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        sendChatMessage(input.value.trim());
    });
}

function initSite() {
    updateWishlistCount();
    initWishlistButtons();
    initRevealCards();
    initLoadMore();
    initShareButtons();
    initChatbot();
}

document.addEventListener('DOMContentLoaded', initSite);
