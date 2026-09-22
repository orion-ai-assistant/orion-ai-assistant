const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
let now = 100;
const rendered = [];
const context = vm.createContext({ performance: { now: () => now }, console, window: {},
    UI: { showTextMetrics: (_, data) => rendered.push(['text', data.total_ms]),
        appendAudio: (_, data, autoplay) => rendered.push(['audio', data.arrival_ms, autoplay]),
        setStopButtonVisible() {} },
});
for (const file of ['config.js', 'sse.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js', file), 'utf8'), context);
}
vm.runInContext('globalThis.test = { AppState, SSE };', context);
const { AppState: state, SSE: sse } = context.test;
state.showOptimisticUserMessage('hi');
now = 200;
state.bindOptimisticUserMessage('a');
state.startGenerating('a', 'turn');
state.currentChatId = 'a';
now = 1200;
sse.processEvent({ type: 'text_done', chat_id: 'a', turn_id: 'turn', data: { first_token_ms: 300, total_ms: 1000 } });
assert.equal(state.isGenerating(), true, 'Text completion must not close the audio stream');
now = 2600;
sse.processEvent({ type: 'audio', chat_id: 'a', turn_id: 'old', data: {} });
sse.processEvent({ type: 'audio', chat_id: 'a', turn_id: 'turn', data: {} });
assert.deepEqual(rendered, [['text', 1000], ['audio', 2500, true]]);
state.currentChatId = 'b';
now = 3100;
sse.processEvent({ type: 'audio', chat_id: 'a', turn_id: 'turn', data: {} });
assert.deepEqual(rendered[2], ['audio', 3000, false]);
console.log('Independent text and audio timing tests passed');
