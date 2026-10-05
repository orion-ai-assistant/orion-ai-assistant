const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/stt.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function setup({ modulePromise = Promise.resolve(), trackSampleRate = 48000 } = {}) {
    const settings = deferred();
    const mic = deferred();
    const alerts = [];
    const sockets = [];
    const contexts = [];
    let micRequests = 0;
    let stopped = 0;
    const constraints = [];
    const nodeOptions = [];
    const track = {
        label: 'Test microphone', readyState: 'live', muted: false,
        getSettings: () => ({ sampleRate: trackSampleRate, channelCount: 1, autoGainControl: false }),
        stop() { stopped++; },
    };
    const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
    const input = { value: 'Önceki metin', addEventListener() {} };
    const button = { classList: { add() {}, remove() {} } };
    class AudioContext {
        constructor(options = {}) {
            this.sampleRate = options.sampleRate || 48000;
            this.state = 'suspended';
            this.audioWorklet = { addModule: () => modulePromise };
            contexts.push(this);
        }
        resume() { this.state = 'running'; return Promise.resolve(); }
        close() { this.state = 'closed'; return Promise.resolve(); }
        createMediaStreamSource() { return { connect() {} }; }
    }
    class AudioWorkletNode {
        constructor(context, name, options) {
            nodeOptions.push({ name, options });
            context.processor = this;
            this.port = { onmessage: null, close() {} };
        }
        connect() {}
        disconnect() {}
    }
    class WebSocket {
        static OPEN = 1;
        static CONNECTING = 0;
        constructor() { this.readyState = 0; this.sent = []; sockets.push(this); }
        send(buffer) { this.sent.push(new Int16Array(buffer)); }
        close() { this.readyState = 3; }
        open() { this.readyState = 1; this.onopen(); }
    }
    const sandbox = {
        HubPicker: {apiPath: path => path},
        Auth: {getToken: () => 'token'},
        window: { AudioContext, AudioWorkletNode, location: { host: 'localhost:8909', protocol: 'http:' } },
        navigator: { mediaDevices: { getUserMedia(config) { micRequests++; constraints.push(config); return mic.promise; } } },
        API: { getSettings: () => settings.promise },
        document: { getElementById: id => id === 'message-input' ? input : id === 'mic-btn' ? button : null },
        localStorage: { getItem: () => 'token' },
        WebSocket, console: { log() {}, debug() {}, warn() {}, error() {} },
        alert: message => alerts.push(message),
        setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout,
    };
    vm.runInNewContext(script, sandbox);
    const stt = sandbox.window.STT;
    function audio(value) {
        const pcm = new Int16Array(2048).fill(value * 32767);
        stt.processor.port.onmessage({ data: pcm });
    }
    return { stt, settings, mic, stream, input, audio, sockets, contexts, alerts, constraints, nodeOptions,
        get micRequests() { return micRequests; }, get stopped() { return stopped; } };
}


async function connected(h) {
    const start = h.stt.start();
    h.settings.resolve({ stt_model: 'local-stt' });
    await tick();
    h.sockets[0].open();
    await tick();
    return { start };
}

test('Flutter order: settings, socket ready, then microphone; exact recorder configuration', async () => {
    const h = setup({ trackSampleRate: 16000 });
    const start = h.stt.start();
    assert.equal(h.micRequests, 0);
    assert.equal(h.sockets.length, 0);
    h.settings.resolve({});
    await tick();
    assert.equal(h.sockets.length, 1);
    assert.equal(h.micRequests, 0, 'do not record before socket readiness');
    h.sockets[0].open();
    await tick();
    assert.equal(h.micRequests, 1);
    h.mic.resolve(h.stream);
    await start;
    assert.deepEqual(JSON.parse(JSON.stringify(h.constraints[0].audio)), {
        autoGainControl: false, echoCancellation: true, noiseSuppression: true,
        sampleRate: 16000, sampleSize: 16, channelCount: 1,
    });
    assert.equal(h.nodeOptions[0].name, 'recorder.worklet');
    assert.deepEqual(JSON.parse(JSON.stringify(h.nodeOptions[0].options)), {
        parameterData: { numChannels: 1, sampleRate: 16000, streamBufferSize: 2048 },
    });
    assert.equal(h.stt.diagnostics.packetIntervalMs, 128);
    assert.equal(h.stt.isStreaming, false);
    h.audio(0.1);
    h.audio(0.2);
    assert.equal(h.stt.isStreaming, true);
    assert.deepEqual(h.sockets[0].sent.map(chunk => chunk[0]), [3276, 6553]);
    assert.equal(h.stt.diagnostics.sentBytes, 2 * 2048 * 2);
    h.stt.stop();
    assert.equal(h.stopped, 1);
});

