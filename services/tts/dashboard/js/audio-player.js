let audioCtx = null;

export async function playStream(res, text, startTime, PROBE_REAL_MS, BUFFER_MULTIPLIER, MIN_PREBUFFER, MAX_PREBUFFER, UNDERRUN_THRESHOLD) {
    if (audioCtx) {
        audioCtx.close();
    }

    const bufferStatusEl = document.getElementById('bufferStatus');
    const bufferBarEl = document.getElementById('bufferBar');
    const bufferBarFill = document.getElementById('bufferBarFill');
    const ttfbMetric = document.getElementById('ttfbMetric');
    
    bufferStatusEl.style.display = 'block';
    bufferBarEl.style.display = 'block';
    bufferStatusEl.className = 'buffering';
    bufferStatusEl.innerText = '⏳ Ön yükleme yapılıyor...';
    bufferBarFill.style.width = '0%';

    const sampleRate = parseInt(res.headers.get("X-Sample-Rate")) || 24000;
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: sampleRate });
    const reader = res.body.getReader();

    let pendingChunks = [];
    let pendingDuration = 0;
    let nextTime = 0;
    let playbackStarted = false;
    let isRebuffering = false;
    let streamDone = false;
    let underrunCount = 0;
    let firstChunkReceived = false;

    let probeStartTime = null;
    let probeComplete = false;
    let adaptivePrebuffer = 1.0;
    let measuredRate = null;
    let leftoverByte = null;

    function enqueueChunk(value) {
        if (!value || value.byteLength === 0) return;
        let data;
        if (leftoverByte !== null) {
            const combined = new Uint8Array(1 + value.byteLength);
            combined[0] = leftoverByte;
            combined.set(value, 1);
            data = combined;
            leftoverByte = null;
        } else {
            data = value;
        }
        if (data.byteLength % 2 !== 0) {
            leftoverByte = data[data.byteLength - 1];
            data = data.slice(0, data.byteLength - 1);
        }
        if (data.byteLength < 2) return;
        const buf = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
        const int16 = new Int16Array(buf);
        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768.0;
        pendingChunks.push(float32);
        pendingDuration += float32.length / sampleRate;
    }

    function flushQueue() {
        while (pendingChunks.length > 0) {
            const float32 = pendingChunks.shift();
            const audioBuf = audioCtx.createBuffer(1, float32.length, sampleRate);
            audioBuf.copyToChannel(float32, 0);
            const src = audioCtx.createBufferSource();
            src.buffer = audioBuf;
            src.connect(audioCtx.destination);
            if (nextTime < audioCtx.currentTime + 0.02) {
                nextTime = audioCtx.currentTime + 0.02;
            }
            src.start(nextTime);
            nextTime += audioBuf.duration;
            pendingDuration -= audioBuf.duration;
        }
    }

    const barInterval = setInterval(() => {
        const ahead = Math.max(0, nextTime - audioCtx.currentTime);
        const pct = Math.min(100, Math.max(0, (ahead / Math.max(adaptivePrebuffer, 1)) * 100));
        bufferBarFill.style.width = pct + '%';

        // Stream bitti VE ses de tamamen oynadı → interval'i sonlandır
        if (streamDone && ahead < 0.05) {
            clearInterval(barInterval);
            bufferBarFill.style.width = '0%';
            bufferStatusEl.className = 'playing';
            bufferStatusEl.innerText = underrunCount > 0
                ? `✅ Tamamlandı — ${underrunCount}x yavaşlama yaşandı`
                : '✅ Akış tamamlandı — kesinti yok';
            return;
        }

        if (!streamDone && !isRebuffering && playbackStarted && ahead < UNDERRUN_THRESHOLD) {
            isRebuffering = true;
            underrunCount++;
            adaptivePrebuffer = Math.min(MAX_PREBUFFER, adaptivePrebuffer * 1.5);
            bufferStatusEl.className = 'underrun';
            bufferStatusEl.innerText = `⏸ Üretim yavaş, yeniden dolduruluyor... (${underrunCount}x) → yeni buffer: ${adaptivePrebuffer.toFixed(1)}s`;
        }
    }, 200);

    while (true) {
        const { done, value } = await reader.read();
        if (!firstChunkReceived && value) {
            const firstByteTime = ((performance.now() - startTime) / 1000).toFixed(2);
            ttfbMetric.innerText = `İlk Veri: ${firstByteTime}s`;
            ttfbMetric.dataset.ttfb = firstByteTime;
            firstChunkReceived = true;
            probeStartTime = performance.now();
        }
        if (done) { streamDone = true; break; }
        enqueueChunk(value);

        if (!probeComplete && probeStartTime !== null) {
            const probeElapsed = performance.now() - probeStartTime;
            if (probeElapsed >= PROBE_REAL_MS) {
                measuredRate = pendingDuration / (probeElapsed / 1000);
                // speedFactor'ü 3.0 ile kırpıyoruz: küçük it/s dalgalanmalarında
                // buffer değeri çok sert değişmesin (duyarlılığı azalt).
                const speedFactor = measuredRate > 0 ? Math.min(3.0, Math.max(1, 1 / measuredRate)) : 3.0;
                adaptivePrebuffer = Math.min(MAX_PREBUFFER, Math.max(MIN_PREBUFFER, MIN_PREBUFFER * speedFactor * BUFFER_MULTIPLIER));
                probeComplete = true;
                bufferStatusEl.innerText = `⚙️ Hız: ${measuredRate.toFixed(2)}x gerçek zamanlı → ${adaptivePrebuffer.toFixed(1)}s buffer`;
            } else {
                const probePct = Math.round((probeElapsed / PROBE_REAL_MS) * 100);
                bufferStatusEl.innerText = `⚙️ Üretim hızı ölçülüyor... %${probePct}`;
                continue;
            }
        } else if (!probeComplete) continue;

        if (!playbackStarted) {
            if (pendingDuration >= adaptivePrebuffer) {
                playbackStarted = true;
                nextTime = audioCtx.currentTime + 0.05;
                const firstSoundTime = ((performance.now() - startTime) / 1000).toFixed(2);
                ttfbMetric.innerText = `İlk Veri: ${document.getElementById('ttfbMetric').dataset.ttfb || '?'}s | İlk Ses: ${firstSoundTime}s (${measuredRate.toFixed(2)}x)`;
                bufferStatusEl.className = 'playing';
                bufferStatusEl.innerText = `▶ Oynatılıyor — buffer: ${adaptivePrebuffer.toFixed(1)}s`;
                flushQueue();
            } else {
                const pct = Math.min(100, Math.round((pendingDuration / adaptivePrebuffer) * 100));
                bufferBarFill.style.width = pct + '%';
                bufferStatusEl.innerText = `⏳ Dolduruluyor %${pct} — ${pendingDuration.toFixed(2)}s / ${adaptivePrebuffer.toFixed(1)}s`;
            }
        } else if (isRebuffering) {
            const pct = Math.min(100, Math.round((pendingDuration / adaptivePrebuffer) * 100));
            bufferBarFill.style.width = pct + '%';
            bufferStatusEl.innerText = `⏸ Yeniden dolduruluyor %${pct} — ${pendingDuration.toFixed(2)}s / ${adaptivePrebuffer.toFixed(1)}s`;
            if (pendingDuration >= adaptivePrebuffer) {
                isRebuffering = false;
                nextTime = audioCtx.currentTime + 0.05;
                bufferStatusEl.className = 'playing';
                bufferStatusEl.innerText = '▶ Oynatılıyor (devam)';
                flushQueue();
            }
        } else {
            flushQueue();
        }
    }

    if (!playbackStarted && pendingChunks.length > 0) {
        playbackStarted = true;
        nextTime = audioCtx.currentTime + 0.05;
        flushQueue();
    } else {
        flushQueue();
    }

    // Stream HTTP response'u bitti, toplam süreyi göster.
    // Bar ve tamamlanma mesajı barInterval içinde ses bitince kendisi kapanacak.
    const totalTime = ((performance.now() - startTime) / 1000).toFixed(2);
    document.getElementById('totalTimeMetric').innerText = `Toplam Üretim: ${totalTime}s`;
    bufferStatusEl.className = 'playing';
    bufferStatusEl.innerText = '▶ Oynatılıyor (tüm ses yüklendi)';
}

export function stopAudio() {
    if (audioCtx) {
        audioCtx.close();
        audioCtx = null;
    }
}
