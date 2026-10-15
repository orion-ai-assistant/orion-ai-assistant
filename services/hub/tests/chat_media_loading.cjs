const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; this.style = {}; }
    appendChild(child) { this.children.push(child); return child; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute() {}
    addEventListener(name, callback) { (this.listeners ||= {})[name] = callback; }
    showModal() {}
    close() { this.onclose?.(); }
    remove() { this.removed = true; }
    play() { this.plays = (this.plays || 0) + 1; this.paused = false; this.listeners?.play?.(); return Promise.resolve(); }
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
const observers = [];
class Observer {
    constructor(callback, options) { this.callback=callback; this.options=options; this.targets=new Set(); observers.push(this); }
    observe(target) { this.targets.add(target); }
    unobserve(target) { this.targets.delete(target); }
    disconnect() { this.targets.clear(); }
    show(target) { this.callback([{target, isIntersecting:true}]); }
}
const context = vm.createContext({console, performance, AbortController,
    IntersectionObserver: Observer,
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
    const hidden = ui.createAttachmentCard({...media, media_path: `/api/v1/chats/chat/media/${'b'.repeat(64)}`});
    assert.equal(calls, 0, 'Off-screen metadata must not download media');
    assert.ok(card.querySelector('img'), 'Referenced images keep the existing square thumbnail');
    observers[0].show(card);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 1, 'Only a visible supplied card loads');
    assert.equal(card.querySelector('img').src, 'blob:1');
    assert.equal(hidden.querySelector('img').src, undefined);
    card.onclick();
    await new Promise(resolve => setImmediate(resolve));
    const dialog = body.children[0];
    assert.equal(dialog.querySelector('img').src, 'blob:1');
    dialog.close();
    assert.equal(calls, 1, 'Opening the preview reuses the downloaded thumbnail bytes');
    assert.equal(revoked, 0, 'The shared URL stays alive for its thumbnail until history is cleared');
    ui.openAttachment({...media, name: 'voice.wav', mime_type: 'audio/wav'});
    await new Promise(resolve => setImmediate(resolve));
    const audioDialog = body.children[1];
    const audio = audioDialog.querySelector('audio');
    assert.equal(audio.autoplay, false);
    assert.equal(audio.plays || 0, 0, 'Opening an audio attachment must stay paused');
    assert.equal(audio.controls, false, 'Use themed controls');
    audio.duration = 9; audio.currentTime = 0; audio.listeners.loadedmetadata();
    assert.equal(audioDialog.querySelector('span').textContent, '0:00 / 0:09');
    audioDialog.querySelector('button').onclick();
    assert.equal(audio.plays, 1);
    audioDialog.onclick({target: audioDialog.querySelector('button')});
    assert.notEqual(audioDialog.removed, true, 'Clicking audio controls keeps the preview open');
    audioDialog.onclick({target: audioDialog});
    assert.equal(audioDialog.removed, true, 'The backdrop closes audio previews');
    assert.equal(audio.paused, true);
    for (const file of [
        {name: 'video.mp4', mime_type: 'video/mp4', data: 'data:video/mp4;base64,AA=='},
        {name: 'image.png', mime_type: 'image/png', data: 'data:image/png;base64,AA=='},
        {name: 'text.txt', isText: true, data: 'preview'},
        {name: 'document.pdf', mime_type: 'application/pdf', data: 'data:application/pdf;base64,AA=='},
    ]) {
        ui.openAttachment(file);
        const preview = body.children.at(-1);
        preview.onclick({target: preview.children[0]});
        assert.notEqual(preview.removed, true, `${file.name}: content clicks keep the preview open`);
        preview.onclick({target: preview});
        assert.equal(preview.removed, true, `${file.name}: backdrop closes the preview`);
    }
    for (const mime_type of ['audio/wav', 'video/mp4']) {
        const mediaCard = ui.createAttachmentCard({mime_type, name: 'media'});
        const play = mediaCard.querySelector('.attachment-play');
        assert.ok(play);
        assert.equal(play.textContent, undefined, 'The play triangle uses one CSS shape, without a text grid item');
    }
    const text = Array.from({length: 10000}, (_, i) => `satır ${i}: uzun metin\n`).join('');
    const pre = new Element('pre');
    ui.textPreview(text, pre);
    const spacer = pre.children[0];
    assert.equal(pre.className, 'attachment-virtual-text');
    assert.ok(spacer.children.length < 50, 'Only visible text rows enter the DOM');
    assert.equal(spacer.children[0].textContent, 'satır 0: uzun metin');
    pre.scrollTop = 9995 * 22; pre.onscroll();
    assert.ok(spacer.children.some(row => row.textContent === 'satır 9999: uzun metin'));
    assert.ok(spacer.children.length < 50);
    const target = new Element('div');
    ui._getOrCreateChatState = () => ({botDiv: target});
    ui.appendAudio('chat', {media_path: media.media_path, format: 'ogg'}, false, target);
    assert.equal(calls, 1, 'History audio loads only when played');
    const button = target.querySelector('button');
    assert.equal(button.className, "audio-load-button");
    await button.onclick();
    assert.equal(target.querySelector('audio').src, 'blob:2');
    ui.clearChatArea();
    assert.equal(revoked, 2);
    assert.equal(target.querySelector('audio').paused, true);
    assert.equal(observers[0].targets.size, 0);
    console.log('Visible history thumbnails preload, reuse binary blobs and release media on chat changes');
})().catch(error => { console.error(error); process.exitCode = 1; });
