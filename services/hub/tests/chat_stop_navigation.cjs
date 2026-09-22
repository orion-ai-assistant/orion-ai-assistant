const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const button = { style: {}, disabled: false, innerHTML: '' };
const context = vm.createContext({ performance, console, window: {}, document: {
    getElementById: () => button,
}});
for (const file of ['config.js', 'ui.js', 'api.js', 'sse.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js', file), 'utf8'), context);
}
vm.runInContext('globalThis.test = { AppState, UI, API, SSE };', context);
const { AppState: state, UI: ui, API: api, SSE: sse } = context.test;
async function run() {
    state.startGenerating('a', 'turn-a');
    state.startGenerating('b', 'turn-b');
    state.selectChat('a');
    assert.equal(button.style.display, 'flex');
    state.selectChat(null);
    assert.equal(button.style.display, 'none');
    state.selectChat('idle');
    assert.equal(button.style.display, 'none');
    state.selectChat('b');
    const paths = [];
    api._fetch = async url => { paths.push(url); return { ok: true }; };
    await api.stopGeneration();
    assert.deepEqual(paths, ['/api/v1/chats/b/turns/turn-b/stop']);
    assert.equal(state.isStopping('b'), true);
    assert.equal(state.isStopping('a'), false);
    state.selectChat('a');
    assert.equal(button.disabled, false);
    state.selectChat('b');
    assert.equal(button.disabled, true);
    state.selectChat('a');
    ui.finishGeneration = () => {};
    sse.processEvent({ type: 'done', chat_id: 'b', turn_id: 'turn-b', data: {} });
    assert.equal(button.style.display, 'flex');
    assert.equal(state.isAnyChatGenerating('a'), true);

    // A delayed message response must not select the abandoned conversation.
    ui.appendUserMessage = ui.clearInput = () => {};
    state.sseConnected = true;
    state.selectChat(null);
    let resolve;
    api._fetch = () => new Promise(r => { resolve = r; });
    const sending = api.sendMessage('hello');
    state.selectChat(null);
    resolve({ ok: true, json: async () => ({ chat_id: 'new', turn_id: 'new-turn' }) });
    await sending;
    assert.equal(state.currentChatId, null);
    assert.equal(button.style.display, 'none');
    assert.equal(state.isAnyChatGenerating('new'), true);
    state.selectChat('new');
    assert.equal(button.style.display, 'flex');
    console.log('Chat-scoped stop and navigation tests passed');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