test('stopping while settings are pending prevents any recording or connection', async () => {
    const h = setup();
    const start = h.stt.start();
    await h.stt.toggle();
    h.settings.resolve({});
    await start;
    assert.equal(h.sockets.length, 0);
    assert.equal(h.micRequests, 0);
});

test('second click cancels the connection handshake immediately', async () => {
    const h = setup();
    const start = h.stt.start();
    h.settings.resolve({});
    await tick();
    const socket = h.sockets[0];
    await h.stt.toggle();
    await start;
    socket.open();
    assert.equal(h.micRequests, 0);
    assert.equal(h.stt.isStarting, false);
    assert.equal(h.alerts.length, 0);
});

test('cancelled microphone permission stops a late stream', async () => {
    const h = setup();
    const { start } = await connected(h);
    h.stt.stop();
    h.mic.resolve(h.stream);
    await start;
    assert.equal(h.stopped, 1);
    assert.equal(h.contexts.length, 0);
});

for (const settings of [{ stt_enabled: false }, { stt_model: 'other-model' }, { error: 'offline' }]) {
    test(`invalid settings never start capture: ${JSON.stringify(settings)}`, async () => {
        const h = setup();
        const start = h.stt.start();
        h.settings.resolve(settings);
        await start;
        assert.equal(h.micRequests, 0);
        assert.equal(h.sockets.length, 0);
        assert.equal(h.stt.isStarting, false);
        assert.equal(h.alerts.length, 1);
    });
}

test('live/final text matches Flutter, including an empty live event', async () => {
    const h = setup();
    const { start } = await connected(h);
    h.mic.resolve(h.stream);
    await start;
    h.audio(0.1);
    function event(type, text) { h.sockets[0].onmessage({ data: JSON.stringify({ type, text }) }); }
    event('live', 'geçici');
    assert.equal(h.input.value, 'Önceki metin geçici');
    event('live', '');
    assert.equal(h.input.value, 'Önceki metin');
    event('final', 'kalıcı');
    event('live', 'yeni');
    assert.equal(h.input.value, 'Önceki metin kalıcı yeni');
    assert.equal(h.stt.diagnostics.events.live, 3);
    event('error', 'service failed');
    assert.equal(h.input.value, 'Önceki metin kalıcı');
    assert.equal(h.stopped, 1);
});

test('AudioContext uses the applied track rate, with no extra warmup context', async () => {
    const h = setup({ trackSampleRate: 44100 });
    const { start } = await connected(h);
    h.mic.resolve(h.stream);
    await start;
    assert.equal(h.contexts.length, 1);
    assert.equal(h.stt.audioCtx.sampleRate, 44100);
    assert.equal(h.stt.diagnostics.trackSampleRate, 44100);
    assert.equal(h.stt.diagnostics.outputSampleRate, 16000);
    h.stt.stop();
});

test('cancelled worklet load cannot start a recorder after cancellation', async () => {
    const module = deferred();
    const h = setup({ modulePromise: module.promise });
    const { start } = await connected(h);
    h.mic.resolve(h.stream);
    await tick();
    h.stt.stop();
    module.resolve();
    await start;
    assert.equal(h.stt.processor, null);
    assert.equal(h.stt.audioCtx, null);
    assert.equal(h.stopped, 1);
});

test('late events from an old socket cannot change a new recording', async () => {
    const h = setup();
    const { start } = await connected(h);
    h.mic.resolve(h.stream);
    await start;
    const old = h.sockets[0];
    h.stt.stop();
    const next = h.stt.start();
    await tick();
    h.sockets[1].open();
    await next;
    h.audio(0.1);
    old.onclose();
    old.onmessage({ data: JSON.stringify({ type: 'final', text: 'old session' }) });
    assert.equal(h.stt.isStreaming, true);
    assert.equal(h.input.value, 'Önceki metin');
    h.stt.stop();
});
