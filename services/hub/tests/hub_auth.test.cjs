const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/api.js'), 'utf8');

test('A late login cannot install its token under another Hub namespace', async () => {
    const storage = new Map();
    let resolve;
    const context = {HubPicker: {connected: true, apiPath: path => path},
        localStorage: {setItem: (key, value) => storage.set(key, value)},
        fetch: () => new Promise(done => {resolve = done;})};
    vm.runInNewContext(source + ';globalThis.auth = Auth;', context);
    context.auth.tokenKey = 'orion_token:first';
    const pending = context.auth.login('u', 'password-for-first');
    context.auth.tokenKey = 'orion_token:second';
    resolve({ok: true, json: async () => ({access_token: 'first-hub-token'})});
    await assert.rejects(pending, /Hub değişti/);
    assert.equal(storage.size, 0);
});

test('A late unauthorized API response cannot clear a different Hub session', async () => {
    let resolve, cleared = false;
    const context = {HubPicker: {connected: true, apiPath: path => path},
        AppState: {sseConnected: true}, OrionI18n: {language: 'tr'},
        localStorage: {getItem: () => 'first-token'}, fetch: () => new Promise(done => {resolve = done;})};
    vm.runInNewContext(source + ';globalThis.auth = Auth; globalThis.api = API;', context);
    context.auth.tokenKey = 'orion_token:first';
    context.auth.handleUnauthorized = () => {cleared = true;};
    const pending = context.api._fetch('/api/v1/chats');
    context.auth.tokenKey = 'orion_token:second';
    resolve({status: 401});
    await assert.rejects(pending, /Hub değişti/);
    assert.equal(cleared, false);
});
