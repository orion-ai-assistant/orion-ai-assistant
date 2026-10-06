const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8');
test('message copy preserves raw Markdown and prevents duplicate actions', async () => {
    const copied = [];
    const nodes = [];
    const context = {window: {},
        document: {getElementById: () => null, createElement: () => ({setAttribute() {}, appendChild(node) { nodes.push(node); }})},
        navigator: {clipboard: {writeText: async text => copied.push(text)}},
        OrionI18n: {t: () => 'Mesajı kopyala'},
        setTimeout: () => {},
    };
    vm.runInNewContext(source + ';globalThis.ui = UI;', context);
    const div = {querySelector: selector => selector === '.message-copy' ? nodes[0] : {appendChild: node => nodes.push(node)}, appendChild: node => nodes.push(node)};
    const raw = '**kalın** 1. sayı\nhttps://example.com/?a=1&b=2';
    context.ui.addMessageCopy(div, () => raw);
    context.ui.addMessageCopy(div, () => 'duplicate');
    assert.equal(nodes.length, 1);
    await nodes[0].onclick();
    assert.deepEqual(copied, [raw]);
    assert.equal(nodes[0].textContent, '✓');
});

test('completed message keeps its own text when the next response reuses chat state', async () => {
    const copied = [], buttons = [];
    const div = {querySelector: selector => selector === '.message-copy' ? buttons[0] : {appendChild: node => buttons.push(node)}, appendChild: node => buttons.push(node), classList:{contains: () => false}};
    const state = {botDiv:div, contentNodes:[{markdownSource:'**first response**'}]};
    const context = {window:{}, document:{getElementById: () => ({querySelectorAll:()=>[]}), createElement:()=>({setAttribute(){}})},
      navigator:{clipboard:{writeText:async text=>copied.push(text)}}, OrionI18n:{t:()=>''}, setTimeout:()=>{}, CSS:{escape:x=>x},
      AppState:{currentChatId:'another', getGenerationMetrics:()=>({})}, MarkdownView:{render:()=>{}}};
    vm.runInNewContext(source + ';globalThis.ui = UI;',context);
    context.ui._chatDivs.chat = state;
    context.ui.finishGeneration('chat');
    state.contentNodes = [{markdownSource:'second response'}];
    await buttons[0].onclick();
    assert.deepEqual(copied,['**first response**']);
});
