/**
 * Orion Hub - WebSocket Canlı STT (Speech-to-Text) Yöneticisi
 * Orion Router üzerinden Whisper STT akışını yönetir.
 */
const STT = {
    isStreaming: false,
    ws: null,
    audioCtx: null,
    mediaStream: null,
    processor: null,
    committedText: '',
    lastLiveText: '',
    _listenerAttached: false,
    
    // Router adresi ve portu (Router varsayılan portu 20128)
    getRouterWsUrl(apiKey, language = 'tr', model = 'local-stt') {
        const host = window.location.hostname || 'localhost';
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const routerPort = 20128;
        return `${protocol}//${host}:${routerPort}/v1/audio/transcriptions/stream?token=${encodeURIComponent(apiKey)}&language=${encodeURIComponent(language)}&model=${encodeURIComponent(model)}&provider=local`;
    },

    // Float32 Web Audio tamponunu 16kHz 16-bit Mono PCM'e dönüştürme
    downsampleTo16kPCM(inputData, inputSampleRate, targetSampleRate = 16000) {
        if (inputSampleRate === targetSampleRate) {
            const output = new Int16Array(inputData.length);
            for (let i = 0; i < inputData.length; i++) {
                const s = Math.max(-1, Math.min(1, inputData[i]));
                output[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            return output;
        }

        const sampleRateRatio = inputSampleRate / targetSampleRate;
        const newLength = Math.round(inputData.length / sampleRateRatio);
        const result = new Int16Array(newLength);
        let offsetResult = 0;
        let offsetBuffer = 0;

        while (offsetResult < result.length) {
            const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
            let accum = 0;
            let count = 0;
            for (let i = offsetBuffer; i < nextOffsetBuffer && i < inputData.length; i++) {
                accum += inputData[i];
                count++;
            }
            const sample = count > 0 ? accum / count : 0;
            const s = Math.max(-1, Math.min(1, sample));
            result[offsetResult] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            offsetResult++;
            offsetBuffer = nextOffsetBuffer;
        }
        return result;
    },

    // Kullanıcı elle inputa yazdığında veya sildiğinde STT taban metnini güncelle
    handleUserInput() {
        const inputField = document.getElementById('message-input');
        if (!inputField) return;
        const currentVal = inputField.value;

        // Canlı akış sırasında kullanıcı yazıyorsa ve sonda canlı metin varsa onu ayıkla
        if (this.isStreaming && this.lastLiveText && currentVal.endsWith(this.lastLiveText)) {
            this.committedText = currentVal.slice(0, currentVal.length - this.lastLiveText.length).trimEnd();
        } else {
            // Kullanıcı metni doğrudan değiştirdi / sildi / ekleme yaptı
            this.committedText = currentVal;
            this.lastLiveText = '';
        }
    },

    attachInputListener() {
        if (this._listenerAttached) return;
        const inputField = document.getElementById('message-input');
        if (inputField) {
            inputField.addEventListener('input', () => this.handleUserInput());
            this._listenerAttached = true;
        }
    },

    reset() {
        this.committedText = '';
        this.lastLiveText = '';
    },

    async toggle() {
        if (this.isStreaming) {
            this.stop();
        } else {
            await this.start();
        }
    },

    async start() {
        if (this.isStreaming) return;

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            alert('Tarayıcınız mikrofon erişimini desteklemiyor veya izin verilmedi.');
            return;
        }

        this.attachInputListener();

        // Router API Key'ini al
        let routerApiKey = '';
        let sttModel = 'local-stt';
        try {
            const settings = await API.getSettings();
            if (settings.error) throw new Error(settings.error);
            if (settings.stt_enabled === false || settings.stt_enabled === 'false') {
                alert('Canlı sesli yazma ayarlardan kapalı.');
                return;
            }
            sttModel = settings.stt_model || 'local-stt';
            if (sttModel !== 'local-stt') {
                alert('Canlı sesli yazma yalnızca local-stt modelini destekliyor. Ayarlardan yerel modeli seçin.');
                return;
            }
            const rawKey = (settings?.router_api_key || '').trim();
            routerApiKey = (rawKey && rawKey !== 'sk-60f3eaf169d7c485-0icocf-0a3db541') ? rawKey : 'orion';
        } catch (e) {
            alert('Sesli yazma ayarları alınamadı: ' + e.message);
            return;
        }

        const micBtn = document.getElementById('mic-btn');
        const inputField = document.getElementById('message-input');
        
        if (inputField) {
            // Kullanıcının daha önceden yazmış olduğu metni koru
            this.committedText = inputField.value;
            this.lastLiveText = '';
        } else {
            this.committedText = '';
            this.lastLiveText = '';
        }

        try {
            // 1. Mikrofon izni ve akışı
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount: 1,
                    sampleRate: 16000,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                }
            });

            // 2. Audio Context & Script Processor (4096 sample @ 16kHz = 256ms chunk)
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            this.audioCtx = new AudioContextClass({ sampleRate: 16000 });
            const source = this.audioCtx.createMediaStreamSource(this.mediaStream);
            this.processor = this.audioCtx.createScriptProcessor(4096, 1, 1);

            const inputSampleRate = this.audioCtx.sampleRate;

            // 3. WebSocket Bağlantısı
            const wsUrl = this.getRouterWsUrl(routerApiKey, 'tr', sttModel);
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('[STT] Router WebSocket bağlandı.');
                this.isStreaming = true;
                if (micBtn) {
                    micBtn.classList.add('recording');
                    const idleIcon = document.getElementById('mic-icon-idle');
                    const recIcon = document.getElementById('mic-icon-recording');
                    if (idleIcon) idleIcon.style.display = 'none';
                    if (recIcon) recIcon.style.display = 'block';
                }
                if (inputField && !inputField.value) {
                    inputField.placeholder = "Dinleniyor, konuşabilirsiniz...";
                }
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'live') {
                        // Kullanıcı konuşurken anlık akan metin kullanıcının mevcut metninin yanına eklenir
                        const liveText = (data.text || '').trim();
                        if (inputField && liveText) {
                            this.lastLiveText = liveText;
                            const base = this.committedText ? this.committedText.trim() : '';
                            inputField.value = base ? `${base} ${liveText}` : liveText;
                            inputField.scrollLeft = inputField.scrollWidth;
                        }
                    } else if (data.type === 'final') {
                        // Tamamlanan cümle kullanıcının metnine kalıcı olarak eklenir
                        const finalChunk = (data.text || '').trim();
                        if (finalChunk && inputField) {
                            const base = this.committedText ? this.committedText.trim() : '';
                            this.committedText = base ? `${base} ${finalChunk}` : finalChunk;
                            inputField.value = this.committedText;
                            this.lastLiveText = '';
                            inputField.scrollLeft = inputField.scrollWidth;
                        }
                    } else if (data.type === 'error') {
                        console.warn('[STT Hata]:', data.message);
                        this.stop();
                    }
                } catch (e) {
                    console.warn('[STT] WS mesajı çözülemedi:', e);
                }
            };

            this.ws.onerror = (err) => {
                console.error('[STT WS Hata]:', err);
                alert('Orion Router STT WebSocket bağlantısı kurulamadı. Router servisinin çalıştığından emin olun.');
                this.stop();
            };

            this.ws.onclose = () => {
                this.stop();
            };

            // 4. Mikrofon baytlarını canlı olarak WebSocket'e bas
            this.processor.onaudioprocess = (e) => {
                if (!this.isStreaming || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;

                const inputData = e.inputBuffer.getChannelData(0);
                const pcm16 = this.downsampleTo16kPCM(inputData, inputSampleRate, 16000);
                if (pcm16.length > 0) {
                    try {
                        this.ws.send(pcm16.buffer);
                    } catch (sendErr) {
                        console.debug('[STT] Ses gönderilemedi:', sendErr);
                    }
                }
            };

            source.connect(this.processor);
            this.processor.connect(this.audioCtx.destination);

        } catch (err) {
            console.error('[STT] Başlatma hatası:', err);
            alert(`Mikrofon erişim hatası: ${err.message || err}`);
            this.stop();
        }
    },

    stop() {
        this.isStreaming = false;

        const micBtn = document.getElementById('mic-btn');
        const inputField = document.getElementById('message-input');

        if (micBtn) {
            micBtn.classList.remove('recording');
            const idleIcon = document.getElementById('mic-icon-idle');
            const recIcon = document.getElementById('mic-icon-recording');
            if (idleIcon) idleIcon.style.display = 'block';
            if (recIcon) recIcon.style.display = 'none';
        }
        
        if (inputField) {
            inputField.placeholder = "Mesajınızı yazın veya mikrofona konuşun...";
            this.committedText = inputField.value;
            this.lastLiveText = '';
        }

        if (this.processor) {
            try { this.processor.disconnect(); } catch {}
            this.processor = null;
        }

        if (this.audioCtx) {
            try {
                if (this.audioCtx.state !== 'closed') this.audioCtx.close();
            } catch {}
            this.audioCtx = null;
        }

        if (this.mediaStream) {
            try {
                this.mediaStream.getTracks().forEach(t => t.stop());
            } catch {}
            this.mediaStream = null;
        }

        if (this.ws) {
            try {
                if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
                    this.ws.close();
                }
            } catch {}
            this.ws = null;
        }
    }
};

window.STT = STT;

