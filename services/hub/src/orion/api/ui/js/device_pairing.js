/* The local owner password approves one QR; account/device credentials stay separate. */
const DevicePairing = {
    source: null, timer: null, epoch: 0, pending: new Set(),
    available() {
        return ['localhost', '127.0.0.1', '[::1]', '::1'].includes(location.hostname)
            && HubPicker.self?.local_entry === true && HubPicker.connected && HubPicker.selected?.id === HubPicker.self.id;
    },
    start() {
        this.stop();
        const available = this.available();
        const panel = document.getElementById("pair-device-panel"); if (panel) panel.hidden = !available;
        ['pair-device-settings', 'owner-password-settings'].forEach(id => {
            const button = document.getElementById(id);
            if (!button) return;
            button.hidden = !available;
            button.onclick = () => this.open(null, id === 'owner-password-settings');
        });
        if (!available || !Auth.getToken()) return;
        this.source = new EventSource('/api/v1/pair/requests/stream?token=' + encodeURIComponent(Auth.getToken()));
        this.source.addEventListener('pairing_completed', event => {
            let data; try { data = JSON.parse(event.data); } catch { return; }
            if (data.hub_id === HubPicker.self.id && !this.passwordMode && this.dialog?.open) {
                this.request = null; this.dialog.close();
            }
        });
        this.source.addEventListener('pairing_request', event => {
            let request; try { request = JSON.parse(event.data); } catch { return; }
            const expires = typeof request.expires_at === 'number' ? request.expires_at * 1000 : Date.parse(request.expires_at);
            if (!request.request_id || request.hub_id !== HubPicker.self.id || this.pending.has(request.request_id)
                || !Number.isFinite(expires) || expires <= Date.now()) return;
            this.pending.add(request.request_id);
            if (this.pending.size > 100) this.pending.delete(this.pending.values().next().value);
            if (!this.dialog?.open) this.open(request);
        });
    },
    stop() {
        const panel = document.getElementById("pair-device-panel"); if (panel) panel.hidden = true;
        this.epoch++; this.source?.close(); this.source = null; this.request = null;
        this.clearQr(); this.dialog?.close(); this.pending.clear();
        ['pair-device-settings', 'owner-password-settings'].forEach(id => {
            const button = document.getElementById(id); if (button) button.hidden = true;
        });
    },
    clearQr() {
        clearInterval(this.timer); this.timer = null; this.pairingId = null;
        if (!this.dialog) return;
        this.dialog.querySelector('[data-qr]').removeAttribute('src');
        this.dialog.querySelector('[data-result]').hidden = true;
        this.link = null;
        this.dialog.querySelector('[data-password]').value = '';
        this.dialog.querySelector('[data-password-change]')?.reset?.();
    },
    createDialog() {
        const dialog = document.createElement('dialog'); dialog.className = 'device-pair-dialog';
        dialog.innerHTML = `<form method="dialog"><button class="device-pair-close" aria-label="Kapat">×</button></form>
            <h2 data-title>Telefon / cihaz bağla</h2><p data-description></p><p data-error role="alert" hidden></p>
            <form data-owner><label>Yönetici şifresi<input data-password type="password" autocomplete="current-password" required maxlength="512"></label>
                <p data-default hidden>Varsayılan şifre: <strong>orion</strong>. Hub ayarlarından değiştirebilirsin.</p>
                <button class="btn btn-primary" type="submit">Devam et</button></form>
            <form data-password-change hidden><label>Mevcut şifre<input name="current" type="password" autocomplete="current-password" required></label>
                <label>Yeni şifre<input name="next" type="password" autocomplete="new-password" minlength="4" maxlength="72" required></label>
                <button class="btn btn-primary">Şifreyi değiştir</button></form>
            <button data-generate type="button" class="btn btn-primary" hidden>QR yeniden oluştur</button>
            <div data-result hidden><img data-qr alt="Telefon bağlantısı QR kodu"><p data-expiry role="status"></p>
                <p>Bağlantıyı kopyalayıp aynı ağdaki telefonunuzda veya bilgisayarınızda açın.</p><button data-copy type="button" class="btn btn-secondary">Bağlantıyı kopyala</button>
                <p>Aynı ağdaki telefondan okut. Tek QR cihazı eşleştirir; mevcut hesabınla girebilir veya yeni hesap açabilirsin.</p></div>`;
        document.body.appendChild(dialog);
        dialog.addEventListener('close', () => {
            const request = this.request; this.request = null; this.epoch++; this.clearQr();
            dialog.querySelector('[data-password-change]').reset();
            if (request && this.available()) fetch('/api/v1/pair/requests/' + encodeURIComponent(request.request_id) + '/reject', {
                method: 'POST', headers: {'Authorization': 'Bearer ' + Auth.getToken()}
            }).catch(() => {});
        });
        dialog.querySelector('[data-owner]').onsubmit = event => {
            event.preventDefault(); const epoch = this.epoch;
            const input = dialog.querySelector('[data-password]'); const password = input.value; input.value = '';
            this.perform(async () => {
                await this.owner('/login', {password, scope: 'admin'});
                if (epoch !== this.epoch) return;
                dialog.querySelector('[data-owner]').hidden = true;
                if (this.passwordMode) dialog.querySelector('[data-password-change]').hidden = false;
                else await this.generate(epoch);
            });
        };
        dialog.querySelector('[data-password-change]').onsubmit = event => {
            event.preventDefault(); const form = event.currentTarget;
            const body = {current_password: form.elements.current.value, new_password: form.elements.next.value}; form.reset();
            this.perform(async () => {
                await this.owner('/login', {password: body.current_password, scope: 'admin'});
                await this.owner('/secure/password', body); this.request = null; dialog.close();
            });
        };
        dialog.querySelector('[data-generate]').onclick = () => this.perform(() => this.generate(this.epoch));
        dialog.querySelector('[data-copy]').onclick = async () => {
            const link = this.link;
            try { await navigator.clipboard.writeText(link); }
            catch { const input = document.createElement('textarea'); input.value = link; dialog.appendChild(input); input.select(); document.execCommand('copy'); input.remove(); }
        };
        this.dialog = dialog;
    },
    async owner(path, body) {
        if (!this.available()) throw new Error('Bu işlem Hub’ın kurulu olduğu PC üzerinden yapılabilir.');
        const response = await fetch('/panel/api/v1/owner' + path, {
            method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
            headers: body === undefined ? {} : {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)
        });
        const data = await response.json();
        if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : 'İşlem tamamlanamadı.'); error.status = response.status; throw error; }
        return data;
    },
    async perform(action) {
        if (this.busy) return; this.busy = true; const epoch = this.epoch;
        const buttons = [...this.dialog.querySelectorAll('button:not(.device-pair-close)')]; buttons.forEach(button => button.disabled = true);
        this.dialog.querySelector('[data-error]').hidden = true;
        try { await action(); }
        catch (error) {
            if (epoch !== this.epoch) return;
            this.dialog.querySelector('[data-error]').textContent = error.message; this.dialog.querySelector('[data-error]').hidden = false;
            if (error.status === 401) { this.dialog.querySelector('[data-owner]').hidden = false; this.clearQr(); }
        } finally { this.busy = false; buttons.forEach(button => button.disabled = false); }
    },
    async open(request = null, passwordMode = false) {
        if (!this.available()) return; if (!this.dialog) this.createDialog();
        this.epoch++; const epoch = this.epoch; this.request = request; this.passwordMode = passwordMode; this.usedDefault = false; this.clearQr();
        this.dialog.querySelector('[data-error]').hidden = true;
        this.dialog.querySelector('[data-owner]').hidden = true; this.dialog.querySelector('[data-generate]').hidden = true;
        this.dialog.querySelector('[data-password-change]').hidden = true;
        this.dialog.querySelector('[data-title]').textContent = passwordMode ? 'Yönetici şifresini değiştir' : 'Telefon / cihaz bağla';
        this.dialog.querySelector('[data-description]').textContent = passwordMode ? 'Yeni şifre tüm yönetim oturumlarını kapatır.'
            : request ? `${String(request.device_name || 'Yeni cihaz').slice(0, 120)} eşleşmek istiyor. Tanıdığın cihazsa QR’ı telefonla okut.`
            : 'Telefondan QR’ı okut veya bağlantıyla Orion uygulamasını aç.';
        if (!this.dialog.open) this.dialog.showModal();
        this.dialog.querySelector('[data-default]').hidden = true;
        if (passwordMode) { this.dialog.querySelector('[data-password-change]').hidden = false; return; }
        await this.perform(async () => {
            try { await this.owner('/secure/addresses'); }
            catch (error) {
                if (error.status !== 401) throw error;
                const info = await this.owner('/secure/password-info');
                if (info.default_password !== 'orion') throw error;
                await this.owner('/login', {password: 'orion', scope: 'admin'});
                if (epoch === this.epoch) this.usedDefault = true;
            }
            if (epoch !== this.epoch) return;
            if (passwordMode) this.dialog.querySelector('[data-password-change]').hidden = false;
            else await this.generate(epoch);
        });
    },
    async generate(epoch) {
        this.clearQr(); const data = await this.owner('/secure/pairing', this.request ? {request_id: this.request.request_id} : {});
        if (epoch !== this.epoch || !this.dialog.open) return;
        if (!data.qr_png || !data.link?.startsWith('https://')) throw new Error('QR oluşturulamadı.');
        this.pairingId = data.pairing_id;
        this.dialog.querySelector('[data-qr]').src = 'data:image/png;base64,' + data.qr_png;
        this.link = data.link; this.dialog.querySelector('[data-result]').hidden = false;
        const defaultNotice = this.dialog.querySelector('[data-default]');
        defaultNotice.hidden = !this.usedDefault;
        defaultNotice.textContent = 'Şifre orion olduğu için QR otomatik açıldı. Hub ayarlarından yönetici şifreni değiştirebilirsin.';
        this.dialog.querySelector('[data-result]').appendChild(defaultNotice);
        const expires = Date.now() + Number(data.expires_in || 120) * 1000;
        const tick = () => {
            const remaining = Math.max(0, Math.ceil((expires - Date.now()) / 1000));
            this.dialog.querySelector('[data-expiry]').textContent = `QR ${remaining} saniye geçerli.`;
            if (!remaining) { this.clearQr(); this.dialog.querySelector('[data-generate]').hidden = false; }
        };
        tick(); this.timer = setInterval(tick, 1000);
    }
};
