const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/auth_wait.js'), 'utf8');

test('countdown uses server expiry, survives inactive tab, and unlocks controls', () => {
    let time = 0, callback, cleared = 0;
    const sandbox = {window: {}, Date: {now: () => time, parse: Date.parse}, setInterval: fn => {callback = fn; return 1;}, clearInterval: () => cleared++};
    vm.runInNewContext(source, sandbox);
    const wait = sandbox.window.AuthWait, element = {style: {}}, button = {disabled: false};
    wait.start(element, [button], 30);
    assert.equal(button.disabled, true);
    assert.match(element.textContent, /30 saniye/);
    time = 13000; callback();
    assert.match(element.textContent, /17 saniye/);
    time = 45000; callback();
    assert.equal(button.disabled, false);
    assert.match(element.textContent, /Bekleme süresi doldu/);
    assert.equal(cleared, 1);
});

test('Retry-After seconds and date are honored; missing/invalid uses 30s', () => {
    const sandbox = {window: {}, Date}; vm.runInNewContext(source, sandbox);
    const wait = sandbox.window.AuthWait;
    assert.equal(wait.retrySeconds({headers: {get: () => '12'}}), 12);
    assert.equal(wait.retrySeconds({headers: {get: () => null}}), 30);
    assert.equal(wait.retrySeconds({headers: {get: () => 'invalid'}}), 30);
    const seconds = wait.retrySeconds({headers: {get: () => new Date(Date.now() + 20000).toUTCString()}});
    assert.ok(seconds >= 19 && seconds <= 20);
});

test('invitation registration passes token, saves login token, and clears invite URL', async () => {
    const saved = new Map(); let body, replaced;
    const sandbox = {window: {}, localStorage: {setItem: (key, value) => saved.set(key, value)}, history: {replaceState: (...args) => replaced = args[2]}, location: {pathname: '/dashboard/'}, fetch: async (url, options) => {body = JSON.parse(options.body); return {ok: true, json: async () => ({access_token: 'new-user-token'})};}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/api.js'), 'utf8') + ';globalThis.auth = Auth;', sandbox);
    sandbox.auth.invitationToken = 'invitation';
    await sandbox.auth.register('alice', 'password');
    assert.equal(body.invitation_token, 'invitation');
    assert.equal(saved.get('orion_token'), 'new-user-token');
    assert.equal(replaced, '/dashboard/');
    assert.equal(sandbox.auth.invitationToken, null);
});
