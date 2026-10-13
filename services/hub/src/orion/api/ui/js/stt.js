/**
 * Orion Hub - WebSocket Canlı STT (Speech-to-Text) Yöneticisi
 * Orion Router üzerinden Whisper STT akışını yönetir.
 */
const STT = {
    isStreaming: false,
    isStarting: false,
    _session: 0,
    _cancelStartup: null,
    diagnostics: null,
    _diagnosticTimer: null,
    ws: null,
    audioCtx: null,
    mediaStream: null,
    processor: null,
    committedText: '',
    lastLiveText: '',
    _listenerAttached: false,
    
    // Hub proxy WebSocket adresi
    getRouterWsUrl(authToken, language = 'auto', model = 'local-stt') {
        const host = window.location.host;
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const path = HubPicker.apiPath(`/api/v1/audio/transcriptions/stream?token=${encodeURIComponent(authToken)}&language=${encodeURIComponent(language)}&model=${encodeURIComponent(model)}&provider=local`);
        return `${protocol}//${host}${path}`;
    },

    markListening() {
        if (this.isStreaming || !this.diagnostics?.packets || this.ws?.readyState !== WebSocket.OPEN) return;
        this.isStreaming = true;
        this.isStarting = false;
        const micBtn = document.getElementById('mic-btn');
        if (micBtn) {
            micBtn.classList.add('recording');
            const idleIcon = document.getElementById('mic-icon-idle');
            const recIcon = document.getElementById('mic-icon-recording');
            if (idleIcon) idleIcon.style.display = 'none';
            if (recIcon) recIcon.style.display = 'block';
        }
        const inputField = document.getElementById('message-input');
        if (inputField && !inputField.value) inputField.placeholder = 'Dinleniyor, konuşabilirsiniz...';
    },

    logDiagnostics() {
        if (!this.diagnostics) return;
        const track = this.mediaStream?.getAudioTracks()[0];
        console.debug('[STT] Ses ölçümü', {
            ...this.diagnostics,
            events: { ...this.diagnostics.events },
            contextState: this.audioCtx?.state,
            contextSampleRate: this.audioCtx?.sampleRate,
            trackState: track?.readyState,
            trackMuted: track?.muted,
            websocketState: this.ws?.readyState,
        });
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
        if (this.isStreaming || this.isStarting) {
            this.stop();
        } else {
            await this.start();
        }
    },

    async start() {
        window.PcmSpeech?.stop();
        document.querySelectorAll("audio").forEach(audio => audio.pause());
        if (AppState.currentChatId && AppState.isAnyChatGenerating(AppState.currentChatId)) await API.stopGeneration();
        if (this.isStreaming || this.isStarting) return;
        if (!navigator.mediaDevices?.getUserMedia) {
            alert('Tarayıcınız mikrofon erişimini desteklemiyor veya izin verilmedi.');
            return;
        }
        this.attachInputListener();
        this.isStarting = true;
        const session = ++this._session;
        const inputField = document.getElementById('message-input');

        try {
            // Flutter VoiceInput: ayarlar -> WebSocket -> mikrofon -> record_web.
            const settings = await API.getSettings();
            if (session !== this._session) return;
            if (settings.error) throw new Error('Sesli yazma ayarları alınamadı: ' + settings.error);
            if (settings.stt_enabled === false || settings.stt_enabled === 'false') {
                throw new Error('Canlı sesli yazma ayarlardan kapalı.');
            }
            const sttModel = settings.stt_model || 'local-stt';
            if (sttModel !== 'local-stt') {
                throw new Error('Canlı sesli yazma yalnızca local-stt modelini destekliyor. Ayarlardan yerel modeli seçin.');
            }
            const authToken = Auth.getToken() || '';
            if (!authToken.trim()) throw new Error('Sesli yazma için Hub oturumuna tekrar giriş yapın.');

            const ws = new WebSocket(this.getRouterWsUrl(authToken, 'auto', sttModel));
            this.ws = ws;
            let resolveReady, rejectReady;
            const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
            const cancelStartup = () => rejectReady(new Error('Sesli yazma başlangıcı iptal edildi.'));
            this._cancelStartup = cancelStartup;
            const timeout = setTimeout(() => rejectReady(new Error('Sesli yazma bağlantısı zaman aşımına uğradı.')), 15000);
            ws.onopen = () => {
                if (session !== this._session) return;
                console.log('[STT] Router WebSocket bağlandı.');
                resolveReady();
            };
            ws.onmessage = (event) => {
                if (session !== this._session) return;
                try {
                    const data = JSON.parse(event.data);
                    if (this.diagnostics && Object.hasOwn(this.diagnostics.events, data.type)) {
                        this.diagnostics.events[data.type]++;
                    }
                    if (data.type === 'error') {
                        console.warn('[STT Hata]:', data.message);
                        if (inputField) inputField.value = this.committedText;
                        this.stop();
                        return;
                    }
                    // Flutter'daki gibi boş live olayları da önceki geçici metni temizler.
                    if (data.type === 'live' || data.type === 'final') {
                        const text = (data.text || '').trim();
                        if (data.type === 'final') {
                            this.committedText = [this.committedText, text].filter(Boolean).join(' ');
                            this.lastLiveText = '';
                        } else {
                            this.lastLiveText = text;
                        }
                        if (inputField) {
                            inputField.value = [this.committedText, this.lastLiveText].filter(Boolean).join(' ');
                            if (typeof UI !== 'undefined') UI.resizeMessageInput(true);
                            inputField.scrollLeft = inputField.scrollWidth;
                        }
                    }
                } catch (error) {
                    console.warn('[STT] WS mesajı çözülemedi:', error);
                }
            };
            ws.onerror = () => {
                if (session !== this._session) return;
                rejectReady(new Error('Sesli yazma bağlantısı kurulamadı.'));
                if (this.isStreaming) {
                    this.stop();
                    alert('Sesli yazma bağlantısı kesildi. Tekrar deneyin.');
                }
            };
            ws.onclose = () => {
                if (session !== this._session) return;
                rejectReady(new Error('Sesli yazma bağlantısı sunucu tarafından kapatıldı.'));
                this.stop();
            };
            try {
                await ready;
            } finally {
                clearTimeout(timeout);
                if (this._cancelStartup === cancelStartup) this._cancelStartup = null;
            }
            if (session !== this._session) return;
            this.committedText = inputField?.value || '';
            this.lastLiveText = '';

            const mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    autoGainControl: false,
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: 16000,
                    sampleSize: 16,
                    channelCount: 1,
                }
            });
            if (session !== this._session) {
                mediaStream.getTracks().forEach(track => track.stop());
                return;
            }
            this.mediaStream = mediaStream;
            const track = mediaStream.getAudioTracks()[0];
            const applied = track.getSettings();
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            const audioCtx = new AudioContextClass(
                applied.sampleRate ? { sampleRate: applied.sampleRate } : undefined
            );
            this.audioCtx = audioCtx;
            if (!audioCtx.audioWorklet || !window.AudioWorkletNode) {
                throw new Error('Tarayıcınız AudioWorklet desteklemiyor. Güncel Chrome ve HTTPS veya localhost kullanın.');
            }
            this.diagnostics = {
                microphone: track.label,
                trackSampleRate: applied.sampleRate,
                channelCount: applied.channelCount,
                echoCancellation: applied.echoCancellation,
                noiseSuppression: applied.noiseSuppression,
                autoGainControl: applied.autoGainControl,
                outputSampleRate: 16000,
                inputPacketSamples: 2048,
                packetIntervalMs: 2048 / audioCtx.sampleRate * 1000,
                recorder: 'record_web 1.3.0',
                packets: 0, sentBytes: 0, rms: 0, peak: 0, clippedSamples: 0,
                events: { live: 0, final: 0, error: 0 },
            };
            // Explicitly resume for browser autoplay policies. PCM processing is upstream record_web.
            const resumeResult = audioCtx.resume().then(() => null, error => error);
            await audioCtx.audioWorklet.addModule('js/stt-worklet.js?v=2');
            if (session !== this._session) return;
            const resumeError = await resumeResult;
            if (session !== this._session) return;
            if (resumeError) throw resumeError;
            const source = audioCtx.createMediaStreamSource(mediaStream);
            this.processor = new window.AudioWorkletNode(audioCtx, 'recorder.worklet', {
                parameterData: { numChannels: 1, sampleRate: 16000, streamBufferSize: 2048 },
            });
            this.processor.port.onmessage = ({ data: pcm16 }) => {
                if (session !== this._session || !pcm16?.length) return;
                if (ws.readyState !== WebSocket.OPEN) return;
                let energy = 0, peak = 0, clipped = 0;
                for (const value of pcm16) {
                    const sample = value / 32768;
                    energy += sample * sample;
                    peak = Math.max(peak, Math.abs(sample));
                    if (value === -32768 || value === 32767) clipped++;
                }
                this.diagnostics.packets++;
                this.diagnostics.rms = Math.sqrt(energy / pcm16.length);
                this.diagnostics.peak = Math.max(this.diagnostics.peak, peak);
                this.diagnostics.clippedSamples += clipped;
                try {
                    // record_web sends the same Int16Array buffer directly, without repacketizing.
                    ws.send(pcm16.buffer);
                    this.diagnostics.sentBytes += pcm16.byteLength;
                    this.markListening();
                } catch (error) {
                    console.warn('[STT] Ses gönderilemedi:', error);
                    this.stop();
                }
            };
            this.processor.onprocessorerror = () => {
                if (session !== this._session) return;
                this.stop();
                alert('Mikrofondan ses işlenemedi. Tekrar deneyin.');
            };
            source.connect(this.processor);
            this.processor.connect(audioCtx.destination);
            this._diagnosticTimer = setInterval(() => this.logDiagnostics(), 1000);
        } catch (error) {
            if (session !== this._session) return;
            console.error('[STT] Başlatma hatası:', error);
            this.stop();
            alert(`Sesli yazma başlatılamadı: ${error.message || error}`);
        }
    },

    stop() {
        if (this._diagnosticTimer !== null) {
            clearInterval(this._diagnosticTimer);
            this._diagnosticTimer = null;
            this.logDiagnostics();
        }
        ++this._session;
        this.isStarting = false;
        this.isStreaming = false;
        if (this._cancelStartup) {
            this._cancelStartup();
            this._cancelStartup = null;
        }

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
            if (typeof UI !== 'undefined') UI.resizeMessageInput();
        }

        if (this.processor) {
            this.processor.onprocessorerror = null;
            this.processor.port.onmessage = null;
            this.processor.port.close();
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

