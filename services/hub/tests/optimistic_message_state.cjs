const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const source = fs.readFileSync(
    require('path').join(__dirname, '../src/orion/api/ui/js/config.js'),
    'utf8'
);
const context = { performance: { now: () => 1 } };
vm.createContext(context);
vm.runInContext(`${source}\nthis.testState = AppState;`, context);

const state = context.testState;
state.showOptimisticUserMessage('Merhaba', null);
assert.strictEqual(state.claimOptimisticUserMessage('chat-1', 'Merhaba'), false);
state.pendingNewChatRequest = true;
assert.strictEqual(state.adoptPendingNewChat('chat-1'), true);
assert.strictEqual(state.currentChatId, 'chat-1');
assert.strictEqual(state.pendingNewChatRequest, false);
assert.strictEqual(state.claimOptimisticUserMessage('chat-1', 'Başka mesaj'), false);
assert.strictEqual(state.claimOptimisticUserMessage('chat-1', 'Merhaba'), true);
assert.strictEqual(state.optimisticUserMessage, null);

state.pendingNewChatRequest = true;
assert.strictEqual(state.adoptPendingNewChat('chat-2'), false);
state.pendingNewChatRequest = false;

state.showOptimisticUserMessage('Tekrar', 'chat-2');
state.clearOptimisticUserMessage();
assert.strictEqual(state.optimisticUserMessage, null);

console.log('optimistic message state tests passed');
