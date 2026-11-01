const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/hub_picker.js'), 'utf8');
const selfId = '12345678-1234-4321-8123-123456789abc';
const peerId = '00000000-0000-4000-8000-000000000001';
const peerUrl = 'https://192.168.1.30:8910/';

function setup({saved, expected = '', invitation = false, failSelf = false, holdScan = false, peerAddress = peerUrl, local = true} = {}) {
    const storage = new Map(saved ? [['orion_last_hub', saved]] : []);
    const nodes = new Map();
    // The picker is a <dialog>: showModal/close set its open state like a browser does.
    function element() {
        const classes = new Set();
        return {children: [], append(...children) {this.children.push(...children);}, replaceChildren() {this.children = [];}, setAttribute() {}, disabled: false,
            dataset: {}, classList: {add(name) {classes.add(name);}, remove(name) {classes.delete(name);}, contains(name) {return classes.has(name);}},
            open: false, showModal() { this.open = true; }, close() { this.open = false; }};
    }
    const calls = [];
    // A held scan stays pending until release(): init must not wait for it.
    let release = () => {};
    const scan = holdScan ? new Promise(done => {release = done;}) : null;
    const context = {
        URL, URLSearchParams, AbortSignal,
        Auth: {tokenKey: 'orion_token'},
        document: {getElementById(id) { if (!nodes.has(id)) nodes.set(id, {...element(), textContent: '', value: ''}); return nodes.get(id); }, createElement: element},
        localStorage: {getItem(key) { return storage.get(key) || null; }, setItem(key, value) { storage.set(key, value); }, removeItem(key) {storage.delete(key);}},
        location: {origin: local ? 'http://localhost' : 'http://192.168.1.20:8910', hostname: local ? 'localhost' : '192.168.1.20', search: expected ? '?hub=' + expected : '', assign(url) { this.assigned = url; }},
        // The 10 s background rescan and the manual-refresh minimum delay are not under test.
        setInterval: () => 0, clearInterval() {}, setTimeout(callback) { callback(); return 0; }, clearTimeout() {},
        fetch: async url => {
            calls.push(url);
            if (url.endsWith('/hubs') && scan) await scan;
            return {ok: !failSelf || !url.endsWith('/self'), json: async () => url.endsWith('/self') ?
                {id: selfId, name: 'Hiro', local_entry: local} : {
                hubs: [{id: selfId, name: 'Hiro', url: '/hub'}, {id: peerId, name: 'Office', url: peerAddress}]
            }};
        }
    };
    vm.createContext(context);
    vm.runInContext(source + '\nglobalThis.picker = HubPicker;', context);
    // init starts a background scan; wait for it so later steps see a settled picker.
    const settle = async () => { while (context.picker.busy) await new Promise(resolve => setImmediate(resolve)); };
    return {context, storage, nodes, calls, settle, release: () => release(), init: () => context.picker.init({invitation})};
}

test('First visit selects this device immediately without waiting for a scan', async () => {
    const {context, calls, nodes, storage, init, release} = setup({holdScan: true});
    await init();
    assert.deepEqual(calls, ['/api/v1/discovery/self', '/api/v1/discovery/hubs'], 'only a background scan, never a peer lookup');
    assert.equal(context.picker.busy, true, 'init returned while the scan is still pending');
    assert.equal(nodes.get('auth-login-btn').disabled, false);
    assert.equal(nodes.get('hub-current-name').textContent, 'Hiro');
    assert.equal(context.Auth.tokenKey, `orion_token:${selfId}`);
    assert.equal(nodes.get('hub-picker').open, false);
    assert.equal(storage.get('orion_last_hub'), undefined);
    context.picker.rememberSuccess();
    assert.equal(storage.get('orion_last_hub'), selfId);
    release();
});

test('Choosing another Hub opens its own HTTPS page and never carries over this Hub’s token or password', async () => {
    const {context, storage, nodes, init, settle} = setup({saved: selfId});
    await init();
    await settle();
    storage.set(`orion_token:${selfId}`, 'own-token');
    nodes.get('auth-password').value = 'password-for-self';
    nodes.get('hub-refresh').onclick();
    assert.equal(nodes.get('hub-picker').open, true);
    await settle();
    assert.equal(context.picker.records[1].id, peerId);
    await context.picker.choose(context.picker.records[1]);
    assert.equal(context.location.assigned, `${peerUrl}?hub=${peerId}`);
    assert.equal(nodes.get('auth-password').value, '');
    assert.equal(context.picker.connected, false, 'this page stops offering login once it leaves');
    assert.equal(nodes.get('auth-login-btn').disabled, true);
    assert.equal(context.Auth.tokenKey, `orion_token:${selfId}`, 'the token namespace is never switched to the other Hub');
    assert.equal(context.picker.apiPath('/api/v1/auth/login'), '/api/v1/auth/login', 'no API call is redirected to the other Hub from this page');
    assert.equal(storage.get('orion_last_hub'), selfId, 'selection alone must not replace last successful login');
    context.picker.rememberSuccess();
    assert.equal(storage.get('orion_last_hub'), selfId);
    assert.equal(storage.get(`orion_token:${peerId}`), undefined);
    await context.picker.choose(context.picker.records[0]);
    assert.equal(context.picker.connected, true);
    assert.equal(context.picker.apiPath('/api/v1/auth/me'), '/api/v1/auth/me');
});

