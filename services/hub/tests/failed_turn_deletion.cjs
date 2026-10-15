const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const requests = [], reloads = [];
let newChats = 0;
const node = () => ({children: [], appendChild(child) {this.children.push(child);},
    querySelector() {return this.children.find(n => n.className === 'model-context-excluded');}});
const context = vm.createContext({console, performance, window: {loadChats() {}, loadChat: id => reloads.push(id)},
    setTimeout: () => 1, clearTimeout() {},
    localStorage: {getItem: () => null}, OrionI18n: {t: key => key},
    document: {getElementById: id => id === 'new-chat-btn' ? {click() {newChats++; context.test.AppState.selectChat(null);}} : {style: {}}, createElement: node},
});
for (const file of ['config.js', 'ui.js', 'api.js', 'sse.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js', file), 'utf8'), context);
}
vm.runInContext('globalThis.test = {AppState, UI, API, SSE}', context);
const {AppState: state, UI: ui, API: api, SSE: sse} = context.test;
ui.setStopButtonVisible = () => {};
api._fetch = async url => {requests.push(url); return {ok: true, json: async () => ({status: 'updated'})};};
(async () => {
    state.selectChat('chat');
    const user = node(), bot = node();
    ui._chatDivs.chat = {userDiv: user, botDiv: bot, userTurnId: 'bad'};
    ui.markFailedTurn('chat', 'bad');
    assert.equal(user.children.length, 0, 'Only the assistant gets an exclusion label');
    const label = bot.children[0], button = label.children[1];
    assert.equal(button.textContent, 'delete');
    await button.onclick();
    assert.deepEqual(requests, ['/api/v1/chats/chat/failed-turns/bad']);
    assert.deepEqual(reloads, ['chat']);
    sse.processEvent({type: 'chat_history_changed', chat_id: 'chat', data: {}});
    assert.deepEqual(reloads, ['chat', 'chat'], 'Other devices reload the changed chat');
    sse.processEvent({type: 'chat_history_changed', chat_id: 'other', data: {}});
    assert.equal(state.currentChatId, 'chat', 'Background edits preserve selection');
    sse.processEvent({type: 'chat_delete', chat_id: 'chat', data: {}});
    assert.equal(state.currentChatId, null);
    assert.equal(newChats, 1, 'Remote deletion opens a new chat');
    assert.equal(ui._chatDivs.chat, undefined);
    state.selectChat('error-chat');
    state.startGenerating('error-chat', 'bad-turn');
    let errorMarkdown = '';
    ui.appendToken = (_, text) => {errorMarkdown += text;};
    ui._chatDivs['error-chat'] = {botDiv: node(), userTurnId: 'bad-turn'};
    ui.finishGeneration = (id, hasTokens, metrics) => {
        assert.equal(ui._chatDivs[id].botDiv.querySelector('.model-context-excluded'), undefined,
            'The exclusion label must wait until metrics are rendered');
        assert.equal(metrics.failed_turn, true);
        assert.equal(metrics.turn_id, 'bad-turn');
    };
    const message = "⚠️ **Yönlendirici (Router) Hatası:**\n\n```text\nOpenRouter model 'gpt-oss-120b' does not support audio, image, video input. Supported input modalities: ['text'].\n```";
    sse.processEvent({type: 'error', chat_id: 'error-chat', turn_id: 'bad-turn', data: {message}});
    const marked = require('../src/orion/api/ui/js/vendor/marked.js');
    const html = marked.parse(errorMarkdown);
    assert.match(html, /<strong>Yönlendirici \(Router\) Hatası:<\/strong>/);
    assert.equal((html.match(/<pre>/g) || []).length, 1);
    assert.match(html, /\[&#39;text&#39;\]\.\n<\/code><\/pre>/);
    assert.doesNotMatch(html, /```|\[Hata:|<\/pre>\s*<p>\]/);
    console.log('Failed turn deletion and cross-device UI synchronization passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
