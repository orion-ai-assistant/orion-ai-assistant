const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('Invitation redemption on another device appears without pressing Refresh', async () => {
    const listeners = {}, nodes = new Map();
    let tick, data = {public_url: 'http://orion.local', users: [], invitations: []}, requests = 0;
    const element = () => ({children: [], hidden: false, textContent: '', append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children = children; }});
    const backgrounds = [];
    const context = {
        document: {hidden: false, addEventListener(event, callback) { listeners[event] = callback; },
            currentScript: {src: 'http://localhost:8910/panel/owner-assets/base.js'},
            getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); }, createElement: element},
        OwnerGate: {unlocked: true},
        setInterval(callback, delay) { assert.equal(delay, 5000); tick = callback; }, clearInterval() {},
        Date, URL, Headers, navigator: {}, confirm: () => true
    };
    context.window = {addEventListener() {}, OwnerGate: context.OwnerGate};
    vm.createContext(context);
    const owner = path.join(__dirname, '../src/orion/api/owner_ui');
    vm.runInContext(fs.readFileSync(path.join(owner, 'base.js'), 'utf8'), context);
    context.OwnerAPI = context.window.OwnerAPI;
    // Real whileVisible from base.js; only the network is replaced.
    context.OwnerAPI.fetch = async (url, options = {}) => { requests++; backgrounds.push(Boolean(options.background)); return {ok: true, status: 200, json: async () => data}; };
    vm.runInContext(fs.readFileSync(path.join(owner, 'people.js'), 'utf8'), context);
    listeners.DOMContentLoaded();
    listeners['owner-unlocked']();
    await new Promise(resolve => setImmediate(resolve));
    data = {...data, users: [{id: 'new-user', is_active: true, created_at: '2026-10-04T12:00:00Z'}]};
    await tick();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests, 2);
    assert.deepEqual(backgrounds, [false, true], 'automatic refreshes must not count as owner activity');
    assert.equal(nodes.get('people-list').children[0].children[0].children[0].textContent, 'new-user');
    context.document.hidden = true;
    tick();
    assert.equal(requests, 2, 'background tabs should not poll');
    context.document.hidden = false;
    context.OwnerGate.unlocked = false;
    tick();
    assert.equal(requests, 2, 'locked management should not poll');
});

test('An automatic refresh never postpones the owner idle lock', async () => {
    const stored = new Map(), sent = [];
    const context = {
        document: {currentScript: {src: 'http://localhost:8910/panel/owner-assets/base.js'}},
        sessionStorage: {getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key)},
        Headers, Date, URL, JSON
    };
    context.window = {fetch: async (url, options) => { sent.push(options.headers); return {status: 200}; }};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/owner_ui/base.js'), 'utf8'), context);
    const api = context.window.OwnerAPI;
    const expires = Date.now() + 60000;
    stored.set(api.sessionKey, JSON.stringify({token: 'owner', expires}));
    await api.fetch('/api/v1/owner/people', {background: true});
    assert.equal(sent[0].get('X-Orion-Background-Refresh'), '1');
    assert.equal(JSON.parse(stored.get(api.sessionKey)).expires, expires);
    await api.fetch('/api/v1/owner/people');
    assert.equal(sent[1].get('X-Orion-Background-Refresh'), null);
    assert.ok(JSON.parse(stored.get(api.sessionKey)).expires > expires);
});

test('A page restored with Back keeps refreshing itself', async () => {
    const windowListeners = {}, timers = new Set();
    let updates = 0;
    const context = {
        document: {hidden: false, addEventListener() {}, currentScript: {src: 'http://localhost:8910/panel/owner-assets/base.js'}},
        setInterval(callback) { const timer = {callback}; timers.add(timer); return timer; },
        clearInterval(timer) { timers.delete(timer); },
        Date, URL, Headers
    };
    context.window = {addEventListener(event, callback) { windowListeners[event] = callback; }, OwnerGate: {unlocked: true}};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/owner_ui/base.js'), 'utf8'), context);
    context.window.OwnerAPI.whileVisible(async () => { updates++; });
    windowListeners.pagehide();
    assert.equal(timers.size, 0, 'a page that is left stops polling');
    windowListeners.pageshow({persisted: true});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(timers.size, 1);
    assert.equal(updates, 1, 'the restored page shows current data at once');
    windowListeners.pageshow({persisted: true});
    assert.equal(timers.size, 1, 'never two timers');
});
