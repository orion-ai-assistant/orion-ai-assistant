const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
    constructor(tagName, text = '') {
        this.tagName = tagName; this.children = []; this._text = text; this.className = '';
        this.classList = {
            contains: name => this.className.split(' ').includes(name),
            remove: name => { this.className = this.className.split(' ').filter(item => item !== name).join(' '); },
        };
    }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set textContent(value) { this._text = String(value); this.children = []; }
    appendChild(child) { this.children.push(child); return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    insertBefore(child, before) {
        const position = this.children.indexOf(before);
        if (position < 0) this.appendChild(child); else this.children.splice(position, 0, child);
    }
    setAttribute() {}
    querySelector(selector) {
        for (const child of this.children) {
            if (selector[0] === '.' && child.className.split(' ').includes(selector.slice(1))) return child;
            const nested = child.querySelector(selector);
            if (nested) return nested;
        }
        return null;
    }
}
const chatArea = new Element('main');
const context = vm.createContext({
    window: {}, AppState: {currentChatId: 'chat'},
    document: {
        getElementById: id => id === 'chat-area' ? chatArea : null,
        createElement: tag => new Element(tag),
        createTextNode: text => new Element('#text', text),
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);
const ui = context.ui;

ui.createBotMessagePlaceholder('chat');
ui.appendThinkingToken('chat', 'first thought');
ui.appendToken('chat', 'first answer');
ui.appendThinkingToken('chat', 'second thought');
ui.appendToken('chat', 'second answer');
ui.replaceMessageContent('chat', 'first answersecond final');
const live = chatArea.children[0];
assert.deepEqual(live.children.map(child => child.tagName), ['details', '#text', 'details', '#text']);
assert.equal(live.children[0].open, false);
assert.equal(live.children[2].open, false);
assert.equal(live.children[0].querySelector('.think-body').textContent, 'first thought');
assert.equal(live.children[2].querySelector('.think-body').textContent, 'second thought');
assert.equal(live.children[1].textContent, 'first answer');
assert.equal(live.children[3].textContent, 'second final');

ui.appendStaticBotMessage('first answersecond final', null, null, null, [], [
    {type: 'thinking', content: 'first thought'},
    {type: 'content', content: 'first answer'},
    {type: 'thinking', content: 'second thought'},
    {type: 'content', content: 'second final'},
]);
const history = chatArea.children[1];
assert.deepEqual(history.children.map(child => child.tagName), ['details', '#text', 'details', '#text']);
assert.equal(history.children[0].querySelector('.think-body').textContent, 'first thought');
assert.equal(history.children[2].querySelector('.think-body').textContent, 'second thought');
console.log('Separate thinking blocks survive live snapshots and history rendering');
