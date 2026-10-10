const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
function player() {
    const nodes = [], target = {children: [], appendChild(el) {this.children.push(el); el.parentElement = this;}, querySelector() {return this.children.find(el => el.className === 'audio-player-container');}};
    class AudioContext {
        state = 'running'; currentTime = 0; destination = {};
        resume() {return Promise.resolve();}
        createBuffer(_, count, rate) {return {duration: count / rate, getChannelData: () => new Float32Array(count)};}
        createBufferSource() {const node = {connect() {}, disconnect() {}, start(time) {this.time = time;}, stop() {this.stopped = true;}}; nodes.push(node); return node;}
    }
    function element() {return {children: [], setAttribute() {}, appendChild(el) {this.children.push(el); el.parentElement = this;}};}
    const context = vm.createContext({window: {AudioContext, addEventListener() {}}, crypto: webcrypto,
        document: {createElement: element, querySelectorAll: () => []},
        AppState: {currentChatId: 'chat'}, UI: {currentSettings: {tts_enabled: true}, _chatDivs: {chat: {botDiv: target}}},
        OrionI18n: {t: x => x}, localStorage: {getItem() {}}, atob, Uint8Array, DataView, console,
        setInterval: () => 1, clearInterval() {}});
    vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../src/orion/api/ui/js/pcm_stream.js'), 'utf8'), context);
    const pcm = context.window.PcmSpeech;
    const start = (origin = pcm.sessionId, id = 'turn') => pcm.start({chat_id: 'chat', data: {
        stream_id: id, origin_session_id: origin, encoding: 'pcm_s16le', sample_rate: 24000, channels: 1, bits_per_sample: 16}});
    const chunk = (bytes, sequence = 0, id = 'turn') => pcm.chunk({chat_id: 'chat', data: {stream_id: id, sequence, chunk: Buffer.alloc(bytes).toString('base64')}});
    const end = (status, bytes, count = 1) => pcm.end({data: {stream_id: 'turn', status, total_bytes: bytes, chunk_count: count}});
    return {pcm, nodes, start, chunk, end, target};
}
test('another session gets the growing slider without autoplay', () => {
    const {pcm, nodes, start, chunk} = player(); start('another-session'); chunk(48000);
    assert.equal(nodes.length, 0); const s = pcm.recordings.get('turn');
    assert.equal(s.controls.slider.max, '1'); assert.equal(s.controls.time.textContent, '0:00 / 0:01');
});
test('short utterance flushes on EOF and retains one player when WAV arrives', () => {
    const {pcm, nodes, start, chunk, end, target} = player(); start(); chunk(4); assert.equal(nodes.length, 0);
    end('completed', 4); assert.equal(nodes.length, 1); assert.equal(nodes[0].buffer.duration, 2 / 24000);
    pcm.recording('chat', {stream_id: 'turn', audio: 'saved', format: 'wav'}, false, target);
    assert.equal(target.children.length, 1); pcm.stop(); assert.equal(nodes[0].stopped, true);
});
test('stop silences playback, retains arriving samples, and allows seek/replay', () => {
    const {pcm, nodes, start, chunk, end} = player(); start(); chunk(48000); assert.equal(nodes.length, 1);
    pcm.stop(); chunk(24000, 1); end('stopped', 72000, 2);
    const s = pcm.recordings.get('turn'); assert.equal(nodes[0].stopped, true);
    assert.equal(s.controls.slider.max, '1.5'); assert.equal(s.playing, false);
    s.controls.slider.value = '1'; s.controls.slider.oninput(); s.controls.button.onclick();
    assert.equal(nodes.length, 2); assert.equal(nodes[1].buffer.duration, .5); pcm.stop();
});
test('missing chunks silence the stream and repeated starts never restart it', () => {
    const {pcm, nodes, start, chunk} = player(); start(); chunk(8640); chunk(4, 2);
    assert.equal(nodes[0].stopped, true); start(); chunk(8640); assert.equal(nodes.length, 1);
});
