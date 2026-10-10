const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function setup(fetch) {
    const timers = [], messages = [], snapshots = [], retries = [];
    let now = 0, finished = 0;
    const context = {window: {}, console: {log() {}}, performance: {now: () => now},
        Map, AbortSignal, fetch, setTimeout: cb => {timers.push(cb); return cb;},
        clearTimeout: cb => {const i = timers.indexOf(cb); if (i >= 0) timers.splice(i, 1);},
        Auth: {tokenKey: 'hub', getToken: () => 'token', getAuthHeaders: () => ({Authorization: 'Bearer token'})},
        HubPicker: {apiPath: path => path},
        UI: {_chatDivs: {chat: {botDiv: {}}}, setStopButtonVisible() {},
            appendToken: (chat, text) => messages.push(text),
            replaceMessageContent: (chat, text) => snapshots.push(text),
            finishGeneration: () => finished++, offerRetry: (...args) => retries.push(args)},
    };
    for (const file of ['config.js', 'sse.js']) {
        vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js', file), 'utf8'), context);
    }
    const state = vm.runInNewContext('AppState', context);
    const sse = vm.runInNewContext('SSE', context);
    state.currentChatId = 'chat';
    state.startGenerating('chat', 'turn');
    return {context, state, sse, timers, messages, snapshots, retries, get finished() {return finished;},
        async tick() {now += 3000; const cb = timers.shift(); assert.ok(cb); await cb();}};
}

test('missed Pub/Sub error is recovered by HTTP and ends the typing indicator', async () => {
    const harness = setup(async () => ({ok: true, json: async () => ({
        status: 'failed', turn_id: 'turn', error: 'Kuyruk zaman aşımı', retry_text: 'hello'})}));
    await harness.tick();
    assert.equal(harness.state.getActiveTurn('chat'), null);
    assert.equal(harness.finished, 1);
    assert.match(harness.messages[0], /Kuyruk zaman aşımı/);
    assert.equal(harness.retries.length, 0, 'Errors do not add a preparation button');
    assert.equal(harness.timers.length, 0);
});

test('missed completion restores the saved answer without sending another request', async () => {
    const harness = setup(async () => ({ok: true, json: async () => ({
        status: 'completed', turn_id: 'turn', result: {text: 'Saved answer'}})}));
    await harness.tick();
    assert.deepEqual(harness.snapshots, ['Saved answer']);
    assert.equal(harness.finished, 1);
    assert.equal(harness.state.getActiveTurn('chat'), null);
});

test('server outage ends waiting after 30 seconds and warns that work may continue', async () => {
    const harness = setup(async () => {throw new Error('offline');});
    for (let i = 0; i < 11; i++) await harness.tick();
    assert.equal(harness.finished, 1);
    assert.match(harness.messages[0], /devam ediyor olabilir/);
    assert.equal(harness.timers.length, 0);
});

test('healthy long processing is not mistaken for an orphan', async () => {
    const harness = setup(async () => ({ok: true, json: async () => ({status: 'processing', turn_id: 'turn'})}));
    for (let i = 0; i < 15; i++) await harness.tick();
    assert.equal(harness.finished, 0);
    assert.ok(harness.state.getActiveTurn('chat'));
});

test('late status response cannot finish a newer turn', async () => {
    let release;
    const harness = setup(() => new Promise(resolve => {release = resolve;}));
    const polling = harness.tick();
    harness.state.startGenerating('chat', 'new-turn');
    release({ok: true, json: async () => ({status: 'failed', turn_id: 'turn', error: 'old'})});
    await polling;
    assert.equal(harness.state.getActiveTurn('chat').turnId, 'new-turn');
    assert.equal(harness.finished, 0);
});

test('disconnect cancels polling even when a request is in flight', async () => {
    let release;
    const harness = setup(() => new Promise(resolve => {release = resolve;}));
    harness.context.UI.setConnectionStatus = () => {};
    const polling = harness.tick();
    harness.sse.disconnect();
    release({ok: true, json: async () => ({status: 'processing', turn_id: 'turn'})});
    await polling;
    assert.equal(harness.timers.length, 0);
});
