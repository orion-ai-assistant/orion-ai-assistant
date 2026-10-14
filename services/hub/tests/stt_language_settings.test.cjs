const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const base = path.join(__dirname, '../src/orion/api/ui/js');
const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../../stt/whisper/stt_languages.json'), 'utf8'));

function setup(browserLanguage, saved = null) {
    const classes = new Set();
    const select = {
        value: '', dataset: {}, options: [], tagName: 'SELECT', validity: {valid: true},
        classList: {contains: name => classes.has(name), add: name => classes.add(name),
            toggle(name, on) {if (on) classes.add(name); else classes.delete(name);}},
        replaceChildren(...options) {this.options = options;}, add(option) {this.options.push(option);},
    };
    const elements = {'setting-input-stt_language': select};
    const requests = [];
    let fetches = 0;
    const sandbox = {
        window: {}, navigator: {language: browserLanguage}, Intl,
        document: {getElementById: id => elements[id] || null},
        Option: function(label, value) {this.text = label; this.value = value;},
        OrionI18n: {language: 'tr', t: key => key}, console, clearTimeout,
        Auth: {tokenKey: 'test'},
        API: {_fetch: async url => {fetches++; assert.equal(url, '/api/v1/audio/languages'); return {ok: true, json: async () => catalog};}, saveSettings: async (key, value) => {requests.push({key, value}); return {[key]: value};}},
    };
    vm.runInNewContext(fs.readFileSync(path.join(base, 'stt.js'), 'utf8'), sandbox);
    vm.runInNewContext(fs.readFileSync(path.join(base, 'ui.js'), 'utf8') + '\nwindow.UI = UI;', sandbox);
    const ui = sandbox.window.UI;
    ui.currentSettings = {stt_language: saved};
    ui.showToast = () => {};
    return {ui, select, requests, classes, get fetches() {return fetches;}};
}

test('settings show all 100 languages and supported browser language initially', async () => {
    const h = setup('tr-TR');
    await h.ui.loadSttLanguageChoices();
    assert.equal(h.select.options.length, 101);
    assert.equal(h.select.value, 'tr');
    assert.equal(h.select.dataset.original, 'tr');
    assert.equal(h.select.options[0].value, 'auto');
    await h.ui.loadSttLanguageChoices();
    assert.equal(h.fetches, 1, 'the shared catalog is loaded once');
});

test('unsupported browser defaults to auto, while saved choices are preserved', async () => {
    const unsupported = setup('xx-ZZ');
    await unsupported.ui.loadSttLanguageChoices();
    assert.equal(unsupported.select.value, 'auto');
    for (const language of ['en', 'auto']) {
        const h = setup('tr-TR', language);
        await h.ui.loadSttLanguageChoices();
        assert.equal(h.select.value, language);
    }
});

test('polling preserves unsaved language edits and explicit auto is saved', async () => {
    const h = setup('tr-TR');
    await h.ui.loadSttLanguageChoices();
    h.select.value = 'auto';
    h.classes.add('dirty');
    await h.ui.loadSttLanguageChoices();
    assert.equal(h.select.value, 'auto');
    await h.ui.saveSetting('stt_language');
    assert.deepEqual(h.requests, [{key: 'stt_language', value: 'auto'}]);
    assert.equal(h.ui.currentSettings.stt_language, 'auto');
    await h.ui.loadSttLanguageChoices();
    assert.equal(h.select.value, 'auto');
});


test('offline STT preserves a saved language and retries the catalog', async () => {
    const h = setup('tr-TR', 'en');
    h.ui.currentSettings.stt_language = 'en';
    // A saved preference must remain usable even when capabilities are unavailable.
    const script = fs.readFileSync(path.join(base, 'stt.js'), 'utf8');
    let attempts = 0;
    const sandbox = {window: {}, navigator: {language: 'tr-TR'}, Auth: {tokenKey: 'hub-a'},
        API: {_fetch: async () => {attempts++; throw new Error('offline');}},
        console: {warn() {}}};
    vm.runInNewContext(script, sandbox);
    const stt = sandbox.window.STT;
    const empty = await stt.loadLanguageCatalog();
    assert.equal(stt.resolveLanguage('en', empty), 'en');
    assert.equal(stt.resolveLanguage('auto', empty), 'auto');
    assert.equal(stt.resolveLanguage(null, empty), 'auto');
    await stt.loadLanguageCatalog();
    assert.equal(attempts, 2);
});

test('switching Hub reloads the language metadata', async () => {
    let attempts = 0;
    const sandbox = {window: {}, navigator: {language: 'tr-TR'}, Auth: {tokenKey: 'hub-a'},
        API: {_fetch: async () => {attempts++; return {ok: true, json: async () => catalog};}}, console};
    vm.runInNewContext(fs.readFileSync(path.join(base, 'stt.js'), 'utf8'), sandbox);
    await sandbox.window.STT.loadLanguageCatalog();
    sandbox.Auth.tokenKey = 'hub-b';
    await sandbox.window.STT.loadLanguageCatalog();
    assert.equal(attempts, 2);
});
