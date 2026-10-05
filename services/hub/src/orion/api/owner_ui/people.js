document.addEventListener('DOMContentLoaded', () => {
    const error = document.getElementById('people-error');
    const showError = failure => { error.hidden = false; error.textContent = failure.message; };
    const api = async (path, options={}) => {
        const response = await OwnerAPI.fetch('/api/v1/owner' + path, { ...options, headers: {'Content-Type': 'application/json'} });
        if (response.status === 401) { OwnerGate.lock(); throw new Error('Yönetim oturumun sona erdi.'); }
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'İşlem tamamlanamadı. Sunucu adresini ve alanları kontrol et.');
        return data;
    };
    const date = value => new Date(value).toLocaleString('tr-TR');
    const renderRecord = (container, title, description, action, callback) => {
        const row = document.createElement('div'); row.className = 'record';
        const text = document.createElement('div'), name = document.createElement('strong'), detail = document.createElement('p');
        name.textContent = title; detail.textContent = description; detail.className = 'hint'; text.append(name, detail); row.append(text);
        if (action) {
            const button = document.createElement('button'); button.className = 'secondary'; button.textContent = action;
            button.onclick = async () => { button.disabled = true; try { await callback(); await refresh(); } catch (failure) { showError(failure); } finally { button.disabled = false; } };
            row.append(button);
        }
        container.append(row);
    };
    let refreshing = false;
    const refresh = async () => {
        if (refreshing || !OwnerGate.unlocked) return;
        refreshing = true;
        try {
        error.hidden = true;
        const data = await api('/people');
        const address = document.getElementById('public-url');
        address.textContent = 'Davet adresi: ' + data.public_url;
        const users = document.getElementById('people-list'); users.replaceChildren();
        if (!data.users.length) users.textContent = 'Henüz kullanıcı yok. İlk kişiyi davet ederek başlayabilirsin.';
        data.users.forEach(user => renderRecord(users, user.id, `${user.is_active ? 'Aktif' : 'Onay bekliyor veya erişimi kapalı'} · ${date(user.created_at)}`, user.is_active ? 'Erişimi kapat' : 'Erişim ver', async () => {
            if (user.is_active && !confirm(`${user.id} kullanıcısının erişimi kapatılsın mı?`)) return;
            await api('/people/' + encodeURIComponent(user.id), {method: 'PUT', body: JSON.stringify({is_active: !user.is_active})});
        }));
        const invites = document.getElementById('invitation-list'); invites.replaceChildren();
        if (!data.invitations.length) invites.textContent = 'Henüz davet oluşturmadın.';
        data.invitations.forEach(invite => {
            const available = !invite.revoked && !invite.used_by && new Date(invite.expires_at) > new Date();
            const status = invite.used_by ? `${invite.used_by} tarafından kullanıldı` : invite.revoked ? 'İptal edildi' : available ? 'Kullanılabilir' : 'Süresi doldu';
            renderRecord(invites, invite.label || 'Davet', `${status} · Son geçerlilik: ${date(invite.expires_at)}`, available ? 'İptal et' : null, () => api('/invitations/' + invite.id, {method: 'DELETE'}));
        });
        } finally { refreshing = false; }
    };
    document.addEventListener('owner-unlocked', () => refresh().catch(showError));
    // An invitation can be accepted on another device or in another tab.
    const updateWhenVisible = () => {
        if (!document.hidden && OwnerGate.unlocked) refresh().catch(showError);
    };
    const timer = setInterval(updateWhenVisible, 5000);
    document.addEventListener('visibilitychange', updateWhenVisible);
    window.addEventListener('focus', updateWhenVisible);
    window.addEventListener('pagehide', () => clearInterval(timer), {once: true});
    document.getElementById('refresh-people').onclick = () => refresh().catch(showError);
    document.getElementById('invite-form').onsubmit = async event => {
        event.preventDefault(); error.hidden = true;
        const button = document.getElementById('invite-submit'); button.disabled = true;
        try {
            const data = await api('/invitations', {method: 'POST', body: JSON.stringify({label: document.getElementById('invite-label').value, expires_minutes: Number(document.getElementById('invite-duration').value)})});
            document.getElementById('invite-link').value = data.url;
            const svg = new Blob([data.qr_svg], {type: 'image/svg+xml'}), image = document.createElement('img');
            image.alt = 'Davet bağlantısı QR kodu'; image.width = image.height = 230;
            image.src = URL.createObjectURL(svg); image.onload = () => URL.revokeObjectURL(image.src);
            document.getElementById('invite-qr').replaceChildren(image);
            document.getElementById('invite-result').hidden = false;
            document.getElementById('invite-feedback').textContent = `Son geçerlilik: ${date(data.expires_at)}`;
            await refresh();
        } catch (failure) { showError(failure); } finally { button.disabled = false; }
    };
    document.getElementById('copy-invite').onclick = async () => {
        const link = document.getElementById('invite-link');
        try { await navigator.clipboard.writeText(link.value); document.getElementById('invite-feedback').textContent = 'Bağlantı kopyalandı.'; }
        catch (_) { link.focus(); link.select(); document.getElementById('invite-feedback').textContent = 'Bağlantıyı seçtim. Kopyalayıp gönderebilirsin.'; }
    };
    document.getElementById('share-invite').onclick = async () => {
        if (!navigator.share) return document.getElementById('copy-invite').click();
        try { await navigator.share({title: 'Orion daveti', url: document.getElementById('invite-link').value}); } catch (_) { /* share cancellation is harmless */ }
    };
});
