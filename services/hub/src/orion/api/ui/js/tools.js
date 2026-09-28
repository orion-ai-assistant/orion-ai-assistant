/* Server-owned tool catalog; the dashboard only selects registered tools. */
const ToolsUI = {
    draft: null,
    revision: 0,
    resetDraft() { this.draft = null; this.revision++; this.dialog?.close(); },
    draftPayload() { return this.draft ? structuredClone(this.draft) : null; },
    async request(url, method = 'GET', body) {
        let cur = null;
        try { cur = await this.currentBrowser(); } catch (_) {}
        const headers = {'Content-Type': 'application/json'};
        if (cur) headers['X-Client-Browser'] = cur;
        const response = await API._fetch(url, {
            method, headers,
            ...(body === undefined ? {} : {body: JSON.stringify(body)})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
        return data;
    },
    async currentBrowser() {
        try {
            if (window.navigator.brave) {
                if (typeof window.navigator.brave.isBrave === 'function') {
                    if (await window.navigator.brave.isBrave()) return 'brave';
                }
                return 'brave';
            }
        } catch (_) {
            if (window.navigator.brave) return 'brave';
        }
        if (navigator.userAgentData?.brands?.some(b => /brave/i.test(b.brand))) return 'brave';
        if (/Edg\//i.test(navigator.userAgent)) return 'edge';
        if (navigator.userAgentData?.brands?.some(b => /microsoft edge|edg/i.test(b.brand))) return 'edge';
        if (/Chrome\//i.test(navigator.userAgent)) return 'chrome';
        return 'chrome';
    },
    init() {
        this.dialog = document.createElement('dialog');
        this.dialog.className = 'tools-dialog';
        this.dialog.setAttribute('aria-labelledby', 'tools-title');
        this.dialog.innerHTML = `<header class="tools-header"><div><h2 id="tools-title">Fonksiyonlar</h2><p id="tools-source"></p></div><button type="button" class="tools-close" aria-label="Kapat"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 5l10 10M15 5 5 15"/></svg></button></header>
            <div class="tools-layout"><div id="tools-list" class="tools-list" aria-label="Fonksiyon kategorileri"></div></div>
            <p id="tools-error" role="status" aria-live="polite"></p>
            <footer class="tools-footer"><button type="button" id="tools-reset" class="tools-button tools-button-ghost">Sıfırla</button><button type="button" id="tools-save" class="tools-button tools-button-primary">Tamam</button></footer>`;
        document.body.appendChild(this.dialog);
        this.dialog.querySelector('.tools-close').onclick = () => { if (!this.saving) this.dialog.close(); };
        this.dialog.addEventListener('click', event => { if (event.target === this.dialog && !this.saving) this.dialog.close(); });
        this.dialog.addEventListener('close', () => { this.revision++; });
        this.dialog.addEventListener('cancel', event => { if (this.saving) event.preventDefault(); });
        const plus = document.getElementById('composer-plus');
        const menu = document.getElementById('composer-menu');
        plus.onclick = () => { menu.hidden = !menu.hidden; plus.setAttribute('aria-expanded', String(!menu.hidden)); };
        document.getElementById('composer-tools').onclick = () => { menu.hidden = true; plus.setAttribute('aria-expanded', 'false'); this.open(false); };
        document.addEventListener('click', event => {
            if (!event.target.closest('.composer-add')) { menu.hidden = true; plus.setAttribute('aria-expanded', 'false'); }
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !menu.hidden) { menu.hidden = true; plus.setAttribute('aria-expanded', 'false'); plus.focus(); }
        });
        setTimeout(async () => {
            try {
                const cur = await this.currentBrowser();
                const data = await this.request('/api/v1/browser');
                const target = cur || (data && data.browser) || 'chrome';
                const shouldAuto = Boolean(data && data.saved && data.autoconnect !== false);
                if (shouldAuto && (!data.running || data.browser !== target)) {
                    await this.request('/api/v1/browser/open', 'POST', {browser: target});
                }
                await this.refreshBrowserStatus?.();
            } catch (_) {}
        }, 500);
    },
    renderSettings() {
        const dashboard = document.getElementById('settings-dashboard');
        if (!dashboard) return;
        if (dashboard.querySelector('.tool-settings-card')) {
            this.refreshBrowserStatus?.();
            return;
        }
        const card = document.createElement('section');
        card.className = 'settings-card tool-settings-card';
        card.innerHTML = '<h3>Varsayılan fonksiyonlar</h3><p>Özel seçim yapılmamış sohbetlerde kullanılacak fonksiyonları belirleyin.</p><button type="button" class="btn btn-primary">Fonksiyonları düzenle</button>';
        card.querySelector('button').onclick = () => this.open(true);
        const host = document.getElementById('settings-panel-general') || dashboard;
        host.appendChild(card);
        const browserCard = document.createElement('section');
        browserCard.className = 'settings-card tool-settings-card';
        browserCard.innerHTML = '<h3>Tarayıcı</h3><p>Mevcut açık tarayıcınızdaki oturumları kullanarak yerel tarayıcı işlemlerini yürütür.</p>';
        host.appendChild(browserCard);
        this.renderGoogleSession(browserCard);
    },
    renderGoogleOptions(target, settings) {
        const row = document.createElement('label'); row.className = 'tool-row';
        const copy = document.createElement('span'); copy.className = 'tool-row-copy';
        const title = document.createElement('strong'); title.textContent = 'Google yanıtını doğrudan akıt';
        const description = document.createElement('small');
        description.textContent = 'Açıkken Google yanıtı oluştukça ekrana aktarılır; Orion modeli yeniden yazmaz. Kapalıyken Google sonucu Orion modeline verilir. Tüm sohbetlerde geçerlidir.';
        copy.append(title, description);
        const status = document.createElement('p'); status.setAttribute('role', 'status');
        const user = AppConfig.getUserId();
        const input = this.toggle(title.textContent, settings.google_ai_direct_stream !== false && settings.google_ai_direct_stream !== 'false', async value => {
            if (user !== AppConfig.getUserId()) return;
            input.disabled = true; status.textContent = '';
            try {
                await this.request('/api/v1/admin/settings', 'POST', {
                    user_id: user, values: {google_ai_direct_stream: String(value)}
                });
            } catch (error) {
                input.checked = !value;
                status.textContent = `Kaydedilemedi: ${error.message}`;
            } finally { input.disabled = false; }
        });
        row.append(copy, input); target.append(row, status);
    },
    renderGoogleSession(target) {
        const panel = document.createElement('div'); panel.className = 'google-session';
        const status = document.createElement('p'); status.setAttribute('role', 'status');
        status.style.cssText = 'margin: 18px 0 8px 0; font-size: 13px; line-height: 1.55; color: var(--text-secondary);';
        const hint = document.createElement('p'); hint.className = 'google-session-hint';
        hint.style.cssText = 'margin-top:10px;font-size:12px;color:var(--text-muted);line-height:1.5;';
        const actions = document.createElement('div'); actions.className = 'google-session-actions';
        const select = document.createElement('select'); select.setAttribute('aria-label', 'Orion tarayıcısı');
        for (const [value, label] of [['chrome', 'Chrome'], ['edge', 'Edge'], ['brave', 'Brave']]) {
            const option = document.createElement('option'); option.value = value; option.textContent = label;
            select.appendChild(option);
        }
        const buttons = [];
        const current = () => panel.isConnected;

        const updateHint = (connected) => {
            if (connected) {
                hint.textContent = '';
                hint.style.display = 'none';
            } else {
                const b = select.value || 'brave';
                const inspectUrl = `${b}://inspect/#remote-debugging`;
                hint.innerHTML = `Otomatik bağlanamazsa: Yeni sekmede <code>${inspectUrl}</code> sayfasını açıp <em>Uzaktan hata ayıklamayı etkinleştir</em> kutusunu işaretleyin, ardından <strong>Tarayıcıya bağlan</strong> düğmesine basın ve ekrana gelecek pencereden izin verin.`;
                hint.style.display = 'block';
            }
        };

        select.addEventListener('change', () => {
            select.dataset.userChanged = 'true';
            updateHint(false);
        });
        actions.appendChild(select);

        const disconnectBtn = document.createElement('button');
        disconnectBtn.type = 'button';
        disconnectBtn.className = 'tools-button tools-button-ghost';
        disconnectBtn.textContent = 'Bağlantıyı kes';
        disconnectBtn.style.display = 'none';
        disconnectBtn.onclick = async () => {
            if (!current()) return;
            disconnectBtn.disabled = true; select.disabled = true;
            status.textContent = 'Bağlantı kesiliyor…';
            try {
                await this.request('/api/v1/browser/disconnect', 'POST');
                await refresh();
            } catch (error) {
                if (current()) status.textContent = 'Hata: ' + error.message;
            } finally {
                disconnectBtn.disabled = false; select.disabled = false;
            }
        };

        const refresh = async () => {
            const data = await this.request('/api/v1/browser');
            const currentBrowser = await this.currentBrowser();
            if (!current()) return;
            const activeBrowser = (data.running && data.browser) || currentBrowser || data.suggested_browser || 'chrome';
            if (select.dataset.userChanged !== 'true') select.value = activeBrowser;
            const isConnected = Boolean(data && data.running);
            status.textContent = isConnected
                ? 'Bağlantı açık. Araçlar aynı bağlantıyı kullanır; tarayıcıyı küçültebilirsiniz.'
                : 'Bağlan düğmesine basın.';
            disconnectBtn.style.display = isConnected ? 'inline-block' : 'none';
            updateHint(isConnected);
        };
        this.refreshBrowserStatus = refresh;

        const add = (label, action) => {
            const button = document.createElement('button'); button.type = 'button';
            button.className = 'tools-button'; button.textContent = label;
            button.onclick = async () => {
                if (!current()) return;
                buttons.forEach(item => item.disabled = true); select.disabled = true;
                status.textContent = 'Bağlanıyor…';
                try { await action(); }
                catch (error) {
                    if (current()) {
                        status.textContent = 'Bağlanılamadı: ' + error.message;
                        updateHint(false);
                    }
                }
                finally { buttons.forEach(item => item.disabled = false); select.disabled = false; }
            };
            buttons.push(button); actions.appendChild(button);
        };
        add('Tarayıcıya bağlan', async () => {
            try {
                const data = await this.request('/api/v1/browser/open', 'POST', {browser: select.value});
                if (current()) status.textContent = data.message;
                await refresh();
            } catch (error) {
                if (current()) {
                    status.textContent = 'Bağlanılamadı: ' + error.message;
                    updateHint(false);
                }
            }
        });
        actions.appendChild(disconnectBtn);
        panel.append(actions, status, hint); target.appendChild(panel);
        refresh().catch(error => { if (current()) status.textContent = error.message; });
    },
    async open(defaults) {
        const revision = ++this.revision;
        this.saving = false;
        const chatId = AppState.currentChatId;
        const userId = AppConfig.getUserId();
        const list = this.dialog.querySelector('#tools-list');
        const error = this.dialog.querySelector('#tools-error');
        const save = this.dialog.querySelector('#tools-save');
        const reset = this.dialog.querySelector('#tools-reset');
        this.dialog.querySelector('#tools-title').textContent = defaults ? 'Varsayılan fonksiyonlar' : 'Sohbet fonksiyonları';
        const source = this.dialog.querySelector('#tools-source');
        source.textContent = defaults ? 'Yeni sohbetlerde kullanılacak fonksiyonları seçin.' : 'Bu sohbette kullanılacak fonksiyonları seçin.';
        list.replaceChildren(); list.textContent = 'Yükleniyor…'; error.textContent = '';
        list.setAttribute('aria-busy', 'true');
        save.disabled = true; reset.hidden = false; reset.disabled = true;
        this.dialog.showModal();
        const current = () => revision === this.revision && userId === AppConfig.getUserId();
        try {
            const [catalog, settings, chat, factory] = await Promise.all([
                this.request('/api/v1/tools'), this.request('/api/v1/admin/settings'),
                !defaults && chatId ? this.request(`/api/v1/chats/${encodeURIComponent(chatId)}/tools`) : null,
                defaults ? this.request('/api/v1/tools/default-selection') : null
            ]);
            if (!current()) return;
            list.replaceChildren();
            const normalize = value => ({
                categories: Object.fromEntries(catalog.map(cat => [cat.id, value?.categories?.[cat.id] === true])),
                functions: Object.fromEntries(catalog.flatMap(cat => cat.functions.map(fn => [fn.id, value?.functions?.[fn.id] === true])))
            });
            const defaultSelection = normalize(settings.tool_selection);
            const resetSelection = defaults ? normalize(factory) : defaultSelection;
            let selected = normalize(defaults ? settings.tool_selection : chat ? chat.selection : this.draft || settings.tool_selection);
            let savedSelection = structuredClone(selected);
            source.textContent = defaults ? 'Yeni sohbetlerde kullanılacak fonksiyonları seçin.' :
                'Bu sohbette kullanılacak fonksiyonları seçin.';
            if (!catalog.length) list.textContent = 'Henüz fonksiyon eklenmemiş.';
            const categoryRows = new Map();
            const switches = [];
            const updateCategoryBadge = cat => {
                const badge = categoryRows.get(cat.id)?.querySelector('.tool-category-status');
                if (!badge) return;
                const enabled = cat.functions.filter(fn => selected.functions[fn.id]).length;
                badge.textContent = selected.categories[cat.id] ? `${enabled}/${cat.functions.length}` : '';
                categoryRows.get(cat.id).querySelector('.tools-functions').disabled = !selected.categories[cat.id];
            };
            const refresh = () => {
                for (const entry of switches) entry.input.checked = selected[entry.kind][entry.id];
                for (const cat of catalog) updateCategoryBadge(cat);
            };
            let pendingReset = false;
            const setSaving = saving => {
                this.saving = saving;
                reset.setAttribute('aria-busy', String(saving));
                for (const entry of switches) entry.input.disabled = saving ||
                    (entry.kind === 'functions' && !selected.categories[entry.categoryId]);
            };
            const persist = async useDefaults => {
                if (this.saving || !current()) return;
                setSaving(true); error.textContent = '';
                try {
                    if (defaults) {
                        await this.request('/api/v1/admin/settings', 'POST', {user_id: userId, values: {tool_selection: JSON.stringify(selected)}});
                    } else if (chatId) {
                        await this.request(`/api/v1/chats/${encodeURIComponent(chatId)}/tools`, useDefaults ? 'DELETE' : 'PUT', useDefaults ? undefined : selected);
                    } else {
                        this.draft = useDefaults ? null : structuredClone(selected);
                    }
                    savedSelection = structuredClone(selected);
                    if (current() && typeof UI !== 'undefined') UI.showToast('Kaydedildi');
                } catch (exc) {
                    if (current()) {
                        selected = structuredClone(savedSelection);
                        refresh();
                        error.textContent = `Kaydedilemedi: ${exc.message}`;
                    }
                } finally {
                    if (current()) {
                        setSaving(false);
                        if (pendingReset) {
                            pendingReset = false;
                            await reset.onclick();
                        }
                    }
                }
            };
            for (const cat of catalog) {
                const card = document.createElement('section'); card.className = 'tool-category';
                const expanded = false;
                card.classList.toggle('is-expanded', expanded);
                const row = document.createElement('div'); row.className = 'tool-category-row'; row.dataset.categoryId = cat.id;
                const button = document.createElement('button'); button.type = 'button'; button.className = 'tool-category-button';
                button.id = `tools-category-button-${cat.id}`;
                button.setAttribute('aria-expanded', String(expanded));
                button.setAttribute('aria-controls', `tools-category-content-${cat.id}`);
                button.setAttribute('aria-label', `${cat.name} fonksiyonları`);
                const icon = document.createElement('span'); icon.className = 'tool-category-icon';
                icon.innerHTML = ({
                    datetime: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8"/><path d="M12 7v5l3.5 2"/></svg>',
                    text: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 7h14M5 12h14M5 17h14"/></svg>'
                })[cat.id] || '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2z"/></svg>';
                icon.setAttribute('aria-hidden', 'true');
                const copy = document.createElement('span'); copy.className = 'tool-category-copy';
                const name = document.createElement('strong'); name.textContent = cat.name;
                const status = document.createElement('span'); status.className = 'tool-category-status';
                copy.append(name, status);
                const chevron = document.createElement('span'); chevron.className = 'tool-category-chevron';
                chevron.innerHTML = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m7 5 5 5-5 5"/></svg>';
                chevron.setAttribute('aria-hidden', 'true');
                button.append(icon, chevron, copy);
                const reveal = document.createElement('div'); reveal.className = 'tool-category-reveal';
                reveal.id = `tools-category-content-${cat.id}`;
                reveal.setAttribute('role', 'region'); reveal.setAttribute('aria-labelledby', button.id);
                reveal.setAttribute('aria-hidden', String(!expanded)); reveal.inert = !expanded;
                const content = document.createElement('div'); content.className = 'tool-category-content';
                const description = document.createElement('p'); description.className = 'tool-category-description';
                description.textContent = cat.description;
                content.appendChild(description);
                if (cat.id === 'google_ai') {
                    this.renderGoogleOptions(content, settings);
                }
                const children = document.createElement('fieldset'); children.className = 'tools-functions';
                children.disabled = !selected.categories[cat.id];
                for (const fn of cat.functions) {
                    const functionRow = document.createElement('label'); functionRow.className = 'tool-row';
                    const text = document.createElement('span'); text.className = 'tool-row-copy';
                    const name = document.createElement('strong'); name.textContent = fn.name;
                    const fnDescription = document.createElement('small'); fnDescription.textContent = fn.description;
                    text.append(name, fnDescription); functionRow.appendChild(text);
                    const input = this.toggle(fn.name, selected.functions[fn.id], async value => {
                        const isBrowser = Boolean(fn.requires_browser || cat.requires_browser);
                        if (value && isBrowser) {
                            const confirmed = await this.requestBrowserConnectionConfirm(fn.name);
                            if (!confirmed) {
                                input.checked = false;
                                return;
                            }
                        }
                        selected.functions[fn.id] = input.checked;
                        updateCategoryBadge(cat);
                        persist(false);
                    });
                    switches.push({input, kind: 'functions', id: fn.id, categoryId: cat.id});
                    functionRow.appendChild(input);
                    children.appendChild(functionRow);
                }
                content.appendChild(children); reveal.appendChild(content);
                button.onclick = () => {
                    const open = !card.classList.contains('is-expanded');
                    card.classList.toggle('is-expanded', open);
                    button.setAttribute('aria-expanded', String(open));
                    reveal.setAttribute('aria-hidden', String(!open));
                    reveal.inert = !open;
                };
                const toggle = this.toggle(cat.name + ' kategorisi', selected.categories[cat.id], async value => {
                    if (value && cat.requires_browser) {
                        const confirmed = await this.requestBrowserConnectionConfirm(cat.name);
                        if (!confirmed) {
                            toggle.checked = false;
                            return;
                        }
                    }
                    selected.categories[cat.id] = toggle.checked;
                    updateCategoryBadge(cat);
                    persist(false);
                });
                switches.push({input: toggle, kind: 'categories', id: cat.id});
                row.append(button, toggle); card.append(row, reveal); list.appendChild(card);
                categoryRows.set(cat.id, card); updateCategoryBadge(cat);
            }
            list.setAttribute('aria-busy', 'false');
            save.disabled = false;
            reset.disabled = false;
            setSaving(false);
            save.onclick = () => { if (!this.saving) this.dialog.close(); };
            reset.onclick = async () => {
                if (this.saving) { pendingReset = true; return; }
                selected = structuredClone(resetSelection);
                refresh();
                await persist(true);
            };
        } catch (exc) { if (current()) { list.setAttribute('aria-busy', 'false'); list.replaceChildren(); error.textContent = exc.message; } }
    },
    async requestBrowserConnectionConfirm(featureName) {
        try {
            const data = await this.request('/api/v1/browser');
            if (data && data.running) return true;
        } catch (_) {}

        return new Promise(resolve => {
            const dialog = document.createElement('dialog');
            dialog.className = 'tools-dialog browser-confirm-dialog';
            dialog.style.cssText = 'max-width: 440px; border-radius: 16px; padding: 0; box-shadow: 0 20px 60px rgba(0,0,0,0.6);';
            dialog.innerHTML = `
                <header class="tools-header" style="padding: 22px 24px 14px;">
                    <div>
                        <h2 style="font-size: 18px; margin: 0 0 6px;">Tarayıcı bağlantısı gerekli</h2>
                        <p style="font-size: 12px; color: var(--text-secondary); margin: 0;">Bu fonksiyon için yerel tarayıcı kontrolü gerekiyor</p>
                    </div>
                </header>
                <div style="padding: 0 24px 20px; font-size: 13px; line-height: 1.6; color: var(--text-secondary);">
                    <strong>${featureName || 'Bu fonksiyon'}</strong> özelliğinin çalışabilmesi için aktif açık tarayıcınıza bağlanılması gereklidir.<br><br>
                    Tarayıcı bağlantı prosedürü şimdi başlatılsın mı?
                </div>
                <footer class="tools-footer" style="padding: 14px 24px 18px; display: flex; justify-content: flex-end; gap: 10px;">
                    <button type="button" class="tools-button tools-button-ghost" id="browser-confirm-cancel">Vazgeç</button>
                    <button type="button" class="tools-button tools-button-primary" id="browser-confirm-ok">Tarayıcıya bağlan</button>
                </footer>
            `;
            document.body.appendChild(dialog);
            dialog.showModal();

            let handled = false;
            const finish = async (confirmed) => {
                if (handled) return;
                handled = true;
                dialog.close();
                dialog.remove();
                if (confirmed) {
                    await this.ensureBrowserConnected();
                }
                resolve(confirmed);
            };

            dialog.querySelector('#browser-confirm-cancel').onclick = () => finish(false);
            dialog.querySelector('#browser-confirm-ok').onclick = () => finish(true);
            dialog.addEventListener('cancel', () => finish(false));
            dialog.addEventListener('click', (e) => {
                if (e.target === dialog) finish(false);
            });
        });
    },
    async ensureBrowserConnected() {
        try {
            const data = await this.request('/api/v1/browser');
            if (!data.running) {
                const cur = await this.currentBrowser();
                await this.request('/api/v1/browser/open', 'POST', {browser: cur || data.browser || 'chrome'});
                await this.refreshBrowserStatus?.();
            }
        } catch (_) {}
    },
    toggle(label, checked, change) {
        const input = document.createElement('input'); input.type = 'checkbox'; input.checked = checked;
        input.className = 'tool-switch'; input.setAttribute('role', 'switch'); input.setAttribute('aria-label', label);
        input.onchange = () => change(input.checked); return input;
    },
    renderActivity(target, item) {
        const key = `${item.turn_id || ''}:${item.call_id}`;
        let card = Array.from(target.querySelectorAll('.tool-activity')).find(el => el.dataset.callKey === key);
        if (!card) {
            card = document.createElement('details'); card.className = 'tool-activity'; card.dataset.callKey = key;
            card.append(document.createElement('summary'), document.createElement('pre')); target.appendChild(card);
        }
        // A replayed start event must not overwrite a terminal result.
        if (card.dataset.status && card.dataset.status !== 'running' && item.status === 'running') return;
        card.dataset.status = item.status;
        const statuses = {running: 'Çalışıyor…', completed: 'Tamamlandı', error: 'Hata', cancelled: 'İptal edildi'};
        let args = item.arguments;
        try { args = JSON.parse(args); } catch (_) { /* Show malformed arguments verbatim. */ }
        card.querySelector('pre').textContent = 'Girdi\n' + JSON.stringify(args, null, 2) +
            (item.result === undefined ? '' : '\n\nSonuç\n' + JSON.stringify(item.result,
                (key, value) => key === 'truncated' ? undefined : value, 2));
        card.querySelector('.google-session')?.remove();

        let result = item.result;
        if (typeof result === 'string') {
            try { result = JSON.parse(result); } catch (_) { /* Keep plain text. */ }
        }
        const needsBrowser = result?.status === 'not_configured';

        let displayStatus = statuses[item.status] || item.status;
        let cardStatus = item.status;
        if (item.status === 'completed' && item.result !== undefined) {
            const r = result;
            if (needsBrowser) {
                displayStatus = 'Tarayıcı gerekli';
                cardStatus = 'not_configured';
            } else if (r?.status === 'tab_missing') {
                displayStatus = 'Sekme bulunamadı';
                cardStatus = 'warning';
            } else if (r?.status === 'verification_required' || r?.status === 'interaction_required') {
                displayStatus = 'Doğrulama gerekli';
                cardStatus = 'warning';
            } else if (r?.status === 'timeout') {
                displayStatus = 'Zaman aşımı';
                cardStatus = 'error';
            } else if (r?.status === 'blocked') {
                displayStatus = 'Engellendi';
                cardStatus = 'error';
            } else if (r?.status === 'busy') {
                displayStatus = 'Meşgul';
                cardStatus = 'warning';
            } else if (r?.status === 'provider_error' || r?.status === 'error' || r?.error) {
                displayStatus = 'Hata';
                cardStatus = 'error';
            }
        }
        card.dataset.status = cardStatus;
        card.querySelector('summary').textContent = `${item.label || item.name} · ${displayStatus}`;

        let connectBtn = target.querySelector(`.tool-connect-btn[data-call-key="${key}"]`);
        if (needsBrowser) {
            if (!connectBtn) {
                connectBtn = document.createElement('button');
                connectBtn.type = 'button';
                connectBtn.className = 'tool-connect-btn tools-button tools-button-primary';
                connectBtn.dataset.callKey = key;
                connectBtn.style.cssText = 'display:inline-flex;align-items:center;gap:6px;margin:8px 0 10px;font-size:12px;font-weight:600;padding:7px 15px;border-radius:9px;cursor:pointer;';
                connectBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/></svg> Tarayıcıya bağlan';
                connectBtn.onclick = async (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    connectBtn.disabled = true;
                    connectBtn.textContent = 'Bağlanıyor…';
                    try {
                        const cur = await ToolsUI.currentBrowser();
                        await ToolsUI.request('/api/v1/browser/open', 'POST', {browser: cur || 'chrome'});
                        connectBtn.textContent = 'Bağlandı ✓';
                        connectBtn.style.background = '#059669';
                        await ToolsUI.refreshBrowserStatus?.();
                    } catch (err) {
                        connectBtn.disabled = false;
                        connectBtn.textContent = 'Yeniden dene';
                    }
                };
                card.insertAdjacentElement('afterend', connectBtn);
            }
        } else if (connectBtn) {
            connectBtn.remove();
        }
    },
    liveActivity(chatId, item) {
        UI.finishThinking(chatId);
        const state = UI._getOrCreateChatState(chatId);
        if (item.status === 'running') state.currentContentNode = null;
        if (!state.botDiv) UI.createBotMessagePlaceholder(chatId);
        if (state.botDiv.classList.contains('typing')) { state.botDiv.replaceChildren(); state.botDiv.classList.remove('typing'); }
        this.renderActivity(state.botDiv, item);
    }
};
window.ToolsUI = ToolsUI;
document.addEventListener('DOMContentLoaded', () => ToolsUI.init());
