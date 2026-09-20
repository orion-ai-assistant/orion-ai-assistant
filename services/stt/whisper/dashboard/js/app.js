/**
 * Orion STT Dashboard
 *
 * Özellikler:
 *  1. Son Sesi Dinle: Tıklayınca durdurma butonu olur, tekrar tıklayınca durur; sayfa yenilenince backend hafızası temizlenir.
 *  2. Karakter & kelime sayısı alt metrikler barına taşındı.
 *  3. Başlık sağ üstte model ve donanım rozetiyle dengelendi, canlı akış badge'i kaldırıldı.
 *  4. Prompt: 2 saniye yazı yazılmazsa otomatik kaydedilir & akış varsa güncellenir.
 *  5. Her satırda [Canlı · EN], [Dosya · TR], [Mikrofon · TR] dil etiketleri.
 *  6. Önizleme kartı: dosya/mikrofon sonrası dinleme ve ardından deşifre etme.
 */

document.addEventListener('DOMContentLoaded', () => {
    // Sayfa açıldığında veya yenilendiğinde sunucudaki son ses hafızasını temizle
    fetch('/v1/audio/debug/clear', { method: 'POST' }).catch(() => {});
    window.addEventListener('beforeunload', () => {
        if (navigator.sendBeacon) {
            navigator.sendBeacon('/v1/audio/debug/clear');
        }
    });

    // DOM Elemanları
    const modelNameText          = document.getElementById('modelNameText');
    const deviceBadge            = document.getElementById('deviceBadge');
    const computeBadge           = document.getElementById('computeBadge');
    const languageSelect         = document.getElementById('languageSelect');
    const promptInput            = document.getElementById('promptInput');

    const dropContainer          = document.getElementById('dropContainer');
    const audioFileInput         = document.getElementById('audioFileInput');
    const uploadAudioBtn         = document.getElementById('uploadAudioBtn');
    const uploadBtnText          = document.getElementById('uploadBtnText');
    const recordMicBtn           = document.getElementById('recordMicBtn');
    const recordMicBtnText       = document.getElementById('recordMicBtnText');
    const streamBtn              = document.getElementById('streamBtn');
    const streamBtnText          = document.getElementById('streamBtnText');
    const dropHintText           = document.getElementById('dropHintText');

    // Önizleme Kartı
    const previewCard            = document.getElementById('previewCard');
    const previewLabel           = document.getElementById('previewLabel');
    const previewAudioPlayer     = document.getElementById('previewAudioPlayer');
    const transcribePreviewBtn   = document.getElementById('transcribePreviewBtn');
    const transcribePreviewBtnText = document.getElementById('transcribePreviewBtnText');
    const cancelPreviewBtn       = document.getElementById('cancelPreviewBtn');

    // Sonuç & Araçlar
    const resultStatusPill       = document.getElementById('resultStatusPill');
    const textStats              = document.getElementById('textStats');
    const copyTextBtn            = document.getElementById('copyTextBtn');
    const copyBtnLabel           = document.getElementById('copyBtnLabel');
    const playDebugAudioBtn      = document.getElementById('playDebugAudioBtn');
    const debugBtnLabel          = document.getElementById('debugBtnLabel');
    const debugBtnIconWrap       = document.getElementById('debugBtnIconWrap');
    const clearBtn               = document.getElementById('clearBtn');
    const transcriptionBox       = document.getElementById('transcriptionBox');
    const emptyPlaceholder       = document.getElementById('emptyPlaceholder');
    const finalText              = document.getElementById('finalText');
    const liveText               = document.getElementById('liveText');
    const metricDetectedLang     = document.getElementById('metricDetectedLang');
    const metricProcessTime      = document.getElementById('metricProcessTime');
    const toast                  = document.getElementById('toastNotification');

    // State
    let pendingSource = "";       // "Dosya" | "Mikrofon"
    let pendingWav = null;        // 16kHz WAV blob
    let isPlayingDebugAudio = false;
    let currentDebugAudio = null;

    // Mikrofon Kayıt State
    let isRecordingMic = false;
    let micAudioCtx = null;
    let micMediaStream = null;
    let micProcessor = null;
    let micRecordedSamples = [];
    let micTimerInterval = null;
    let micSeconds = 0;

    // Canlı Akış State
    let isStreaming = false;
    let streamWs = null;
    let streamAudioCtx = null;
    let streamMediaStream = null;
    let streamProcessor = null;
    let streamTimerInterval = null;
    let streamSeconds = 0;
    let streamCurrentLang = "";
    let streamCurrentPrompt = "";

    const defaultHint = "Ses dosyası yükleyebilir, mikrofonla kaydedebilir veya canlı konuşabilirsiniz. (Dosyayı buraya sürükleyebilirsiniz)";

    // ==========================================================
    // INIT — Model & Dil Listesi
    // ==========================================================
    async function initDashboard() {
        try {
            const res = await fetch('/v1/audio/info');
            if (res.ok) {
                const info = await res.json();
                if (info.model && modelNameText) modelNameText.innerText = info.model;
                if (info.device && deviceBadge) {
                    deviceBadge.innerText = info.device.toLowerCase() === 'cuda' ? 'GPU' : info.device.toUpperCase();
                }
                if (info.compute_type && computeBadge) {
                    computeBadge.innerText = info.compute_type;
                }
            }
        } catch (_) {}

        try {
            const langRes = await fetch('/v1/audio/languages');
            if (langRes.ok) {
                const data = await langRes.json();
                if (Array.isArray(data.languages)) {
                    languageSelect.innerHTML = "";
                    data.languages.forEach(lang => {
                        const opt = document.createElement('option');
                        opt.value = lang.code;
                        opt.textContent = lang.name;
                        if (lang.code === "") opt.selected = true;
                        languageSelect.appendChild(opt);
                    });
                }
            }
        } catch (e) {
            console.warn("Diller yüklenemedi:", e);
        }
    }

    initDashboard();

    // ==========================================================
    // YARDIMCI METODLAR
    // ==========================================================
    function showToast(msg, duration = 2600) {
        toast.innerText = msg;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), duration);
    }

    function setStatus(text, type = 'idle') {
        resultStatusPill.innerText = text;
        resultStatusPill.className = `status-pill status-pill-${type}`;
    }

    function updateStats() {
        const full = (finalText.innerText + " " + liveText.innerText).trim();
        if (full.length === 0) {
            if (textStats) textStats.innerText = "0 karakter | 0 kelime";
            if (emptyPlaceholder) emptyPlaceholder.style.display = "block";
            return;
        }
        if (emptyPlaceholder) emptyPlaceholder.style.display = "none";
        const wordCount = full.split(/\s+/).filter(w => w).length;
        if (textStats) textStats.innerText = `${full.length} karakter | ${wordCount} kelime`;
    }

    function appendFinal(text, source = "", lang = "") {
        if (!text || !text.trim()) return;
        if (emptyPlaceholder) emptyPlaceholder.style.display = "none";

        const p = document.createElement('p');
        const labelParts = [source, lang ? lang.toUpperCase() : ""].filter(Boolean);
        const label = labelParts.join(" · ");

        if (label) {
            p.innerHTML = `<strong style="color:var(--accent-blue)">[${escapeHtml(label)}]:</strong> ${escapeHtml(text)}`;
        } else {
            p.innerText = text;
        }
        finalText.appendChild(p);
        transcriptionBox.scrollTop = transcriptionBox.scrollHeight;
        updateStats();
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
        }[c]));
    }

    function formatTime(totalSec) {
        const s = Math.floor(totalSec % 60);
        const m = Math.floor((totalSec / 60) % 60);
        const h = Math.floor((totalSec / 3600) % 24);
        const d = Math.floor(totalSec / 86400);
        const pad = n => (n < 10 ? '0' : '') + n;
        if (d > 0) return `${d}g ${pad(h)}:${pad(m)}:${pad(s)}`;
        if (h > 0) return `${pad(h)}:${pad(m)}:${pad(s)}`;
        return `${pad(m)}:${pad(s)}`;
    }

    function encodeWav(samples, sr = 16000) {
        const buf = new ArrayBuffer(44 + samples.length * 2);
        const view = new DataView(buf);
        const ws = (off, str) => { for (let i = 0; i < str.length; i++) view.setUint8(off + i, str.charCodeAt(i)); };
        ws(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true);
        ws(8, 'WAVE'); ws(12, 'fmt ');
        view.setUint32(16, 16, true); view.setUint16(20, 1, true);
        view.setUint16(22, 1, true); view.setUint32(24, sr, true);
        view.setUint32(28, sr * 2, true); view.setUint16(32, 2, true);
        view.setUint16(34, 16, true); ws(36, 'data');
        view.setUint32(40, samples.length * 2, true);
        let off = 44;
        for (let i = 0; i < samples.length; i++, off += 2) {
            const s = Math.max(-1, Math.min(1, samples[i]));
            view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        }
        return new Blob([view], { type: 'audio/wav' });
    }

    // ==========================================================
    // ÖNIZLEME KARTI
    // ==========================================================
    function showPreview(blob, source, label) {
        pendingWav = blob;
        pendingSource = source;
        previewLabel.innerText = label;
        previewAudioPlayer.src = URL.createObjectURL(blob);
        previewCard.classList.remove('hidden');
        transcribePreviewBtnText.innerText = "Deşifre Et";
        transcribePreviewBtn.disabled = false;
    }

    function hidePreview() {
        previewCard.classList.add('hidden');
        if (previewAudioPlayer.src) {
            URL.revokeObjectURL(previewAudioPlayer.src);
            previewAudioPlayer.src = "";
        }
        pendingWav = null;
        pendingSource = "";
    }

    cancelPreviewBtn.addEventListener('click', () => {
        hidePreview();
        dropHintText.innerText = defaultHint;
        setStatus("HAZIR", "idle");
    });

    transcribePreviewBtn.addEventListener('click', () => {
        if (pendingWav) {
            sendForTranscription(pendingWav, pendingSource);
            hidePreview();
        }
    });

    async function sendForTranscription(blob, source) {
        transcribePreviewBtn.disabled = true;
        transcribePreviewBtnText.innerText = "Gönderiliyor...";
        setStatus("İŞLENİYOR", "processing");
        dropHintText.innerText = "⏳ Deşifre ediliyor, lütfen bekleyin...";

        const formData = new FormData();
        const ext = blob.type.includes('wav') ? 'wav' : 'webm';
        formData.append("file", blob, `${source.toLowerCase()}_${Date.now()}.${ext}`);

        const lang = languageSelect.value.trim();
        if (lang) formData.append("language", lang);
        const prompt = promptInput.value.trim();
        if (prompt) formData.append("prompt", prompt);

        try {
            const res = await fetch("/v1/audio/transcriptions", { method: "POST", body: formData });
            const data = await res.json();

            if (res.ok) {
                const detectedLang = data.language && data.language !== lang ? data.language : (lang || data.language || "");
                appendFinal(data.text, source, detectedLang);
                setStatus("TAMAMLANDI", "idle");
                metricProcessTime.innerText = `${(data.duration * 1000).toFixed(0)} ms`;
                if (detectedLang) metricDetectedLang.innerText = detectedLang.toUpperCase();
                showToast(`${source} deşifre edildi.`);
            } else {
                showToast("Hata: " + (data.detail || "Deşifre edilemedi"));
                setStatus("HATA", "idle");
            }
        } catch (err) {
            showToast("Sunucuya bağlanılamadı.");
            setStatus("BAĞLANTI HATASI", "idle");
        } finally {
            uploadBtnText.innerText = "Upload Audio";
            uploadAudioBtn.disabled = false;
            dropHintText.innerText = defaultHint;
        }
    }

    // ==========================================================
    // 1. DOSYA YÜKLEME
    // ==========================================================
    uploadAudioBtn.addEventListener('click', () => audioFileInput.click());

    audioFileInput.addEventListener('change', e => {
        if (e.target.files?.[0]) {
            handleFileSelected(e.target.files[0]);
            audioFileInput.value = "";
        }
    });

    ['dragenter', 'dragover'].forEach(ev => dropContainer.addEventListener(ev, e => {
        e.preventDefault(); dropContainer.classList.add('drag-over');
    }));
    ['dragleave', 'drop'].forEach(ev => dropContainer.addEventListener(ev, e => {
        e.preventDefault(); dropContainer.classList.remove('drag-over');
    }));
    dropContainer.addEventListener('drop', e => {
        if (e.dataTransfer.files?.[0]) handleFileSelected(e.dataTransfer.files[0]);
    });

    function handleFileSelected(file) {
        stopRecordingSafe();
        stopStreamingSafe();
        dropHintText.innerText = `Seçilen dosya: ${file.name}`;
        const blob = file.slice(0, file.size, file.type);
        showPreview(blob, "Dosya", `Ses Dosyası: ${file.name}`);
        setStatus("ÖNİZLEME", "processing");
    }

    // ==========================================================
    // 2. MİKROFON KAYDI
    // ==========================================================
    recordMicBtn.addEventListener('click', () => {
        if (!isRecordingMic) startMicRecording();
        else stopMicRecordingAndPreview();
    });

    async function startMicRecording() {
        stopStreamingSafe();
        hidePreview();

        try {
            micMediaStream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1, sampleRate: 16000 }
            });

            micRecordedSamples = [];
            micAudioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
            const source = micAudioCtx.createMediaStreamSource(micMediaStream);
            micProcessor = micAudioCtx.createScriptProcessor(4096, 1, 1);

            micProcessor.onaudioprocess = e => {
                if (!isRecordingMic) return;
                const ch = e.inputBuffer.getChannelData(0);
                for (let i = 0; i < ch.length; i++) micRecordedSamples.push(ch[i]);
            };

            source.connect(micProcessor);
            micProcessor.connect(micAudioCtx.destination);

            isRecordingMic = true;
            micSeconds = 0;
            recordMicBtn.classList.add('active');
            recordMicBtnText.innerText = "Kaydı Durdur (00:00)";
            setStatus("KAYIT", "live");
            dropHintText.innerText = "🔴 Kayıt alınıyor... Bitirmek için tekrar tıklayın.";

            micTimerInterval = setInterval(() => {
                micSeconds++;
                recordMicBtnText.innerText = `Kaydı Durdur (${formatTime(micSeconds)})`;
            }, 1000);
        } catch (err) {
            alert("Mikrofona erişim izni alınamadı.");
        }
    }

    async function stopMicRecordingAndPreview() {
        if (!isRecordingMic) return;

        clearInterval(micTimerInterval);
        micTimerInterval = null;
        isRecordingMic = false;

        recordMicBtn.classList.remove('active');
        recordMicBtnText.innerText = "Record Mic";

        if (micProcessor) { micProcessor.disconnect(); micProcessor = null; }
        if (micAudioCtx) { micAudioCtx.close(); micAudioCtx = null; }
        if (micMediaStream) { micMediaStream.getTracks().forEach(t => t.stop()); micMediaStream = null; }

        if (micRecordedSamples.length < 1600) {
            showToast("Kayıt çok kısa.");
            setStatus("HAZIR", "idle");
            dropHintText.innerText = defaultHint;
            return;
        }

        const wavBlob = encodeWav(micRecordedSamples, 16000);
        setStatus("ÖNİZLEME", "processing");
        dropHintText.innerText = "Kaydı dinleyin, ardından 'Deşifre Et' butonuna tıklayın.";
        showPreview(wavBlob, "Mikrofon", `Mikrofon Kaydı (${formatTime(micSeconds)})`);
    }

    function stopRecordingSafe() {
        if (!isRecordingMic) return;
        clearInterval(micTimerInterval); micTimerInterval = null;
        isRecordingMic = false;
        if (micProcessor) micProcessor.disconnect();
        if (micAudioCtx) micAudioCtx.close();
        if (micMediaStream) micMediaStream.getTracks().forEach(t => t.stop());
        micProcessor = null; micAudioCtx = null; micMediaStream = null;
        recordMicBtn.classList.remove('active');
        recordMicBtnText.innerText = "Record Mic";
    }

    // ==========================================================
    // 3. CANLI AKIŞ & PROMPT (2 SN DEBOUNCE)
    // ==========================================================
    let promptDebounceTimer = null;

    promptInput.addEventListener('input', () => {
        clearTimeout(promptDebounceTimer);
        promptDebounceTimer = setTimeout(() => {
            applyPromptUpdate();
        }, 2000);
    });

    promptInput.addEventListener('change', () => {
        clearTimeout(promptDebounceTimer);
        applyPromptUpdate();
    });

    function applyPromptUpdate() {
        const val = promptInput.value.trim();
        if (isStreaming) {
            if (val !== streamCurrentPrompt) {
                reconnectStreamWithNewSettings();
            }
        } else {
            showToast("💾 Prompt kaydedildi", 1600);
        }
    }

    languageSelect.addEventListener('change', () => {
        if (isStreaming) reconnectStreamWithNewSettings();
    });

    function reconnectStreamWithNewSettings() {
        const newLang = languageSelect.value.trim();
        const newPrompt = promptInput.value.trim();

        if (newLang === streamCurrentLang && newPrompt === streamCurrentPrompt) return;

        showToast("⚙️ Dil/Prompt güncellendi, canlı akış yenileniyor...", 2200);

        if (streamWs) {
            streamWs.onclose = null;
            streamWs.close();
            streamWs = null;
        }

        streamCurrentLang = newLang;
        streamCurrentPrompt = newPrompt;

        setTimeout(() => {
            if (isStreaming) openStreamWebSocket();
        }, 300);
    }

    streamBtn.addEventListener('click', () => {
        if (!isStreaming) startStreaming();
        else stopStreamingSafe();
    });

    async function startStreaming() {
        stopRecordingSafe();
        hidePreview();

        try {
            streamMediaStream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1, sampleRate: 16000 },
                video: false
            });

            streamCurrentLang = languageSelect.value.trim();
            streamCurrentPrompt = promptInput.value.trim();

            streamAudioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
            const source = streamAudioCtx.createMediaStreamSource(streamMediaStream);
            streamProcessor = streamAudioCtx.createScriptProcessor(4096, 1, 1);
            source.connect(streamProcessor);
            streamProcessor.connect(streamAudioCtx.destination);

            isStreaming = true;
            streamSeconds = 0;
            streamBtn.classList.add('active');
            streamBtnText.innerText = "Canlı Akışı Durdur (00:00)";
            setStatus("CANLI AKIŞ", "live");
            dropHintText.innerText = "🟢 Canlı akış aktif, doğrudan konuşabilirsiniz.";

            streamTimerInterval = setInterval(() => {
                streamSeconds++;
                streamBtnText.innerText = `Canlı Akışı Durdur (${formatTime(streamSeconds)})`;
            }, 1000);

            openStreamWebSocket();

        } catch (err) {
            alert("Mikrofona erişilemedi.");
            stopStreamingSafe();
        }
    }

    function openStreamWebSocket() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        let wsUrl = `${protocol}//${location.host}/v1/audio/transcriptions/stream?`;
        if (streamCurrentLang) wsUrl += `language=${encodeURIComponent(streamCurrentLang)}&`;
        if (streamCurrentPrompt) wsUrl += `prompt=${encodeURIComponent(streamCurrentPrompt)}&`;

        streamWs = new WebSocket(wsUrl);

        streamWs.onopen = () => {
            if (!streamProcessor) return;
            streamProcessor.onaudioprocess = e => {
                if (isPlayingDebugAudio) return;
                if (!streamWs || streamWs.readyState !== WebSocket.OPEN) return;
                const f32 = e.inputBuffer.getChannelData(0);
                const i16 = new Int16Array(f32.length);
                for (let i = 0; i < f32.length; i++) {
                    const s = Math.max(-1, Math.min(1, f32[i]));
                    i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }
                streamWs.send(i16.buffer);
            };
        };

        streamWs.onmessage = event => {
            try {
                const data = JSON.parse(event.data);
                const msgLang = data.language || streamCurrentLang || "";

                if (data.type === "live") {
                    if (emptyPlaceholder) emptyPlaceholder.style.display = "none";
                    liveText.innerText = " " + data.text + "…";
                    if (data.duration) metricProcessTime.innerText = (data.duration * 1000).toFixed(0) + " ms";
                    if (msgLang) metricDetectedLang.innerText = msgLang.toUpperCase();
                    updateStats();

                } else if (data.type === "final") {
                    liveText.innerText = "";
                    appendFinal(data.text, "Canlı", msgLang);
                    if (data.duration) metricProcessTime.innerText = (data.duration * 1000).toFixed(0) + " ms";
                    if (msgLang) metricDetectedLang.innerText = msgLang.toUpperCase();
                    updateStats();
                }
            } catch (e) {
                console.error("WS parse hatası:", e);
            }
        };

        streamWs.onerror = () => {
            if (isStreaming) showToast("Canlı akış bağlantı hatası.");
        };

        streamWs.onclose = () => {
            if (isStreaming && !streamWs) return;
            stopStreamingSafe();
        };
    }

    function stopStreamingSafe() {
        if (!isStreaming && !streamWs) return;

        if (streamTimerInterval) { clearInterval(streamTimerInterval); streamTimerInterval = null; }
        if (streamWs) { streamWs.onclose = null; streamWs.close(); streamWs = null; }
        if (streamProcessor) { streamProcessor.disconnect(); streamProcessor = null; }
        if (streamAudioCtx) { streamAudioCtx.close(); streamAudioCtx = null; }
        if (streamMediaStream) { streamMediaStream.getTracks().forEach(t => t.stop()); streamMediaStream = null; }

        isStreaming = false;
        streamBtn.classList.remove('active');
        streamBtnText.innerText = "Canlı Akışı Başlat";
        dropHintText.innerText = defaultHint;
        liveText.innerText = "";
        setStatus("HAZIR", "idle");
        updateStats();
    }

    // ==========================================================
    // ARAÇLAR (Kopyala, Temizle, Son Sesi Dinle/Durdur)
    // ==========================================================
    copyTextBtn.addEventListener('click', async () => {
        const full = (finalText.innerText + " " + liveText.innerText).trim();
        if (!full) { showToast("Kopyalanacak metin yok."); return; }
        try {
            await navigator.clipboard.writeText(full);
            copyBtnLabel.innerText = "Kopyalandı!";
            showToast("Metin kopyalandı.");
            setTimeout(() => copyBtnLabel.innerText = "Metni Kopyala", 1800);
        } catch { showToast("Panoya kopyalanamadı."); }
    });

    clearBtn.addEventListener('click', () => {
        finalText.innerHTML = "";
        liveText.innerText = "";
        if (emptyPlaceholder) emptyPlaceholder.style.display = "block";
        updateStats();
        setStatus("HAZIR", "idle");
        showToast("Temizlendi.");
    });

    function stopDebugAudio() {
        if (currentDebugAudio) {
            currentDebugAudio.pause();
            currentDebugAudio.currentTime = 0;
            currentDebugAudio = null;
        }
        isPlayingDebugAudio = false;
        playDebugAudioBtn.disabled = false;
        playDebugAudioBtn.classList.remove('btn-tool-playing');
        debugBtnLabel.innerText = "Son Sesi Dinle";
        if (debugBtnIconWrap) {
            debugBtnIconWrap.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
                    <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path>
                </svg>`;
        }
    }

    playDebugAudioBtn.addEventListener('click', async () => {
        // Eğer zaten oynatılıyorsa tekrar tıklandığında DURDUR
        if (isPlayingDebugAudio) {
            stopDebugAudio();
            return;
        }

        try {
            debugBtnLabel.innerText = "Yükleniyor...";
            playDebugAudioBtn.disabled = true;

            const res = await fetch('/v1/audio/debug/last');
            if (res.ok) {
                const blob = await res.blob();
                currentDebugAudio = new Audio(URL.createObjectURL(blob));
                isPlayingDebugAudio = true;
                playDebugAudioBtn.disabled = false;
                playDebugAudioBtn.classList.add('btn-tool-playing');
                debugBtnLabel.innerText = "Sesi Durdur";

                if (debugBtnIconWrap) {
                    debugBtnIconWrap.innerHTML = `
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                            <rect x="5" y="5" width="14" height="14" rx="2"></rect>
                        </svg>`;
                }

                currentDebugAudio.onended = () => {
                    stopDebugAudio();
                };
                currentDebugAudio.onerror = () => {
                    stopDebugAudio();
                    showToast("Ses oynatılamadı.");
                };
                await currentDebugAudio.play();
            } else {
                showToast("Henüz deşifre edilmiş ses yok.");
                stopDebugAudio();
            }
        } catch (e) {
            showToast("Ses yüklenemedi.");
            stopDebugAudio();
        }
    });
});
