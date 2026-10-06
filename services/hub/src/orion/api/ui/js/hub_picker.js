/* Discovery locates endpoints; a claimed UUID is not proof of authenticity. */
const HubPicker = {
    connected: false,
    apiPath(path) {
        if (this.self?.local_entry && this.selected && this.selected.id !== this.self.id) {
            return `/api/v1/peers/${this.selected.id}${path.slice('/api/v1'.length)}`;
        }
        return path;
    },
    rememberSuccess() {
        if (this.connected && this.selected) {
            localStorage.setItem('orion_last_hub', this.selected.id);
            localStorage.removeItem('orion_selected_hub');
        }
    },
    saved() {
        return localStorage.getItem('orion_last_hub') || localStorage.getItem('orion_selected_hub');
    },
    message(text = '') {
        this.status.textContent = text;
        this.status.hidden = !text;
    },
    expand(open) {
        this.details.hidden = false;
    },
    async json(path) {
        const response = await fetch(path, {cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(7000)});
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Hub bilgisi alınamadı. Yeniden dene.');
        return data;
    },
    label(record) {
        return record.name.replace(/\s+[—–]\s+Orion.*$/i, '');
    },
    async init({invitation = false} = {}) {
        this.invitation = invitation;
        this.status = document.getElementById('hub-status');
        this.name = document.getElementById('hub-current-name');
        this.list = document.getElementById('hub-list');
        this.details = document.getElementById('hub-picker');
        this.refreshButton = document.getElementById('hub-refresh');
        this.refreshButton.onclick = () => this.refresh({manual: true}).catch(error => this.message(error.message));
        this.list.onchange = () => {
            const record = (this.records || []).find(item => item.id === this.list.value);
            if (record) this.choose(record);
        };
        this.connected = false;
        try {
            this.self = await this.json('/api/v1/discovery/self');
            const expected = new URLSearchParams(location.search).get('hub');
            if (expected && expected !== this.self.id) throw new Error('Bu adreste seçtiğin Hub yok. Giriş yapmadan önce doğru Hub’ı seç.');
            this.records = [{...this.self, url: this.self.local_entry ? '/hub' : '/'}];
            const saved = this.saved();
            if (!this.invitation && !expected && saved && saved !== this.self.id) {
                this.name.textContent = 'Kayıtlı Hub aranıyor…';
                await this.refresh({resume: true});
            } else {
                await this.choose(this.records[0]);
                // The legacy token belongs only to this origin's own Hub.
                if (!expected && /^(localhost|127\.0\.0\.1|\d+\.\d+\.\d+\.\d+)$/.test(location.hostname)) {
                    const previous = localStorage.getItem('orion_token');
                    if (previous && !localStorage.getItem(Auth.tokenKey)) localStorage.setItem(Auth.tokenKey, previous);
                }
            }
        } catch (error) {
            this.connected = false;
            this.name.textContent = 'Bağlantı yok';
            this.message(error.message);
            this.expand(true);
        }
        this.updateButtons();
        this.renderList();
        if (this.self && !this.busy) this.refresh().catch(() => {});
        clearInterval(this.refreshTimer);
        this.refreshTimer = setInterval(() => {
            if (!this.busy && document.visibilityState !== 'hidden' && document.getElementById('auth-modal').classList.contains('show')) {
                this.refresh().catch(() => {});
            }
        }, 10000);
        return this.connected;
    },
    updateButtons() {
        const waiting = Boolean(document.getElementById('auth-error')._authWaitTimer);
        for (const id of ['auth-login-btn', 'auth-register-btn']) document.getElementById(id).disabled = !this.connected || this.authenticating || waiting;
        this.list.disabled = Boolean(this.authenticating || waiting);
    },
    async choose(record) {
        if (this.authenticating) return;
        if (this.invitation && record.id !== this.self.id) {
            this.message('Davet bu Hub’a ait. Başka Hub için onun giriş adresini aç.');
            return;
        }
        if (record.conflict) {
            this.message('Aynı kimliği duyuran birden fazla Hub var. Bu cihaz seçilemiyor.');
            return;
        }
        const revision = this.choiceRevision = (this.choiceRevision || 0) + 1;
        this.connected = false;
        this.updateButtons();
        // Never reuse a password typed for another Hub.
        for (const id of ['auth-username', 'auth-password', 'auth-confirm-password']) {
            const field = document.getElementById(id);
            if (field) field.value = '';
        }
        if (record.id !== this.self.id) {
            const destination = new URL(record.url, location.origin);
            if (destination.protocol !== 'https:' || destination.username || destination.password) return;
            destination.search = ''; destination.hash = '';
            destination.searchParams.set('hub', record.id);
            location.assign(destination.href);
            return;
        }
        try {
            if (record.id !== this.self.id) {
                this.message('Hub’a bağlanılıyor…');
                const claimed = await this.json(`/api/v1/peers/${record.id}/discovery/self`);
                if (revision !== this.choiceRevision) return;
                if (claimed.id !== record.id) throw new Error('Bu adreste seçtiğin Hub yok.');
            }
            this.selected = record;
            Auth.tokenKey = `orion_token:${record.id}`;
            this.connected = true;
            this.name.textContent = this.label(record);
            this.message();
            this.expand(false);
        } catch (error) {
            if (revision !== this.choiceRevision) return;
            this.name.textContent = 'Bağlantı yok';
            this.message(error.message);
            this.expand(true);
        }
        this.renderList();
        this.updateButtons();
    },
    renderList() {
        this.list.replaceChildren();
        const current = this.connected ? this.selected?.id : this.saved();
        if (!(this.records || []).some(record => record.id === current && !record.conflict)) {
            const placeholder = document.createElement('option');
            placeholder.value = ''; placeholder.disabled = true; placeholder.selected = true;
            placeholder.textContent = current ? 'Kayıtlı Hub bulunamadı' : 'Hub seç';
            this.list.append(placeholder);
        }
        for (const record of this.records || []) {
            const button = document.createElement('option');
            button.value = record.id;
            button.textContent = this.label(record);
            if (record.id === this.selected?.id && this.connected) {
                button.selected = true;
            }
            button.disabled = Boolean(record.conflict) || (this.invitation && record.id !== this.self.id);
            button.title = record.conflict ? 'Kimlik çakışması' : record.id;
            this.list.append(button);
        }
    },
    async refresh({resume = !this.connected, manual = false} = {}) {
        if (this.busy) return;
        this.busy = true;
        const started = Date.now();
        const previous = new Set((this.records || []).map(record => record.id));
        this.refreshButton.disabled = true;
        this.refreshButton.classList.add('refreshing');
        this.refreshButton.setAttribute('aria-busy', 'true');
        try {
            if (!this.self) return await this.init({invitation: this.invitation});
            const revision = this.choiceRevision || 0;
            const data = await this.json('/api/v1/discovery/hubs');
            if (revision !== (this.choiceRevision || 0)) resume = false;
            this.records = data.hubs;
            if (!this.records.some(record => record.id === this.self.id)) {
                this.records.push({...this.self, url: this.self.local_entry ? '/hub' : '/'});
            }
            const saved = this.invitation ? null : this.saved();
            if (resume && saved && saved !== this.self.id) {
                const selected = data.hubs.find(record => record.id === saved);
                if (selected) await this.choose(selected);
                else {
                    this.connected = false;
                    this.name.textContent = 'Bulunamadı';
                    this.message('Kayıtlı Hub bulunamadı. Yenile veya başka bir Hub seç.');
                    this.expand(true);
                }
            }
            this.renderList();
            this.updateButtons();
            if (manual && this.connected) {
                const added = this.records.filter(record => !previous.has(record.id));
                this.message(added.length ? `Yeni Hub bulundu: ${added.map(record => this.label(record)).join(', ')}. Listeden seçebilirsiniz.` : 'Liste güncel');
                clearTimeout(this.messageTimer);
                this.messageTimer = setTimeout(() => this.message(), 4000);
            }
        } finally {
            if (manual) await new Promise(resolve => setTimeout(resolve, Math.max(0, 1000 - (Date.now() - started))));
            this.busy = false;
            this.refreshButton.disabled = false;
            this.refreshButton.classList.remove('refreshing');
            this.refreshButton.setAttribute('aria-busy', 'false');
        }
    }
};
