function updateTimers() {
    const timers = document.querySelectorAll('.timer');

    timers.forEach(timer => {
        const expiryDate = new Date(timer.getAttribute('data-expiry')).getTime();
        const now = new Date().getTime();
        const timeLeft = expiryDate - now;

        if (timeLeft <= 0) {
            timer.innerHTML = "EXPIRED";
            timer.parentElement.parentElement.classList.add('bg-secondary');
            return;
        }

        const hours = Math.floor((timeLeft % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
        const minutes = Math.floor((timeLeft % (1000 * 60 * 60)) / (1000 * 60));
        const seconds = Math.floor((timeLeft % (1000 * 60)) / 1000);

        timer.innerHTML = `${hours}h ${minutes}m ${seconds}s`;
    });
}

// Run the clock every 1 second
setInterval(updateTimers, 1000);
updateTimers();