const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/main.js'), 'utf8');
let visible = false, requests = 0, renders = 0, catalogs = 0, resolve;
const context = vm.createContext({
    document: { hidden: false, getElementById: () => ({ classList: { contains: () => visible } }), addEventListener() {} },
    window: {}, Auth: { getToken: () => 'test' }, AppState: { sseConnected: true },
    API: { getSettings: () => { requests++; return new Promise(r => { resolve = r; }); } },
    UI: { renderSettings: () => renders++, loadModelChoices: async () => catalogs++ },
    audioToggle: null,
    setInterval: (_, delay) => assert.equal(delay, 5000),
});
vm.runInContext(source.slice(source.indexOf('    let settingsLoading'), source.indexOf('    let userPollTimer')), context);
async function run() {
    const poll = context.window.loadInitialSettings;
    await poll();
    assert.equal(requests, 0, 'No settings requests outside settings');
    visible = true;
    const pending = poll();
    await poll();
    assert.equal(requests, 1, 'Do not overlap requests');
    visible = false;
    resolve({ tts_enabled: true });
    await pending;
    assert.equal(renders, 0, 'Discard response after leaving settings');
    assert.equal(catalogs, 0);
    visible = true;
    const refresh = poll();
    resolve({ tts_enabled: true });
    await refresh;
    assert.equal(renders, 1);
    assert.equal(catalogs, 1);
    context.document.hidden = true;
    await poll();
    assert.equal(requests, 2, 'Hidden browser tabs do not poll');
    console.log('Settings polling visibility and overlap tests passed');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
