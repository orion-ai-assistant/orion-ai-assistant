/* Shared login countdown: server expiry, not timer tick count. */
window.AuthWait = {
    retrySeconds(response) {
        const raw = response.headers.get('Retry-After');
        const seconds = Number(raw);
        if (raw && Number.isFinite(seconds) && seconds > 0) return seconds;
        const date = Date.parse(raw);
        return Number.isFinite(date) ? Math.max(1, Math.ceil((date - Date.now()) / 1000)) : 30;
    },
    start(element, buttons, seconds) {
        this.stop(element);
        const deadline = Date.now() + seconds * 1000;
        buttons.forEach(button => { button.disabled = true; });
        element.hidden = false;
        element.style.display = 'block';
        const update = () => {
            const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
            element.textContent = remaining ? `Çok fazla deneme. ${remaining} saniye sonra tekrar deneyebilirsin.` : 'Bekleme süresi doldu. Tekrar deneyebilirsin.';
            if (!remaining) {
                this.stop(element);
                buttons.forEach(button => { button.disabled = false; });
            }
        };
        element._authWaitTimer = setInterval(update, 250);
        update();
    },
    stop(element) {
        if (element._authWaitTimer) clearInterval(element._authWaitTimer);
        delete element._authWaitTimer;
    },
};
