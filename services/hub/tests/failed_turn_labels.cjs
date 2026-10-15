const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.style = {}; }
    appendChild(child) { this.children.push(child); return child; }
    setAttribute() {}
    querySelector(selector) {
        return this.children.find(child => `.${child.className}` === selector) || null;
    }
}
const chatArea = new Element('div');
const context = vm.createContext({console, window: {}, OrionI18n: {t: key => key},
    AppState: {currentChatId: 'chat'}, MarkdownView: {create: text => Object.assign(new Element('div'), {textContent: text})},
    document: {getElementById: () => chatArea, createElement: tag => new Element(tag)}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8'), context);
const ui = vm.runInContext('UI', context);
ui.scrollToBottom = ui.addMessageCopy = () => {};
ui.appendUserMessage('failed question', [], true, 'failed');
const question = chatArea.children[0];
assert.equal(question.children[0].textContent, 'failed question');
assert.equal(question.children.length, 1, 'User messages do not repeat the exclusion label');
ui.appendStaticBotMessage('offline', null, null, null, [], null, true);
assert.equal(chatArea.children[1].children.at(-1).className, 'model-context-excluded');
ui.appendUserMessage('next question', [], false, 'next');
const next = chatArea.children[2];
const state = ui._getOrCreateChatState('chat');
state.botDiv = new Element('div');
// A delayed background failure must never label a different question.
ui.markFailedTurn('chat', 'older');
assert.equal(next.children.length, 1);
ui.markFailedTurn('chat', 'next');
ui.markFailedTurn('chat', 'next');
assert.equal(next.children.length, 1);
assert.equal(state.botDiv.children.length, 1);
console.log('Failed-turn exclusion appears only below the assistant response');
