/* Personal provider credentials are submitted to Hub, stored only by Router. */
window.RouterProviders = {
    clearInput() { const input = document.querySelector('#router-provider-card input'); if (input) input.value = ''; },
    reset() { this.clearInput(); document.getElementById('router-provider-card')?.remove(); },
    mount() {
        const host = document.getElementById('settings-panel-general');
        if (!host) return;
        const context = () => Auth.tokenKey + ':' + Auth.getToken();
        const existing = document.getElementById('router-provider-card');
        if (existing?._accountContext === context()) return;
        if (existing) this.reset();
        const en = OrionI18n.language === 'en';
        const card = document.createElement('div');
        card.id = 'router-provider-card'; card.className = 'settings-card';
        card._accountContext = context();
        card.innerHTML = `<div class="settings-card-header"><div><h3>Orion Router</h3></div></div>
            <div class="settings-card-body"><p role="status" data-status></p>
            <p>${en ? 'Your access key is created automatically. Provider keys belong to your account and are stored in Orion Router.' : 'Erişim anahtarınız otomatik oluşturulur. Sağlayıcı anahtarları hesabınıza özeldir ve Orion Router’da saklanır.'}</p>
            <form hidden><div class="setting-row"><label class="setting-label" for="personal-provider">${en ? 'Provider' : 'Sağlayıcı'}</label><div class="setting-control"><select id="personal-provider" class="setting-select" required></select></div></div>
            <div class="setting-row"><label class="setting-label" for="personal-provider-key">${en ? 'Provider API key' : 'Sağlayıcı API anahtarı'}</label><div class="setting-control"><input id="personal-provider-key" class="setting-input" type="password" autocomplete="off" spellcheck="false" maxlength="8192" required></div></div>
            <button class="btn-primary" type="submit">${en ? 'Save provider key' : 'Sağlayıcı anahtarını kaydet'}</button></form>
            <div data-configured></div><p role="alert" data-message></p>
            <button class="btn-secondary" type="button" data-refresh>${en ? 'Refresh connection' : 'Bağlantıyı yenile'}</button></div>`;
        host.append(card);
        const form = card.querySelector('form');
        const selector = form.querySelector('select');
        const input = form.querySelector('input');
        const message = card.querySelector('[data-message]');
        const initialContext = context();
        let routerId;
        const valid = () => card.isConnected && context() === initialContext;
        const request = async (path, options = {}) => {
            const response = await API._fetch('/api/v1/router/providers' + path, options);
            const data = await response.json();
            if (!valid()) throw new Error(en ? 'Hub session changed.' : 'Hub oturumu değişti.');
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : (en ? 'Operation failed.' : 'İşlem tamamlanamadı.'));
            return data;
        };
        const load = async () => {
            const data = await request('');
            routerId = data.router_id;
            card.querySelector('[data-status]').textContent = data.connected ? `${data.router_name} · ${en ? 'Connected' : 'Bağlı'}` : data.message;
            if (data.managed) {
                const row = document.getElementById('setting-input-router_api_key')?.closest('.setting-row');
                if (row) { row.hidden = true; row.style.display = 'none'; }
            }
            form.hidden = !data.connected;
            selector.replaceChildren(...(data.providers || []).filter(p => p !== 'local').map(p => new Option(p, p)));
            form.querySelector('button').disabled = selector.options.length === 0;
            const configured = card.querySelector('[data-configured]'); configured.replaceChildren();
            for (const provider of data.configured || []) {
                const row = document.createElement('p');
                row.append(document.createTextNode(provider + ' · ' + (en ? 'Saved ' : 'Kayıtlı ')));
                const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'btn-secondary'; remove.textContent = en ? 'Remove' : 'Kaldır';
                remove.onclick = async () => {
                    remove.disabled = true; message.textContent = '';
                    try { await request('/' + encodeURIComponent(provider) + '?router_id=' + encodeURIComponent(routerId), {method: 'DELETE'}); await load(); }
                    catch (error) { if (valid()) message.textContent = error.message; }
                    finally { remove.disabled = false; }
                };
                row.append(remove); configured.append(row);
            }
        };
        form.onsubmit = async event => {
            event.preventDefault();
            const key = input.value.trim(); input.value = '';
            if (!key || !valid()) return;
            const button = form.querySelector('button'); button.disabled = true; message.textContent = '';
            try {
                await request('/' + encodeURIComponent(selector.value), {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({api_key: key, router_id: routerId})});
                await load();
                message.textContent = en ? 'Key saved. Select a model from the Router catalog.' : 'Anahtar kaydedildi. Router’ın model listesinden model seçebilirsiniz.';
                const refresh = UI.loadModelChoices?.(UI.currentSettings, true);
                refresh?.catch(() => {});
            } catch (error) { if (valid()) message.textContent = error.message; }
            finally { if (valid()) button.disabled = selector.options.length === 0; }
        };
        card.querySelector('[data-refresh]').onclick = async event => {
            event.target.disabled = true; message.textContent = '';
            try { await load(); }
            catch (error) { if (valid()) message.textContent = error.message; }
            finally { if (valid()) event.target.disabled = false; }
        };
        load().catch(error => { if (valid()) message.textContent = error.message; });
    },
};
