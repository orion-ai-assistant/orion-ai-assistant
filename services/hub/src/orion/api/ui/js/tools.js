/* Server-owned tool catalog; the dashboard only selects registered tools. */

const ToolsUI = {

    draft: null,

    catalog: null,

    revision: 0,

    async loadCatalog() {

        if (this.catalog) return this.catalog;

        if (!this.catalogPromise) {

            this.catalogPromise = this.request('/api/v1/tools').then(catalog => {

                this.catalog = catalog;

                this.catalogPromise = null;

                document.querySelectorAll('.tool-activity[data-tool-id]').forEach(card => {

                    let name = this.toolName(card.dataset.toolId);
                    const statusKey = card.dataset.statusKey;
                    const statusText = statusKey ? (OrionI18n.t(statusKey) || card.dataset.statusText) : card.dataset.statusText;
                    if (!name) {
                        const summary = card.querySelector('summary').textContent;
                        const parts = summary.split(' · ');
                        if (parts.length > 0) name = parts[0];
                    }
                    card.querySelector('summary').textContent = `${name || card.dataset.toolId} · ${statusText}`;
                    card.dataset.statusText = statusText;

                });

                return catalog;

            }).catch(error => { this.catalogPromise = null; throw error; });

        }

        return this.catalogPromise;

    },

    toolName(id) {

        return this.catalog?.flatMap(category => category.functions).find(tool => tool.id === id)?.name;

    },

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

        this.dialog.innerHTML = `<header class="tools-header"><div><h2 id="tools-title" data-i18n="tools">${OrionI18n.t('tools')}</h2><p id="tools-source"></p></div><button type="button" class="tools-close" aria-label="${OrionI18n.t('close')}"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 5l10 10M15 5 5 15"/></svg></button></header>

            <div class="tools-layout"><div id="tools-list" class="tools-list" aria-label="${OrionI18n.t('toolCategories')}"></div></div>

            <p id="tools-error" role="status" aria-live="polite"></p>

            <footer class="tools-footer"><button type="button" id="tools-reset" class="tools-button tools-button-ghost" data-i18n="reset">${OrionI18n.t('reset')}</button><button type="button" id="tools-save" class="tools-button tools-button-primary" data-i18n="done">${OrionI18n.t('done')}</button></footer>`;

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

        card.innerHTML = `

            <div class="settings-card-header">

                <div class="settings-card-icon">⚡</div>

                <div>

                    <h3>${OrionI18n.t('defaultTools')}</h3>

                </div>

            </div>

            <div class="settings-card-body">

                <div class="setting-row compact-row">

                    <div class="setting-info">

                        <label class="setting-label">${OrionI18n.t('chatTools')}</label>

                    </div>

                    <div class="setting-control setting-control-compact">

                        <button type="button" class="btn btn-primary" id="btn-edit-tools" style="padding: 9px 18px; font-size: 0.85rem;">${OrionI18n.t('editTools')}</button>

                    </div>

                </div>

            </div>`;

        card.querySelector('#btn-edit-tools').onclick = () => this.open(true);

        const host = document.getElementById('settings-panel-general') || dashboard;

        host.appendChild(card);



        const browserCard = document.createElement('section');

        browserCard.className = 'settings-card tool-settings-card';

        browserCard.innerHTML = `

            <div class="settings-card-header">

                <div class="settings-card-icon">🧭</div>

                <div>

                    <h3>${OrionI18n.t('browser')}</h3>

                    <p>${OrionI18n.t('browserHint')}</p>

                </div>

            </div>

            <div class="settings-card-body"></div>`;

        host.appendChild(browserCard);

        this.renderBrowserSession(browserCard.querySelector('.settings-card-body'));

        if (HubPicker.self?.local_entry) this.renderConnection(host);

    },

    async renderConnection(host) {
        const card = document.createElement('section');
        card.className = 'settings-card tool-settings-card';
        const title = document.createElement('h3'); title.textContent = 'Başka bilgisayardan erişim';
        const copy = document.createElement('p'); copy.className = 'setting-hint';
        copy.textContent = 'Bu PC’deki Hub’ın güncel HTTPS ağ adresi. Aynı ağdaki başka bilgisayarda açabilirsin; IP adresi değişebilir.';
        const addresses = document.createElement('div'); addresses.className = 'settings-card-body connection-addresses';
        const header = document.createElement('div'); header.className = 'settings-card-header';
        const description = document.createElement('div'); description.append(title, copy); header.append(description);
        card.append(header, addresses); host.append(card);
        try {
            const data = await HubPicker.json('/api/v1/discovery/connection');
            for (const url of data.urls) {
                const link = document.createElement('a'); link.textContent = url; link.href = url;
                link.target = '_blank'; link.rel = 'noopener noreferrer';
                const row = document.createElement('p'); row.append(link); addresses.append(row);
            }
            if (!data.urls.length) addresses.textContent = 'Yerel ağ adresi bulunamadı.';
        } catch (error) { addresses.textContent = error.message; }
    },

    renderGoogleOptions(target, settings) {

        const row = document.createElement('label'); row.className = 'tool-row';

        const copy = document.createElement('span'); copy.className = 'tool-row-copy';

        const title = document.createElement('strong'); title.textContent = OrionI18n.t('googleDirect');

        const description = document.createElement('small');

        description.textContent = OrionI18n.t('googleDirectHint');

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

                status.textContent = OrionI18n.t('saveFailed', error.message);

            } finally { input.disabled = false; }

        });

        row.append(copy, input); target.append(row, status);

    },

    renderBrowserSession(target) {
        const row = document.createElement('div');
        row.className = 'setting-row compact-row';

        // Info: label + action button side by side
        const info = document.createElement('div'); info.className = 'setting-info browser-session-info';
        const label = document.createElement('label'); label.className = 'setting-label'; label.textContent = OrionI18n.t('orionBrowser');

        const actionBtn = document.createElement('button'); actionBtn.type = 'button'; actionBtn.className = 'btn btn-primary';
        actionBtn.style.marginLeft = '28px'; actionBtn.style.padding = '6px 14px'; actionBtn.style.fontSize = '0.82rem';

        // hint used only for errors
        const hint = document.createElement('span'); hint.className = 'setting-hint'; hint.style.display = 'none'; hint.style.marginTop = '4px';

        const labelRow = document.createElement('div'); labelRow.style.display = 'flex'; labelRow.style.alignItems = 'center';
        labelRow.append(label, actionBtn);
        info.append(labelRow, hint);

        // Control: only the dropdown on the right
        const control = document.createElement('div'); control.className = 'setting-control setting-control-compact';
        const select = document.createElement('select'); select.className = 'setting-select'; select.style.width = '120px';
        for (const [v, t] of [['chrome', 'Chrome'], ['edge', 'Edge'], ['brave', 'Brave']]) {
            const option = document.createElement('option'); option.value = v; option.textContent = t; select.appendChild(option);
        }
        control.append(select);
        row.append(info, control); target.appendChild(row);

        let isConnected = false;
        const current = () => row.isConnected;

        const clearHint = () => { hint.textContent = ''; hint.style.display = 'none'; };

        const showError = (msg) => {
            hint.textContent = msg;
            hint.style.color = 'var(--error-color, #ef4444)';
            hint.style.display = 'block';
        };

        select.addEventListener('change', () => { select.dataset.userChanged = 'true'; isConnected = false; actionBtn.textContent = OrionI18n.t('connectBrowser'); actionBtn.className = 'btn btn-primary'; clearHint(); });

        const refresh = async () => {
            if (HubPicker.selected?.id !== HubPicker.self?.id) {
                row.replaceChildren();
                const message = document.createElement('p'); message.className = 'setting-hint';
                message.textContent = OrionI18n.t('browserOwnerOnly'); row.append(message);
                return;
            }
            const response = await fetch('/api/v1/browser', {headers: Auth.getAuthHeaders({'X-Client-Browser': (await this.currentBrowser()) || ''})});
            if (response.status === 403) { row.replaceChildren(); const message = document.createElement('p'); message.className = 'setting-hint'; message.textContent = OrionI18n.t('browserOwnerOnly'); row.append(message); return; }
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Tarayıcı durumu alınamadı.');
            const currentBrowser = await this.currentBrowser();
            if (!current()) return;
            const activeBrowser = (data.saved && data.browser) || currentBrowser || data.suggested_browser || 'chrome';
            if (select.dataset.userChanged !== 'true') select.value = activeBrowser;
            isConnected = Boolean(data && data.running);
            actionBtn.textContent = isConnected ? OrionI18n.t('disconnect') : OrionI18n.t('connectBrowser');
            actionBtn.className = isConnected ? 'btn btn-disconnect' : 'btn btn-primary';
            clearHint();
        };

        actionBtn.onclick = async () => {
            if (!current()) return;
            actionBtn.disabled = true; select.disabled = true;
            actionBtn.textContent = isConnected ? OrionI18n.t('disconnecting') : OrionI18n.t('connecting');
            try {
                if (isConnected) await this.request('/api/v1/browser/disconnect', 'POST');
                else await this.request('/api/v1/browser/open', 'POST', {browser: select.value});
                await refresh();
            } catch (error) {
                if (current()) showError((isConnected ? 'Hata: ' : OrionI18n.t('connectFailed', '')) + error.message);
            } finally { actionBtn.disabled = false; select.disabled = false; }
        };

        this.refreshBrowserStatus = refresh;
        refresh().catch(e => { if (current()) showError(e.message); });
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

        this.dialog.querySelector('#tools-title').textContent = OrionI18n.t(defaults ? 'defaultTools' : 'chatTools');

        const source = this.dialog.querySelector('#tools-source');

        source.textContent = '';

        list.replaceChildren(); list.textContent = OrionI18n.t('loading'); error.textContent = '';

        list.setAttribute('aria-busy', 'true');

        save.disabled = true; reset.hidden = false; reset.disabled = true;

        this.dialog.showModal();

        const current = () => revision === this.revision && userId === AppConfig.getUserId();

        try {

            const [catalog, settings, chat, factory] = await Promise.all([

                this.loadCatalog(), this.request('/api/v1/admin/settings'),

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

            source.textContent = '';

            if (!catalog.length) list.textContent = OrionI18n.t('noTools');

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

                    if (current() && typeof UI !== 'undefined') UI.showToast(OrionI18n.t('saved'));

                } catch (exc) {

                    if (current()) {

                        selected = structuredClone(savedSelection);

                        refresh();

                        error.textContent = OrionI18n.t('saveFailed', exc.message);

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

                button.setAttribute('aria-label', OrionI18n.t('categoryTools', cat.name));

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

                if (cat.id === 'google_ai') {

                    this.renderGoogleOptions(content, settings);

                }

                const children = document.createElement('fieldset'); children.className = 'tools-functions';

                children.disabled = !selected.categories[cat.id];

                for (const fn of cat.functions) {

                    const functionRow = document.createElement('label'); functionRow.className = 'tool-row';

                    const text = document.createElement('span'); text.className = 'tool-row-copy';

                    const name = document.createElement('strong'); name.textContent = fn.name;

                    text.append(name); functionRow.appendChild(text);

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

                const toggle = this.toggle(OrionI18n.t('category', cat.name), selected.categories[cat.id], async value => {

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

                        <h2 style="font-size: 18px; margin: 0 0 6px;">${OrionI18n.t('browserRequired')}</h2>

                        <p style="font-size: 12px; color: var(--text-secondary); margin: 0;">${OrionI18n.t('browserRequiredHint')}</p>

                    </div>

                </header>

                <div style="padding: 0 24px 20px; font-size: 13px; line-height: 1.6; color: var(--text-secondary);">

                    ${OrionI18n.t('browserConfirm', featureName || OrionI18n.t('tools'))}

                </div>

                <footer class="tools-footer" style="padding: 14px 24px 18px; display: flex; justify-content: flex-end; gap: 10px;">

                    <button type="button" class="tools-button tools-button-ghost" id="browser-confirm-cancel">${OrionI18n.t('cancel')}</button>

                    <button type="button" class="tools-button tools-button-primary" id="browser-confirm-ok">${OrionI18n.t('connectBrowser')}</button>

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

                await this.request('/api/v1/browser/open', 'POST', {browser: (data.saved && data.browser) || cur || data.suggested_browser || 'chrome'});

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

            card.append(document.createElement('summary'), document.createElement('pre'));

            card.addEventListener('toggle', () => {

                if (card.open) {

                    requestAnimationFrame(() => {

                        card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

                        const chatArea = UI.chatArea;

                        if (chatArea) {

                            const isNearBottom = chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight < 450;

                            if (isNearBottom) UI.scrollToBottom();

                        }

                    });

                }

            });

            target.appendChild(card);

        }

        // A replayed start event must not overwrite a terminal result.

        if (card.dataset.status && card.dataset.status !== 'running' && item.status === 'running') return;

        card.dataset.status = item.status;

        const statuses = {running: OrionI18n.t('statusRunning'), completed: OrionI18n.t('statusCompleted'),

            error: OrionI18n.t('statusError'), cancelled: OrionI18n.t('statusCancelled')};

        let args = item.arguments;

        try { args = JSON.parse(args); } catch (_) { /* Show malformed arguments verbatim. */ }

        card.querySelector('.google-session')?.remove();



        let result = item.result;

        if (typeof result === 'string') {

            try { result = JSON.parse(result); } catch (_) { /* Keep plain text. */ }

        }
        const pre = card.querySelector('pre');
        const inputLabel = document.createElement('span');
        inputLabel.dataset.i18n = 'input';
        inputLabel.textContent = OrionI18n.t('input');
        pre.replaceChildren(inputLabel, document.createTextNode('\n' + JSON.stringify(args, null, 2)));
        if (result !== undefined) {
            const resultLabel = document.createElement('span');
            resultLabel.dataset.i18n = 'result';
            resultLabel.textContent = OrionI18n.t('result');
            pre.append(document.createTextNode('\n\n'), resultLabel, document.createTextNode('\n' +
                (typeof result === 'string' ? result : JSON.stringify(result,
                    (key, value) => key === 'truncated' ? undefined : value, 2))));
        }

        const needsBrowser = result?.status === 'not_configured';



        let statusKey = 'status' + item.status.charAt(0).toUpperCase() + item.status.slice(1);
        let cardStatus = item.status;
        if (item.status === 'completed' && item.result !== undefined) {
            const r = result;
            if (needsBrowser) {
                statusKey = 'statusBrowserRequired';
                cardStatus = 'not_configured';
            } else if (r?.status === 'tab_missing') {
                statusKey = 'statusTabMissing';
                cardStatus = 'warning';
            } else if (r?.status === 'verification_required' || r?.status === 'interaction_required') {
                statusKey = 'statusVerification';
                cardStatus = 'warning';
            } else if (r?.status === 'timeout') {
                statusKey = 'statusTimeout';
                cardStatus = 'error';
            } else if (r?.status === 'blocked') {
                statusKey = 'statusBlocked';
                cardStatus = 'error';
            } else if (r?.status === 'busy') {
                statusKey = 'statusBusy';
                cardStatus = 'warning';
            } else if (r?.status === 'provider_error' || r?.status === 'error' || r?.error) {
                statusKey = 'statusError';
                cardStatus = 'error';
            }
        }
        let displayStatus = OrionI18n.t(statusKey) || statuses[item.status] || item.status;
        card.dataset.statusKey = statusKey;
        card.dataset.status = cardStatus;
        card.dataset.toolId = item.name;
        card.dataset.statusText = displayStatus;

        card.querySelector('summary').textContent = `${this.toolName(item.name) || item.label || item.name} · ${displayStatus}`;

        if (!this.catalog && !this.catalogPromise) this.loadCatalog().catch(() => {});



        let hint = target.querySelector(`.tool-browser-owner-hint[data-call-key="${key}"]`);
        if (needsBrowser) {
            if (!hint) {
                hint = document.createElement('p');
                hint.className = 'tool-browser-owner-hint setting-hint';
                hint.dataset.callKey = key;
                card.insertAdjacentElement('afterend', hint);
            }
            hint.textContent = OrionI18n.t('browserOwnerOnly');
        } else {
            hint?.remove();
        }
    },

    liveActivity(chatId, item) {

        UI.finishThinking(chatId);

        const state = UI._getOrCreateChatState(chatId);



        // Ensure botDiv is positioned AFTER the latest user message

        const lastUserMsg = UI.chatArea?.querySelector('.user-message-group:last-of-type, .message.user:last-of-type');

        if (state.botDiv && lastUserMsg && (state.botDiv.compareDocumentPosition(lastUserMsg) & Node.DOCUMENT_POSITION_FOLLOWING)) {

            state.botDiv = null;

            state.contentNodes = [];

            state.currentContentNode = null;

        }



        if (item.status === 'running') state.currentContentNode = null;

        if (!state.botDiv) UI.createBotMessagePlaceholder(chatId);

        if (state.botDiv.classList.contains('typing')) { state.botDiv.replaceChildren(); state.botDiv.classList.remove('typing'); }

        this.renderActivity(state.botDiv, item);

        if (chatId === AppState.currentChatId) {

            UI.scrollToBottom();

        }

    }

};

window.ToolsUI = ToolsUI;

document.addEventListener('DOMContentLoaded', () => ToolsUI.init());
