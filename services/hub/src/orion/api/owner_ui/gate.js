window.OwnerGate = { unlocked: false, lock() { location.reload(); } };
document.addEventListener('DOMContentLoaded', () => {
    const scope = document.body.dataset.ownerScope;
    const gate = document.createElement('section');
    gate.className = 'gate';
    gate.innerHTML = `<a class="brand" href="/">✦ <span>ORION</span></a><h1>${scope === 'people' ? 'Kişiler ve davetler' : 'Gelişmiş yönetim'}</h1><p class="hint">Devam etmek için yönetici şifreni gir. İki yönetim ekranında da aynı şifre kullanılır.</p><form><label>Yönetici şifresi<input type="password" autocomplete="current-password" required maxlength="512" autofocus></label><button type="submit">Yönetimi aç</button></form><p class="notice" role="alert" hidden></p><p><a href="/">← Başlangıca dön</a></p>`;
    document.body.appendChild(gate);
    const input = gate.querySelector('input'), button = gate.querySelector('button'), error = gate.querySelector('[role="alert"]');
    // Always ask again when entering either management screen; no saved administrator key.
    gate.querySelector('form').addEventListener('submit', async event => {
        event.preventDefault();
        button.disabled = true;
        try {
            const response = await fetch('/api/v1/owner/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ password: input.value, scope }) });
            if (response.status === 429) {
                AuthWait.start(error, [button], AuthWait.retrySeconds(response));
                return;
            }
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Yönetim açılamadı.');
            input.value = '';
            gate.remove();
            document.body.classList.remove('owner-locked');
            OwnerGate.unlocked = true;
            document.dispatchEvent(new Event('owner-unlocked'));
        } catch (failure) {
            error.hidden = false;
            error.textContent = failure.message;
        } finally {
            if (!error._authWaitTimer) button.disabled = false;
        }
    });
    document.getElementById('owner-logout')?.addEventListener('click', async () => {
        const response = await fetch('/api/v1/owner/logout', {method: 'POST'});
        if (response.ok) location.reload();
    });
    input.focus();
});
