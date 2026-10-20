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
        async fetch(path, options = {}) {
            const headers = new Headers(options.headers);
            const session = this.session();
            if (session) headers.set('X-Orion-Owner-Session', session.token);
            const response = await window.fetch(this.url(path), {...options, headers});
            if (session && response?.status !== 401 && this.session()?.token === session.token) {
                sessionStorage.setItem(this.sessionKey, JSON.stringify({...session, expires: Date.now() + this.idleSeconds * 1000}));
            }
            return response;
        }
    };
})();
