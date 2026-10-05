const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/hub_picker.js'), 'utf8');
const selfId = '12345678-1234-4321-8123-123456789abc';
const peerId = '00000000-0000-4000-8000-000000000001';

function setup({saved, expected = '', invitation = false, failSelf = false, wrongPeer = false, local = true} = {}) {
    const storage = new Map(saved ? [['orion_last_hub', saved]] : []);
    const nodes = new Map();
    function element() { return {children: [], append(child) {this.children.push(child);}, replaceChildren() {this.children = [];}, setAttribute() {}, disabled: false}; }
    const calls = [];
    const context = {
        URL, URLSearchParams, AbortSignal,
        Auth: {tokenKey: 'orion_token'},
        document: {getElementById(id) { if (!nodes.has(id)) nodes.set(id, {...element(), hidden: id === 'hub-picker', textContent: id === 'hub-refresh' ? 'Yenile' : '', value: ''}); return nodes.get(id); }, createElement: element},
        localStorage: {getItem(key) { return storage.get(key) || null; }, setItem(key, value) { storage.set(key, value); }, removeItem(key) {storage.delete(key);}},
        location: {origin: local ? 'http://localhost' : 'http://192.168.1.20:8910', hostname: local ? 'localhost' : '192.168.1.20', search: expected ? '?hub=' + expected : '', assign(url) { this.assigned = url; }},
        fetch: async url => {
            calls.push(url);
            const peer = url.includes('/peers/');
            return {ok: !failSelf || !url.endsWith('/self'), json: async () => url.endsWith('/self') ?
                {id: peer ? (wrongPeer ? selfId : peerId) : selfId, name: peer ? 'Office' : 'Hiro', local_entry: !peer && local} : {
                hubs: [{id: selfId, name: 'Hiro', url: '/hub'}, {id: peerId, name: 'Office', url: 'http://192.168.1.30:8910/'}]
            }};
        }
    };
    vm.createContext(context);
    vm.runInContext(source + '\nglobalThis.picker = HubPicker;', context);
    return {context, storage, nodes, calls, init: () => context.picker.init({invitation})};
}

test('First visit selects this device immediately without waiting for a scan', async () => {
    const {context, calls, nodes, storage, init} = setup();
    await init();
    assert.deepEqual(calls, ['/api/v1/discovery/self']);
    assert.equal(nodes.get('auth-login-btn').disabled, false);
    assert.equal(nodes.get('hub-current-name').textContent, 'Hiro · Bu cihaz');
    assert.equal(nodes.get('hub-picker').hidden, true);
    assert.equal(storage.get('orion_last_hub'), undefined);
    context.picker.rememberSuccess();
    assert.equal(storage.get('orion_last_hub'), selfId);
});

test('Local Hub switching preserves the local page and namespaces all APIs/tokens', async () => {
    const {context, storage, nodes, init} = setup({saved: selfId});
    await init();
    storage.set(`orion_token:${selfId}`, 'own-token');
    nodes.get('auth-password').value = 'password-for-self';
    await nodes.get('hub-change').onclick();
    await context.picker.choose(context.picker.records[1]);
    assert.equal(context.location.assigned, undefined);
    assert.equal(context.Auth.tokenKey, `orion_token:${peerId}`);
    assert.equal(nodes.get('auth-password').value, '');
    assert.equal(context.picker.apiPath('/api/v1/auth/login'), `/api/v1/peers/${peerId}/auth/login`);
    assert.equal(context.picker.apiPath('/api/v1/chat/stream?token=t'), `/api/v1/peers/${peerId}/chat/stream?token=t`);
    assert.equal(storage.get('orion_last_hub'), selfId, 'selection alone must not replace last successful login');
    context.picker.rememberSuccess();
    assert.equal(storage.get('orion_last_hub'), peerId);
    assert.equal(storage.get(`orion_token:${peerId}`), undefined);
    await context.picker.choose(context.picker.records[0]);
    assert.equal(context.picker.apiPath('/api/v1/auth/me'), '/api/v1/auth/me');
});

test('Missing saved Hub blocks login rather than silently switching to this device', async () => {
    const {context, nodes, storage, init} = setup({saved: 'offline-id'});
    await init();
    assert.equal(context.picker.connected, false);
    assert.equal(nodes.get('auth-login-btn').disabled, true);
    assert.equal(context.location.assigned, undefined);
    assert.equal(storage.get('orion_last_hub'), 'offline-id');
    await context.picker.choose({id: selfId, name: 'Hiro', url: '/hub'});
    assert.equal(context.picker.connected, true);
});

test('Saved UUID resolves current IP and keeps the browser at localhost', async () => {
    const {context, calls, init} = setup({saved: peerId});
    await init();
    assert.equal(context.location.assigned, undefined);
    assert.equal(context.picker.selected.id, peerId);
    assert.equal(context.picker.connected, true);
    assert.equal(calls.at(-1), `/api/v1/peers/${peerId}/discovery/self`);
});

test('Invitation is pinned to its Hub and cannot transfer its token', async () => {
    const {context, init} = setup({saved: peerId, invitation: true});
    await init();
    await context.picker.choose({id: peerId, name: 'Office', url: 'http://192.168.1.30:8910/'});
    assert.equal(context.location.assigned, undefined);
    assert.equal(context.picker.selected.id, selfId);
    assert.equal(context.Auth.tokenKey, `orion_token:${selfId}`);
});

test('Wrong claimed UUID never enables credentials or changes token namespace', async () => {
    for (const options of [{expected: 'wrong-id'}, {failSelf: true}, {saved: peerId, wrongPeer: true}]) {
        const {context, nodes, init} = setup(options);
        await init();
        assert.equal(context.picker.connected, false);
        assert.equal(nodes.get('auth-login-btn').disabled, true);
        assert.equal(context.Auth.tokenKey, 'orion_token');
    }
});

test('Remembered local login stays fast; refresh/change do not override an explicit choice', async () => {
    const {context, nodes, calls, init} = setup({saved: peerId});
    await init();
    await context.picker.choose(context.picker.records[0]);
    await nodes.get('hub-refresh').onclick();
    assert.equal(context.picker.selected.id, selfId);
    assert.equal(nodes.get('hub-refresh').textContent, 'Yenile');
    const fast = setup({saved: selfId});
    await fast.init();
    assert.deepEqual(fast.calls, ['/api/v1/discovery/self']);
    await fast.nodes.get('hub-change').onclick();
    assert.equal(fast.nodes.get('hub-picker').hidden, false);
    await fast.nodes.get('hub-change').onclick();
    assert.equal(fast.nodes.get('hub-picker').hidden, true);
});

test('Ambiguous discovery identity cannot be selected', async () => {
    const {context, init} = setup();
    await init();
    await context.picker.choose({id: peerId, name: 'Clone', conflict: true});
    assert.equal(context.picker.selected.id, selfId);
});

test('Late peer lookup cannot override the most recent explicit choice', async () => {
    const {context, init} = setup();
    await init();
    let resolve;
    context.fetch = () => new Promise(done => {resolve = done;});
    const pending = context.picker.choose({id: peerId, name: 'Other'});
    await context.picker.choose({id: selfId, name: 'Hiro'});
    resolve({ok: true, json: async () => ({id: peerId})});
    await pending;
    assert.equal(context.picker.selected.id, selfId);
    assert.equal(context.Auth.tokenKey, `orion_token:${selfId}`);
});
