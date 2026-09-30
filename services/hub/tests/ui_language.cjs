const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const values = new Map();
const context = vm.createContext({
    window: {location: {reload() { context.reloaded = true; }}},
    localStorage: {getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value)},
    navigator: {languages: ['tr-TR', 'en-US'], language: 'tr-TR'},
    document: {addEventListener() {}, documentElement: {}, querySelectorAll() { return []; }, getElementById() { return null; }}
});
for (const file of ['locales/en.js', 'locales/tr.js', 'i18n.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js', file), 'utf8'), context);
}
vm.runInContext('globalThis.i18n = OrionI18n;', context);
assert.equal(context.i18n.language, 'tr');
assert.equal(context.i18n.t('tools'), 'Fonksiyonlar');
context.i18n.setLanguage('en-US');
assert.equal(context.i18n.language, 'en');
// Live update without page reload
assert.equal(context.reloaded, undefined);
assert.equal(context.i18n.t('saveFailed', 'offline'), 'Could not save: offline');
const dashboard = {querySelector() { return null; }, innerHTML: ''};
const languageSelect = {value: null};
context.document.getElementById = id => ({'settings-dashboard': dashboard, 'ui-language': languageSelect})[id] || null;
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);
context.ui.renderSettings({});
assert.match(dashboard.innerHTML, /id="ui-language"/);
assert.match(dashboard.innerHTML, /English/);
assert.equal(languageSelect.value, 'en');

// Verify key parity between en and tr locales
const enKeys = Object.keys(context.window.orionUiLocales.en).sort();
const trKeys = Object.keys(context.window.orionUiLocales.tr).sort();
assert.deepEqual(enKeys, trKeys, 'en and tr should have identical keys');

// Verify apply handles data-i18n-placeholder and data-i18n-aria-label
const placeholderNode = {dataset: {i18nPlaceholder: 'messagePlaceholder'}, placeholder: ''};
const ariaLabelNode = {dataset: {i18nAriaLabel: 'add'}, setAttribute(name, val) { this[name] = val; }};
const inputLabel = {dataset: {i18n: 'input'}, textContent: ''};
const resultLabel = {dataset: {i18n: 'result'}, textContent: ''};
context.document.querySelectorAll = selector => {
    if (selector === '[data-i18n]') return [inputLabel, resultLabel];
    if (selector === '[data-i18n-placeholder]') return [placeholderNode];
    if (selector === '[data-i18n-aria-label]') return [ariaLabelNode];
    return [];
};
context.i18n.apply();
assert.equal(placeholderNode.placeholder, 'Type a message or speak into the microphone…');
assert.equal(ariaLabelNode['aria-label'], 'Add');
assert.equal(inputLabel.textContent, 'Input');
assert.equal(resultLabel.textContent, 'Result');
context.ui.currentSettings = null;
context.i18n.setLanguage('tr');
assert.equal(inputLabel.textContent, 'Girdi');
assert.equal(resultLabel.textContent, 'Sonuç');

console.log('Web language selection, placeholder/aria-label binding, and key parity passed');
