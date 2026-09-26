const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.style = {}; }
    appendChild(child) { this.children.push(child); }
    append(...children) { this.children.push(...children); }
    setAttribute() {}
    querySelector() { return null; }
    showModal() { this.open = true; }
    close() { this.onclose(); }
    remove() { this.removed = true; }
}
const chatArea = new Element('div');
const body = new Element('body');
const ctx = vm.createContext({ console, performance, window: {}, alert: msg => { throw Error(msg); }, document: {
    body, getElementById: id => id === 'chat-area' ? chatArea : new Element('div'),
    createElement: tag => new Element(tag),
}});
for (const file of ['config', 'ui', 'api']) vm.runInContext(fs.readFileSync(path.join(__dirname, `../src/orion/api/ui/js/${file}.js`), 'utf8'), ctx);
vm.runInContext('globalThis.test = { UI, API, AppState, AppConfig };', ctx);
const { UI: ui, API: api, AppState: state, AppConfig: config } = ctx.test;
const files = [
    { name: 'test.txt', data: '<b>benim adım kürşat</b>', isText: true },
    ...Array.from({length: 15}, (_, i) => ({ name: `photo-${i}.jpg`, data: `data:image/jpeg;base64,${i}` })),
    { name: 'clip.mp4', data: 'data:video/mp4;base64,video' },
];
ui.appendUserMessage('selam', files);
const group = chatArea.children[0];
assert.equal(group.children[0].children.length, 17);
assert.equal(group.children[1].textContent, 'selam');
const textCard = group.children[0].children[0];
textCard.onclick();
assert.equal(body.children[0].children[0].textContent, files[0].data);
assert.equal(body.children[0].open, true);
assert.equal(body.children[0].children.length, 2);
assert.equal(body.children[0].children[1].textContent, 'test.txt');
body.children[0].close();
assert.equal(body.children[0].removed, true);
const video = group.children[0].children[16];
assert.equal(video.children[0].tagName, 'video');
assert.equal(video.children[1].textContent, '▶');
assert.equal(video.children.length, 2);
assert.equal(video.title, 'clip.mp4');
assert.equal(group.children[0].children[1].children.length, 1);
video.onclick();
assert.equal(body.children[1].children[0].controls, true);
ui.appendUserMessage('selam\n\n[Ek Dosya: test.txt]\n```\nbenim adım kürşat\n```');
assert.equal(chatArea.children[1].children[1].textContent, 'selam');
assert.equal(chatArea.children[1].children[0].children[0].title, 'test.txt');
(async () => {
    state.sseConnected = true;
    state.currentChatId = 'chat';
    config.getUserId = () => 'user';
    ui.selectedImages = files;
    ui.clearInput = () => { ui.selectedImages = []; };
    ui.setStopButtonVisible = () => {};
    let request;
    api._fetch = async (_, options) => {
        request = JSON.parse(options.body);
        return { ok: true, status: 200, json: async () => ({ chat_id: 'chat', turn_id: 'turn', status: 'queued' }) };
    };
    await api.sendMessage('selam');
    assert.equal(request.input.metadata.display_text, 'selam');
    assert.equal(request.input.metadata.attachments.length, 17);
    assert.equal(request.input.metadata.attachments[1].name, 'photo-0.jpg');
    assert.equal(request.input.images.length, 16);
    assert.ok(request.input.text.includes(files[0].data));
    assert.equal(chatArea.children[2].children[1].textContent, 'selam');
    assert.equal(chatArea.children[2].children[0].children.length, 17);
    console.log('Attachment cards, text/video previews, legacy history and send metadata passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
