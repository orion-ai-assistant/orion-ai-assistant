const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
    constructor(tagName) { this.tagName = tagName; this.children = []; this.className = ''; }
    appendChild(child) { this.children.push(child); }
    insertBefore(child, before) {
        const index = this.children.indexOf(before);
        if (index < 0) this.children.push(child);
        else this.children.splice(index, 0, child);
    }
    setAttribute() {}
    querySelector(selector) {
        const matches = element => selector.startsWith('.')
            ? element.className.split(' ').includes(selector.slice(1)) : element.tagName === selector;
        for (const child of this.children) {
            if (matches(child)) return child;
            const nested = child.querySelector(selector);
            if (nested) return nested;
        }
        return null;
    }
}
const chatArea = new Element('div');
const context = vm.createContext({ window: {}, AppState: { currentChatId: 'chat' }, document: {
    getElementById: id => id === 'chat-area' ? chatArea : null,
    createElement: tag => new Element(tag),
    createTextNode: text => Object.assign(new Element('#text'), { textContent: text }),
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);
const history = [
    { content: 'first', metrics: { first_token_ms: 300, total_ms: 800 }, audio: { audio: 'Zmlyc3Q=', arrival_ms: 1200 } },
    { content: 'second', metrics: { first_token_ms: 400, total_ms: 900 }, audio: { audio: 'c2Vjb25k', arrival_ms: 2300 } },
    { content: 'text only' },
];
for (const message of history) context.ui.appendStaticBotMessage(message.content, null, message.metrics, message.audio);
assert.equal(chatArea.children[0].querySelector('audio').src, 'data:audio/wav;base64,Zmlyc3Q=');
assert.equal(chatArea.children[1].querySelector('audio').src, 'data:audio/wav;base64,c2Vjb25k');
assert.equal(chatArea.children[1].querySelector('audio').autoplay, false);
assert.equal(chatArea.children[1].querySelector('.audio-arrival-time').textContent, '♫ 2.30 sn');
assert.equal(chatArea.children[1].children[1].className, 'message-meta');
assert.equal(chatArea.children[1].children[2].className, 'audio-player-container');
assert.equal(chatArea.children[2].querySelector('audio'), null);
console.log('History restores each message audio without autoplay or duplicates');
