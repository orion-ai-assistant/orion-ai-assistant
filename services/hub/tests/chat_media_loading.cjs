const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; }
    appendChild(child) { this.children.push(child); return child; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute() {}
    addEventListener() {}
    showModal() {}
    close() { this.onclose?.(); }
    remove() { this.removed = true; }
    play() { return Promise.resolve(); }
    pause() { this.paused = true; }
    querySelector(selector) {
        for (const child of this.children) {
            if (selector === child.tagName || selector === '.' + child.className) return child;
            const nested = child.querySelector(selector); if (nested) return nested;
        }
        return null;
    }
}
const area = new Element('div'), body = new Element('body');
let calls = 0, urls = 0, revoked = 0;
const context = vm.createContext({console, performance, AbortController,
    OrionI18n: {t: key => key}, AppState: {currentChatId: 'chat'}, window: {},
    API: {mediaBlob: async () => { calls++; return new Blob(['audio']); }},
    URL: {createObjectURL: () => 'blob:' + ++urls, revokeObjectURL: () => revoked++},
    document: {getElementById: () => area, body, createElement: tag => new Element(tag)},
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/ui.js'), 'utf8') + '\nglobalThis.ui = UI;', context);
const ui = context.ui;
ui.scrollToBottom = () => {};
const id = 'a'.repeat(64);
const media = {name: 'photo.png', mime_type: 'image/png', media_id: id, media_path: `/api/v1/chats/chat/media/${id}`};
(async () => {
    const card = ui.createAttachmentCard(media);
    assert.equal(calls, 0, 'Rendering metadata must not download media');
    assert.equal(card.querySelector('img'), null);
    card.onclick();
    await Promise.resolve(); await Promise.resolve();
    const dialog = body.children[0];
    assert.equal(dialog.querySelector('img').src, 'blob:1');
    dialog.close();
    assert.equal(revoked, 1, 'Closing a preview releases its binary URL');
    const target = new Element('div');
    ui._getOrCreateChatState = () => ({botDiv: target});
    ui.appendAudio('chat', {media_path: media.media_path, format: 'ogg'}, false, target);
    assert.equal(calls, 1, 'History audio loads only when played');
    const button = target.querySelector('button');
    await button.onclick();
    assert.equal(target.querySelector('audio').src, 'blob:2');
    ui.clearChatArea();
    assert.equal(revoked, 2);
    assert.equal(target.querySelector('audio').paused, true);
    console.log('Chat media loads on demand, uses binary blobs and releases previews/players');
})().catch(error => { console.error(error); process.exitCode = 1; });
