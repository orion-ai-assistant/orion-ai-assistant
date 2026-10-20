const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const values = new Map(), requests = [];
let now = 1000;
const storage = {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
const context = {URL, Headers, Date: {now: () => now}, sessionStorage: storage,
    document: {currentScript: {src: 'http://localhost:8910/panel/owner-assets/base.js'}},
    window: {fetch: async (url, options) => { requests.push({url, headers: options.headers}); return {status: url.endsWith('/denied') ? 401 : 200}; }}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/owner_ui/base.js'), 'utf8'), context);
(async () => {
    const api = context.window.OwnerAPI;
    storage.setItem(api.sessionKey, JSON.stringify({token: 'tab-token', expires: now + 900000}));
    await api.fetch('/api/v1/owner/router');
    await api.fetch('/api/v1/owner/secure/devices');
    assert.equal(requests[0].url, 'http://localhost:8910/panel/api/v1/owner/router'.replace('http://localhost:8910', ''));
    assert.equal(requests[0].headers.get('X-Orion-Owner-Session'), 'tab-token');
    assert.equal(requests[1].headers.get('X-Orion-Owner-Session'), 'tab-token');
    // In use, the session never ends: every request restarts the 15 minute idle limit.
    for (let step = 0; step < 4; step++) {
        now += 600000;
        await api.fetch('/api/v1/owner/router');
        assert.equal(requests.at(-1).headers.get('X-Orion-Owner-Session'), 'tab-token');
    }
    // A refused request does not keep it open.
    now += 600000;
    await api.fetch('/api/v1/owner/denied');
    now += 300000;
    await api.fetch('/api/v1/owner/router');
    assert.equal(requests.at(-1).headers.get('X-Orion-Owner-Session'), null);
    storage.setItem(api.sessionKey, JSON.stringify({token: 'tab-token', expires: now + 900000}));
    api.clearSession();
    assert.equal(api.session(), null);
    console.log('Management screens share a tab token that stays open while used and ends after 15 idle minutes');
})().catch(error => {console.error(error); process.exitCode = 1;});
