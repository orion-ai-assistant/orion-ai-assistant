const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('Invitation redemption on another device appears without pressing Refresh', async () => {
    const listeners = {}, nodes = new Map();
    let tick, data = {public_url: 'http://orion.local', users: [], invitations: []}, requests = 0;
    const element = () => ({children: [], hidden: false, textContent: '', append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children = children; }});
    const context = {
        document: {hidden: false, addEventListener(event, callback) { listeners[event] = callback; },
            getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); }, createElement: element},
        window: {addEventListener() {}},
        OwnerGate: {unlocked: true},
        OwnerAPI: {fetch: async () => { requests++; return {ok: true, status: 200, json: async () => data}; }},
        setInterval(callback, delay) { assert.equal(delay, 5000); tick = callback; }, clearInterval() {},
        Date, URL, navigator: {}, confirm: () => true
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/owner_ui/people.js'), 'utf8'), context);
    listeners.DOMContentLoaded();
    listeners['owner-unlocked']();
    await new Promise(resolve => setImmediate(resolve));
    data = {...data, users: [{id: 'new-user', is_active: true, created_at: '2026-10-04T12:00:00Z'}]};
    tick();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests, 2);
    assert.equal(nodes.get('people-list').children[0].children[0].children[0].textContent, 'new-user');
    context.document.hidden = true;
    tick();
    assert.equal(requests, 2, 'background tabs should not poll');
    context.document.hidden = false;
    context.OwnerGate.unlocked = false;
    tick();
    assert.equal(requests, 2, 'locked management should not poll');
});
