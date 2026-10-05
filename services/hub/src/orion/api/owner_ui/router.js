document.addEventListener('DOMContentLoaded', () => {
    const current = document.getElementById('router-current');
    const error = document.getElementById('router-error');
    const list = document.getElementById('router-list');
    const refresh = document.getElementById('router-refresh');
    const manual = document.getElementById('router-manual');
    async function request(path, options) {
        const response = await OwnerAPI.fetch('/api/v1/owner/router' + path, options);
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Router bağlantısı kontrol edilemedi.');
        return data;
    }
    const show = selected => {
        if (!selected) { current.textContent = 'Mevcut Router ayarları kullanılıyor. Yeni Router seçmek için ağda ara veya elle adres yaz.'; return; }
        current.textContent = selected.name + (selected.url ? ` · ${selected.url} · Kayıtlı` : ' · Bulunamadı');
        current.title = selected.id || '';
    };
    async function operation(work) {
        refresh.disabled = true;
        manual.querySelector('button').disabled = true;
        error.hidden = true;
        try { await work(); }
        catch (failure) { error.textContent = failure.message; error.hidden = false; }
        finally { refresh.disabled = false; manual.querySelector('button').disabled = false; }
    }
    async function save(selection) {
        const fp = document.getElementById('router-fp').value.trim();
        show((await request('', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...selection, ...(fp ? {fp} : {})})})).selected);
    }
    refresh.onclick = () => operation(async () => {
        const data = await request('/discover');
        list.replaceChildren();
        for (const router of data.routers) {
            const button = document.createElement('button'); button.type = 'button';
            button.textContent = `${router.name} · ${router.url}`;
            button.disabled = Boolean(router.conflict);
            button.onclick = () => operation(() => save({id: router.id}));
            list.append(button);
        }
        if (!data.routers.length) list.textContent = 'Router bulunamadı. Aynı ağda açık olduğundan emin ol veya elle adres yaz.';
    });
    manual.onsubmit = event => {
        event.preventDefault();
        return operation(() => save({manual_url: document.getElementById('router-address').value.trim(), manual_id: document.getElementById('router-manual-id').value.trim()}));
    };
    document.addEventListener('owner-unlocked', () => operation(async () => show((await request('')).selected)));
});
