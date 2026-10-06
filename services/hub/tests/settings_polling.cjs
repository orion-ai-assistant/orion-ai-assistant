const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/main.js'), 'utf8');
let visible = false, requests = 0, renders = 0, catalogs = 0, resolve;
const savedSettings = {};
const refreshButton = {};
const catalogModes = [];
const context = vm.createContext({
    document: { hidden: false, getElementById: id => id === 'settings-refresh' ? refreshButton : ({ classList: { contains: () => visible } }), addEventListener() {} },
    DevicePairing: {renderControls() {}}, window: {}, Auth: { getToken: () => 'test' }, AppState: { sseConnected: true },
    API: { getSettings: () => { requests++; return new Promise(r => { resolve = r; }); } },
    UI: { renderSettings: () => renders++, loadModelChoices: async (_, silent) => { catalogs++; catalogModes.push(silent); } },
    localStorage: {setItem: (key, value) => { savedSettings[key] = value; }},
    setInterval: (_, delay) => assert.equal(delay, 5000),
});
vm.runInContext(source.slice(source.indexOf('    var settingsLoading'), source.indexOf('    let userPollTimer')), context);
vm.runInContext('globalThis.poll = loadInitialSettings;', context);
async function run() {
    const poll = context.poll;
    await poll();
    assert.equal(requests, 0, 'No settings requests outside settings');
    const initial = context.window.loadInitialSettings();
    assert.equal(requests, 1, 'Initial load runs even outside settings');
    resolve({ tts_enabled: true });
    await initial;
    assert.equal(renders, 1);
    assert.equal(savedSettings.orion_tts_enabled, 'true', 'Settings sync audio preference without a chat toggle');
    await context.window.loadInitialSettings();
    await poll();
    assert.equal(requests, 1, 'Initial background load runs only once');
    visible = true;
    const pending = poll();
    await poll();
    assert.equal(requests, 2, 'Do not overlap requests');
    visible = false;
    resolve({ tts_enabled: true });
    await pending;
    assert.equal(renders, 1, 'Discard response after leaving settings');
    assert.equal(catalogs, 1);
    visible = true;
    const refresh = poll();
    resolve({ tts_enabled: true });
    await refresh;
    assert.equal(renders, 2);
    assert.equal(catalogs, 2);
    context.document.hidden = true;
    await poll();
    assert.equal(requests, 3, 'Hidden browser tabs do not poll');
    context.document.hidden = false;
    const manual = refreshButton.onclick();
    assert.equal(refreshButton.disabled, true, 'Manual refresh disables its button until completion');
    resolve({tts_enabled: true});
    await manual;
    assert.equal(refreshButton.disabled, false);
    assert.equal(catalogModes.at(-1), false, 'Header refresh updates the service catalog explicitly');
    console.log('Settings polling visibility, header refresh and overlap tests passed');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
