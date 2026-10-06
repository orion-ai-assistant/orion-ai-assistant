const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const values = new Map(), requests = [];
let now = 1000;
const storage = {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
const context = {URL, Headers, Date: {now: () => now}, sessionStorage: storage,
    document: {currentScript: {src: 'http://localhost:8910/panel/owner-assets/base.js'}},
    window: {fetch: async (url, options) => requests.push({url, headers: options.headers})}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/owner_ui/base.js'), 'utf8'), context);
(async () => {
    const api = context.window.OwnerAPI;
    storage.setItem(api.sessionKey, JSON.stringify({token: 'tab-token', expires: now + 900000}));
    await api.fetch('/api/v1/owner/router');
    await api.fetch('/api/v1/owner/secure/devices');
    assert.equal(requests[0].url, 'http://localhost:8910/panel/api/v1/owner/router'.replace('http://localhost:8910', ''));
    assert.equal(requests[0].headers.get('X-Orion-Owner-Session'), 'tab-token');
    assert.equal(requests[1].headers.get('X-Orion-Owner-Session'), 'tab-token');
    now += 900000;
    await api.fetch('/api/v1/owner/router');
    assert.equal(requests[2].headers.get('X-Orion-Owner-Session'), null);
    api.clearSession();
    assert.equal(api.session(), null);
    console.log('Management screens share a tab token and stop using it at 15 minutes');
})().catch(error => {console.error(error); process.exitCode = 1;});
