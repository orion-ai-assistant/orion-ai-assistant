document.addEventListener('DOMContentLoaded', () => {
    const current = document.getElementById('router-current');
    const error = document.getElementById('router-error');
    const list = document.getElementById('router-list');
    const refresh = document.getElementById('router-refresh');
    const manual = document.getElementById('router-manual');
    const access = document.getElementById('router-access');
    const password = document.getElementById('router-password');
    let currentRouterId;
    let discoveredRouters = null;
    async function loadStatus() {
        const data = await request('');
        show(data.selected);
        currentRouterId = data.selected?.id;
        if (discoveredRouters !== null) renderRouters();
        document.getElementById('router-access-status').textContent = data.message;
        access.hidden = !data.selected || data.access_ready;
    }
    async function request(path, options) {
        const response = await OwnerAPI.fetch('/api/v1/owner/router' + path, options);
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Router bağlantısı kontrol edilemedi.');
        return data;
    }
    const show = selected => {
        if (!selected) { current.textContent = 'Bu Hub’ın model bağlantısı henüz kurulmamış. Router seçerek bağlan.'; return; }
        current.replaceChildren();
        current.className = 'connection-status';
        const dot = document.createElement('span'); dot.className = 'status-dot';
        const text = document.createElement('span');
        const name = document.createElement('span'); name.className = 'router-current-name'; name.textContent = selected.name;
        const address = document.createElement('span'); address.className = 'router-current-url'; address.textContent = selected.url || 'Bağlantı bekleniyor';
        text.append(name, address); current.append(dot, text); current.title = selected.id || '';
    };
    async function operation(work) {
        refresh.disabled = true;
        manual.querySelector('button').disabled = true;
        access.querySelector('button').disabled = true;
        error.hidden = true;
        try { await work(); }
        catch (failure) { error.textContent = failure.message; error.hidden = false; }
        finally { refresh.disabled = false; manual.querySelector('button').disabled = false; access.querySelector('button').disabled = false; }
    }
    async function save(selection) {
        const fp = document.getElementById('router-fp').value.trim();
        show((await request('', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...selection, ...(fp ? {fp} : {})})})).selected);
        await loadStatus();
    }
    function renderRouters() {
        list.replaceChildren();
        const available = discoveredRouters.filter(router => router.id !== currentRouterId);
        for (const router of available) {
            const card = document.createElement('article'); card.className = 'router-card';
            const details = document.createElement('div'); details.className = 'router-details';
            const title = document.createElement('h3'); title.textContent = router.name;
            const address = document.createElement('p'); address.textContent = router.url;
            const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = router.same_pc ? 'Bu bilgisayar · otomatik doğrulama' : 'Ağda bulundu';
            details.append(title, address, badge);
            const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Bağlan';
            button.disabled = Boolean(router.conflict);
            button.onclick = () => operation(async () => { button.disabled = true; button.textContent = 'Bağlanıyor…'; try { await save({id: router.id}); button.textContent = 'Bağlandı'; } finally { button.disabled = false; if (button.textContent !== 'Bağlandı') button.textContent = 'Bağlan'; } });
            card.append(details, button); list.append(card);
        }
        if (!available.length) list.textContent = currentRouterId
            ? 'Bağlı Router dışında başka Router bulunamadı.'
            : 'Router bulunamadı. Aynı ağda açık olduğundan emin ol veya elle adres yaz.';
    }
    refresh.onclick = () => operation(async () => {
        await loadStatus();
        const data = await request('/discover');
        discoveredRouters = data.routers;
        renderRouters();
    });
    manual.onsubmit = event => {
        event.preventDefault();
        return operation(() => save({manual_url: document.getElementById('router-address').value.trim(), manual_id: document.getElementById('router-manual-id').value.trim()}));
    };
    document.addEventListener('owner-unlocked', async () => {
        await operation(loadStatus);
        refresh.click();
    });
    access.onsubmit = event => {
        event.preventDefault();
        operation(async () => {
            const secret = password.value;
            password.value = '';
            await request('/access', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password: secret, router_id: currentRouterId})});
            await loadStatus();
        });
    };
});
