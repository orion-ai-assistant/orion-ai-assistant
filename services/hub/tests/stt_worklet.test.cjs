const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

const source = fs.readFileSync(path.join(__dirname, '../src/orion/api/ui/js/stt-worklet.js'), 'utf8').replace(/\r\n/g, '\n');
function recorder(rate) {
    const packets = [];
    let Processor;
    vm.runInNewContext(source, {
        sampleRate: rate,
        AudioWorkletProcessor: class {
            constructor() { this.port = { postMessage: data => packets.push(data) }; }
        },
        registerProcessor: (name, value) => { Processor = value; },
    });
    return { processor: new Processor({ parameterData: {
        numChannels: 1, sampleRate: 16000, streamBufferSize: 2048,
    } }), packets };
}

test('vendored processor is unchanged from Flutter record_web 1.3.0', () => {
    const upstream = source.split('// BEGIN UPSTREAM RECORD_WEB WORKLET\n')[1];
    assert.equal(crypto.createHash('sha256').update(upstream).digest('hex'),
        '3aaea9426c621f1cf2f0b206969784b36bd12581d1cfadc27084364d445e64ba');
});

for (const [rate, packetSamples] of [[16000, 2048], [44100, 743], [48000, 682]]) {
    test(`Flutter packet cadence and PCM at ${rate}Hz`, () => {
        const h = recorder(rate);
        const frame = new Float32Array(128).fill(0.25);
        for (let i = 0; i < 16; i++) h.processor.process([[frame]]);
        assert.equal(h.packets.length, 0);
        h.processor.process([[frame]]);
        assert.equal(h.packets.length, 1);
        assert.equal(h.packets[0].length, packetSamples);
        assert.ok(Array.from(h.packets[0]).every(sample => sample === 8191));
        for (let i = 17; i < 32; i++) h.processor.process([[frame]]);
        assert.equal(h.packets.length, 1);
        h.processor.process([[frame]]);
        assert.equal(h.packets.length, 2);
        assert.equal(h.packets[1].length, packetSamples);
    });
}

test('Flutter output is signed PCM16 little-endian with identical clipping', () => {
    const h = recorder(16000);
    const frame = new Float32Array(128);
    frame.set([-1, 0, 0.5, 1, 2, -2]);
    h.processor.process([[frame]]);
    for (let i = 1; i < 16; i++) h.processor.process([[new Float32Array(128)]]);
    h.processor.process([[]]);
    assert.deepEqual(Array.from(new Uint8Array(h.packets[0].buffer).slice(0, 12)),
        [0, 128, 0, 0, 255, 63, 255, 127, 255, 127, 0, 128]);
});

test('no microphone input creates no fabricated PCM', () => {
    const h = recorder(48000);
    for (let i = 0; i < 32; i++) h.processor.process([[]]);
    assert.equal(h.packets.length, 0);
});
