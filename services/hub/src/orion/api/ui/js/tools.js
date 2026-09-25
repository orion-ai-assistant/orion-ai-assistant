/* Server-owned tool catalog; the dashboard only selects registered tools. */
const ToolsUI = {
    draft: null,
    revision: 0,
    resetDraft() { this.draft = null; this.revision++; this.dialog?.close(); },
    draftPayload() { return this.draft ? structuredClone(this.draft) : null; },
    async request(url, method = 'GET', body) {
        const response = await API._fetch(url, {
            method, headers: {'Content-Type': 'application/json'},
            ...(body === undefined ? {} : {body: JSON.stringify(body)})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
        return data;
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
    },
    renderSettings() {
        const dashboard = document.getElementById('settings-dashboard');
        if (!dashboard || dashboard.querySelector('.tool-settings-card')) return;
        const card = document.createElement('section');
        card.className = 'settings-card tool-settings-card';
        card.innerHTML = '<h3>Varsayılan fonksiyonlar</h3><p>Özel seçim yapılmamış sohbetlerde kullanılacak fonksiyonları belirleyin.</p><button type="button" class="btn btn-primary">Fonksiyonları düzenle</button>';
        card.querySelector('button').onclick = () => this.open(true);
        (document.getElementById('settings-panel-general') || dashboard).appendChild(card);
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
                const children = document.createElement('fieldset'); children.className = 'tools-functions';
                children.disabled = !selected.categories[cat.id];
                for (const fn of cat.functions) {
                    const functionRow = document.createElement('label'); functionRow.className = 'tool-row';
                    const text = document.createElement('span'); text.className = 'tool-row-copy';
                    const name = document.createElement('strong'); name.textContent = fn.name;
                    const fnDescription = document.createElement('small'); fnDescription.textContent = fn.description;
                    text.append(name, fnDescription); functionRow.appendChild(text);
                    const input = this.toggle(fn.name, selected.functions[fn.id], value => {
                        selected.functions[fn.id] = value;
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
                const toggle = this.toggle(cat.name + ' kategorisi', selected.categories[cat.id], value => {
                    selected.categories[cat.id] = value;
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
        card.querySelector('summary').textContent = `${item.label || item.name} · ${statuses[item.status] || item.status}`;
        let args = item.arguments;
        try { args = JSON.parse(args); } catch (_) { /* Show malformed arguments verbatim. */ }
        card.querySelector('pre').textContent = 'Girdi\n' + JSON.stringify(args, null, 2) +
            (item.result === undefined ? '' : '\n\nSonuç\n' + JSON.stringify(item.result, null, 2));
    },
    liveActivity(chatId, item) {
        UI.finishThinking(chatId);
        const state = UI._getOrCreateChatState(chatId);
        state.currentContentNode = null;
        if (!state.botDiv) UI.createBotMessagePlaceholder(chatId);
        if (state.botDiv.classList.contains('typing')) { state.botDiv.replaceChildren(); state.botDiv.classList.remove('typing'); }
        this.renderActivity(state.botDiv, item);
    }
};
window.ToolsUI = ToolsUI;
document.addEventListener('DOMContentLoaded', () => ToolsUI.init());
