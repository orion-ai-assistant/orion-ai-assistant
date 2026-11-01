/* Same management UI works at /panel and on the direct owner listener. */
(() => {
    const source = new URL(document.currentScript.src);
    const base = source.pathname.slice(0, source.pathname.lastIndexOf('/owner-assets/'));
    window.OwnerAPI = {
        base,
        url(path) { return path.startsWith('/') && !path.startsWith('//') ? base + path : path; },
        sessionKey: 'orion_owner_tab:' + base,
        session() {
            try {
                const record = JSON.parse(sessionStorage.getItem(this.sessionKey) || 'null');
                return record?.expires > Date.now() ? record : null;
            } catch (_) { return null; }
        },
        clearSession() { sessionStorage.removeItem(this.sessionKey); },
        // The Hub restarts the idle limit on every request the session authenticates.
        idleSeconds: 15 * 60,
        // background: an automatic refresh, not the owner's activity; it never postpones the idle lock.
        async fetch(path, {background = false, ...options} = {}) {
            const headers = new Headers(options.headers);
            const session = this.session();
            if (session) headers.set('X-Orion-Owner-Session', session.token);
            if (background) headers.set('X-Orion-Background-Refresh', '1');
            const response = await window.fetch(this.url(path), {...options, headers});
            if (!background && session && response?.status !== 401 && this.session()?.token === session.token) {
                sessionStorage.setItem(this.sessionKey, JSON.stringify({...session, expires: Date.now() + this.idleSeconds * 1000}));
            }
            return response;
        },
        // Keeps a page current while it is on screen; a hidden or closed tab makes no requests.
        whileVisible(update, interval = 5000) {
            let running = false;
            const tick = async () => {
                if (document.hidden || !window.OwnerGate?.unlocked || running) return;
                running = true;
                try { await update(); } catch (_) { /* The next tick or a manual refresh shows errors. */ }
                finally { running = false; }
            };
            let timer = setInterval(tick, interval);
            document.addEventListener('visibilitychange', tick);
            window.addEventListener('focus', tick);
            window.addEventListener('pagehide', () => { clearInterval(timer); timer = null; });
            // Back/Forward restores a page from the browser's cache without loading it again.
            window.addEventListener('pageshow', event => {
                if (!event.persisted || timer !== null) return;
                timer = setInterval(tick, interval);
                tick();
            });
        }
    };
})();