test('A Hub advertised without HTTPS, or with credentials in its URL, is refused', async () => {
    for (const peerAddress of ['http://192.168.1.30:8910/', 'https://user:pass@192.168.1.30:8910/']) {
        const {context, nodes, init, settle} = setup({peerAddress});
        await init();
        await settle();
        nodes.get('auth-password').value = 'typed';
        await context.picker.choose(context.picker.records[1]);
        assert.equal(context.location.assigned, undefined);
        assert.equal(nodes.get('hub-status').textContent, 'Hub’ın HTTPS adresi geçersiz.');
        assert.equal(context.picker.selected.id, selfId);
        assert.equal(context.picker.connected, true);
        assert.equal(context.Auth.tokenKey, `orion_token:${selfId}`);
    }
});

test('Missing saved Hub blocks login rather than silently switching to this device', async () => {
    const {context, nodes, storage, init, settle} = setup({saved: 'offline-id'});
    await init();
    await settle();
    assert.equal(context.picker.connected, false);
    assert.equal(nodes.get('auth-login-btn').disabled, true);
    assert.equal(nodes.get('hub-current-name').textContent, 'Bulunamadı');
    assert.equal(nodes.get('hub-picker').open, true, 'the picker opens so another Hub can be chosen');
    assert.equal(context.location.assigned, undefined);
    assert.equal(context.Auth.tokenKey, 'orion_token');
    assert.equal(storage.get('orion_last_hub'), 'offline-id');
    await context.picker.choose({id: selfId, name: 'Hiro', url: '/hub'});
    assert.equal(context.picker.connected, true);
    assert.equal(nodes.get('hub-picker').open, false);
});

test('Saved UUID resolves the Hub’s current address and opens it there', async () => {
    const {context, calls, init} = setup({saved: peerId});
    await init();
    assert.equal(context.location.assigned, `${peerUrl}?hub=${peerId}`);
    assert.equal(context.picker.connected, false);
    assert.equal(context.Auth.tokenKey, 'orion_token');
    assert.equal(calls[1], '/api/v1/discovery/hubs');
    assert(!calls.some(url => url.includes('/peers/')));
});

test('Invitation is pinned to its Hub and cannot transfer its token', async () => {
    const {context, init} = setup({saved: peerId, invitation: true});
    await init();
    await context.picker.choose({id: peerId, name: 'Office', url: peerUrl});
    assert.equal(context.location.assigned, undefined);
    assert.equal(context.picker.selected.id, selfId);
    assert.equal(context.Auth.tokenKey, `orion_token:${selfId}`);
});

test('Wrong claimed UUID never enables credentials or changes token namespace', async () => {
    for (const options of [{expected: 'wrong-id'}, {failSelf: true}, {saved: peerId, peerAddress: 'http://192.168.1.30:8910/'}]) {
        const {context, nodes, init, settle} = setup(options);
        await init();
        await settle();
        assert.equal(context.picker.connected, false);
        assert.equal(nodes.get('auth-login-btn').disabled, true);
        assert.equal(context.Auth.tokenKey, 'orion_token');
        assert.equal(context.location.assigned, undefined);
    }
});

test('Remembered local login stays fast; refresh/close do not override an explicit choice', async () => {
    const {context, nodes, init, settle} = setup({saved: peerId});
    await init();
    await settle();
    context.location.assigned = undefined;
    await context.picker.choose(context.picker.records[0]);
    nodes.get('hub-refresh').onclick();
    await settle();
    assert.equal(context.picker.selected.id, selfId);
    assert.equal(context.location.assigned, undefined, 'a rescan does not jump back to the saved Hub');
    assert.equal(nodes.get('hub-refresh').disabled, false);
    assert.equal(nodes.get('hub-refresh').classList.contains('refreshing'), false);
    const fast = setup({saved: selfId, holdScan: true});
    await fast.init();
    assert.deepEqual(fast.calls, ['/api/v1/discovery/self', '/api/v1/discovery/hubs']);
    assert.equal(fast.nodes.get('auth-login-btn').disabled, false);
    fast.release();
    await fast.settle();
    fast.nodes.get('hub-refresh').onclick();
    assert.equal(fast.nodes.get('hub-picker').open, true);
    await fast.settle();
    fast.nodes.get('hub-picker-close').onclick();
    assert.equal(fast.nodes.get('hub-picker').open, false);
});

test('Ambiguous discovery identity cannot be selected', async () => {
    const {context, init} = setup();
    await init();
    await context.picker.choose({id: peerId, name: 'Clone', url: peerUrl, conflict: true});
    assert.equal(context.picker.selected.id, selfId);
    assert.equal(context.location.assigned, undefined);
});

test('Late saved-Hub lookup cannot override the most recent explicit choice', async () => {
    const {context, calls, init, release} = setup({saved: peerId, holdScan: true});
    const pending = init();
    while (!calls.includes('/api/v1/discovery/hubs')) await new Promise(resolve => setImmediate(resolve));
    await context.picker.choose(context.picker.records[0]);
    release();
    await pending;
    assert.equal(context.location.assigned, undefined);
    assert.equal(context.picker.selected.id, selfId);
    assert.equal(context.Auth.tokenKey, `orion_token:${selfId}`);
});
