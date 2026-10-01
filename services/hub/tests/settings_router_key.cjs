const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const classes = new Set();
const input = {
    value: '', type: 'password', dataset: {original: ''}, validity: {valid: true},
    classList: {
        contains: name => classes.has(name),
        add: name => classes.add(name),
        toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); },
    },
};
let settingsVisible = true;
const elements = {
    'setting-input-router_api_key': input,
    'setting-secret-icon-router_api_key': {textContent: ''},
    'setting-secret-clear-router_api_key': {hidden: true},
    'settings-dashboard': {querySelector: () => input},
    'settings-view': {classList: {contains: () => settingsVisible}},
};
const requests = [];
const context = vm.createContext({
    window: {},
    document: {activeElement: null, getElementById: id => elements[id] || null},
    OrionI18n: {t: key => key},
    clearTimeout() {},
    API: {saveSettings: (key, value) => new Promise(resolve => requests.push({key, value, resolve}))},
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);
const ui = context.ui;
ui.showToast = () => {};
async function tick() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
async function run() {
    input.value = 'test-key-first';
    ui.toggleSecretVisibility('router_api_key');
    const first = ui.saveSetting('router_api_key');
    await tick();
    input.value = 'test-key-edited';
    requests[0].resolve({router_api_key: 'masked-first'});
    await first;
    assert.equal(input.value, 'test-key-edited', 'An older save preserves newer typing');
    assert.ok(classes.has('dirty'));
    const second = ui.saveSetting('router_api_key');
    await tick();
    assert.equal(requests[1].value, 'test-key-edited');
    requests[1].resolve({router_api_key: 'masked-edited'});
    await second;
    assert.equal(input.value, 'test-key-edited', 'Saved key remains editable');
    assert.equal(input.dataset.original, 'test-key-edited');
    assert.equal(input.type, 'text', 'Save preserves eye button state');
    ui.renderSettings({router_api_key: 'masked-edited'});
    assert.equal(input.value, 'test-key-edited', 'Polling after blur preserves the key');
    ui.toggleSecretVisibility('router_api_key');
    assert.equal(input.type, 'password');
    assert.equal(input.value, 'test-key-edited');
    await ui.saveSetting('router_api_key');
    assert.equal(requests.length, 2, 'Unchanged key is not saved again');
    settingsVisible = false;
    ui.clearSavedRouterApiKeyInput();
    assert.equal(input.value, '', 'Leaving settings clears the saved input');
    assert.equal(ui.currentSettings.router_api_key, 'masked-edited', 'Leaving does not delete the stored key');
    input.value = 'test-key-pending';
    const pending = ui.saveSetting('router_api_key');
    await tick();
    ui.clearSavedRouterApiKeyInput();
    assert.equal(input.value, 'test-key-pending', 'Leaving does not discard an in-flight edit');
    requests[2].resolve({router_api_key: 'masked-pending'});
    await pending;
    assert.equal(input.value, '', 'A save completing after leaving clears the input');
    console.log('Router key retention, visibility, polling, concurrent edits and exit checks passed');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
