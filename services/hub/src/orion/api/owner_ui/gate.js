window.OwnerGate = { unlocked: false, lock() { OwnerAPI.clearSession(); location.reload(); } };
document.addEventListener('DOMContentLoaded', () => {
    const scope = document.body.dataset.ownerScope;
    const gate = document.createElement('section');
    gate.className = 'gate';
    gate.hidden = true;
    gate.innerHTML = `<a class="brand" href="${OwnerAPI.url('/')}">✦ <span>ORION</span></a><h1>${document.body.dataset.ownerTitle || (scope === 'people' ? 'Kişiler ve davetler' : 'Gelişmiş yönetim')}</h1><p class="hint">Devam etmek için yönetici şifreni gir. Bu sekmedeki yönetim oturumu 15 dakika geçerlidir.</p><form><label>Yönetici şifresi<input type="password" autocomplete="current-password" required maxlength="512"></label><button type="submit">Yönetimi aç</button></form><p class="hint" data-default-password hidden></p><p class="notice" role="alert" hidden></p>`;
    document.body.appendChild(gate);
    const input = gate.querySelector('input'), button = gate.querySelector('button'), error = gate.querySelector('[role="alert"]');
    async function showDefaultPassword() {
        try {
            const response = await OwnerAPI.fetch('/api/v1/owner/secure/password-info', {cache: 'no-store'});
            if (!response.ok || OwnerGate.unlocked) return;
            const info = await response.json();
            if (info.default_password === 'orion' && !OwnerGate.unlocked) {
                const hint = gate.querySelector('[data-default-password]');
                hint.textContent = 'Varsayılan yönetici şifresi: orion';
                hint.hidden = false;
            }
        } catch (_) { /* A failed check must never claim the default is active. */ }
    }
    function unlock() {
        gate.remove();
        document.body.classList.remove('owner-locked');
        OwnerGate.unlocked = true;
        const session = OwnerAPI.session();
        if (session) setTimeout(() => OwnerGate.lock(), Math.max(0, session.expires - Date.now()));
        document.dispatchEvent(new Event('owner-unlocked'));
    }
    // Detect a sessionStorage copy when a new tab is opened or duplicated.
    const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(OwnerAPI.sessionKey) : null;
    const tabId = crypto.randomUUID();
    channel?.addEventListener('message', event => {
        if (event.data.type === 'claim' && OwnerGate.unlocked && event.data.token === OwnerAPI.session()?.token) {
            channel.postMessage({type: 'in-use', target: event.data.tabId});
        }
        if (event.data.type === 'in-use' && event.data.target === tabId) OwnerAPI.clearSession();
    });
    async function restore() {
        const saved = OwnerAPI.session();
        if (!saved) return;
        channel?.postMessage({type: 'claim', tabId, token: saved.token});
        await new Promise(resolve => setTimeout(resolve, 150));
        if (!OwnerAPI.session()) return;
        try {
            const response = await OwnerAPI.fetch('/api/v1/owner/session');
            const data = response.ok ? await response.json() : null;
            if (data?.scope === scope) unlock();
            else OwnerAPI.clearSession();
        } catch (_) { OwnerAPI.clearSession(); }
    }
    restore().then(() => {
        if (!OwnerGate.unlocked) {
            gate.hidden = false;
            input.focus();
            void showDefaultPassword();
        }
    });
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && OwnerGate.unlocked && !OwnerAPI.session()) OwnerGate.lock();
    });
    gate.querySelector('form').addEventListener('submit', async event => {
        event.preventDefault();
        button.disabled = true;
        try {
            const response = await OwnerAPI.fetch('/api/v1/owner/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ password: input.value, scope, tab_session: true }) });
            if (response.status === 429) {
                AuthWait.start(error, [button], AuthWait.retrySeconds(response));
                return;
            }
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Yönetim açılamadı.');
            input.value = '';
            sessionStorage.setItem(OwnerAPI.sessionKey, JSON.stringify({token: data.token, expires: Date.now() + data.expires_in * 1000}));
            unlock();
        } catch (failure) {
            error.hidden = false;
            error.textContent = failure.message;
        } finally {
            if (!error._authWaitTimer) button.disabled = false;
        }
    });
    document.getElementById('owner-logout')?.addEventListener('click', async () => {
        const response = await OwnerAPI.fetch('/api/v1/owner/logout', {method: 'POST'});
        if (response.ok) OwnerGate.lock();
    });
});
