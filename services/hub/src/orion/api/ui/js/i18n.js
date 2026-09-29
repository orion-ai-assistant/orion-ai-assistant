/* Text owned by the web client. API-owned names come from the tool catalog. */
const OrionI18n = {
    languages: Object.keys(window.orionUiLocales),
    normalize(value) {
        const tag = String(value || '').toLowerCase().replace('_', '-');
        return this.languages.includes(tag) ? tag :
            (this.languages.includes(tag.split('-')[0]) ? tag.split('-')[0] : null);
    },
    get language() {
        return this.normalize(localStorage.getItem('orion_ui_language')) ||
            (navigator.languages || [navigator.language]).map(lang => this.normalize(lang)).find(Boolean) || 'en';
    },
    has(key) {
        return Object.hasOwn(window.orionUiLocales[this.language] || {}, key) ||
            Object.hasOwn(window.orionUiLocales.en || {}, key);
    },
    t(key, ...values) {
        let message = window.orionUiLocales[this.language]?.[key] ?? window.orionUiLocales.en?.[key] ?? key;
        values.forEach((value, index) => { message = message.replace(`{${index}}`, String(value)); });
        return message;
    },
    setLanguage(value) {
        const language = this.normalize(value);
        if (!language) return;
        localStorage.setItem('orion_ui_language', language);
        this.apply();
        if (typeof UI !== 'undefined' && UI.currentSettings) {
            const dashboard = document.getElementById('settings-dashboard');
            if (dashboard) dashboard.innerHTML = '';
            UI.renderSettings(UI.currentSettings);
            UI.loadModelChoices(UI.currentSettings, true);
        }
        if (typeof ToolsUI !== 'undefined') {
            ToolsUI.catalog = null;
            ToolsUI.catalogPromise = null;
            ToolsUI.renderSettings();
            ToolsUI.loadCatalog();
        }
    },
    apply() {
        document.documentElement.lang = this.language;
        document.querySelectorAll('[data-i18n]').forEach(node => { node.textContent = this.t(node.dataset.i18n); });
        document.querySelectorAll('[data-i18n-title]').forEach(node => { node.title = this.t(node.dataset.i18nTitle); });
        document.querySelectorAll('[data-i18n-placeholder]').forEach(node => { node.placeholder = this.t(node.dataset.i18nPlaceholder); });
        document.querySelectorAll('[data-i18n-aria-label]').forEach(node => { node.setAttribute('aria-label', this.t(node.dataset.i18nAriaLabel)); });
        const select = document.getElementById('ui-language');
        if (select) select.value = this.language;
    }
};
document.addEventListener('DOMContentLoaded', () => OrionI18n.apply());
