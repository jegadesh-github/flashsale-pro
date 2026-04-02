/* ========================================
   💎 ELITE DROPS - COMMAND ENGINE 2026
   ======================================== */

document.addEventListener('DOMContentLoaded', () => {
    initTimers();
    updateWishlistUI();
    syncWishlistStates();
});

// 1. 🕒 LIVE SYSTEM COUNTDOWN
function initTimers() {
    setInterval(() => {
        const timers = document.querySelectorAll('.timer-text');
        timers.forEach(timer => {
            let timeArray = timer.innerText.split(':');
            let totalSeconds = (+timeArray[0]) * 3600 + (+timeArray[1]) * 60 + (+timeArray[2]);

            if (totalSeconds > 0) {
                totalSeconds--;
                let h = Math.floor(totalSeconds / 3600).toString().padStart(2, '0');
                let m = Math.floor((totalSeconds % 3600) / 60).toString().padStart(2, '0');
                let s = (totalSeconds % 60).toString().padStart(2, '0');
                timer.innerText = `${h}:${m}:${s}`;
            } else {
                timer.closest('.deal-card-compact').style.opacity = '0.5';
                timer.innerText = "EXPIRED";
            }
        });
    }, 1000);
}

// 2. ❤️ WISHLIST LOGIC (LocalStorage)
let wishlist = JSON.parse(localStorage.getItem('fspro_wishlist')) || [];

function handleWishlist(btn, id) {
    const index = wishlist.indexOf(id.toString());
    
    if (index === -1) {
        wishlist.push(id.toString());
        btn.classList.add('active');
        btn.innerHTML = '<i class="fa-solid fa-heart-circle-check text-danger"></i>';
    } else {
        wishlist.splice(index, 1);
        btn.classList.remove('active');
        btn.innerHTML = '<i class="fa-solid fa-heart"></i>';
    }

    localStorage.setItem('fspro_wishlist', JSON.stringify(wishlist));
    updateWishlistUI();
}

function updateWishlistUI() {
    const countBadge = document.getElementById('wishlist-count');
    if (countBadge) countBadge.innerText = wishlist.length;
}

// Ensures icons stay red if already in wishlist on page load
function syncWishlistStates() {
    const wishlistButtons = document.querySelectorAll('.wishlist-btn-mini');
    wishlistButtons.forEach(btn => {
        const id = btn.getAttribute('data-id');
        if (wishlist.includes(id)) {
            btn.classList.add('active');
            btn.innerHTML = '<i class="fa-solid fa-heart-circle-check text-danger"></i>';
        }
    });
}

// 🟢 WHATSAPP SMART SHARE
async function shareDeal(title, url) {
    const fullUrl = window.location.origin + url;
    const shareText = `🔥 Check this ELITE DROP: ${title} \n\n🔗 ${fullUrl}`;
    window.open(`https://wa.me/?text=${encodeURIComponent(shareText)}`, '_blank');
}
