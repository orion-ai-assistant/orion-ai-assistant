document.addEventListener('DOMContentLoaded', () => {
    // Tab Switching
    const tabLiveBtn = document.getElementById('tab-live');
    const tabBatchBtn = document.getElementById('tab-batch');
    const modeLive = document.getElementById('mode-live');
    const modeBatch = document.getElementById('mode-batch');

    tabLiveBtn.addEventListener('click', () => {
        tabLiveBtn.classList.add('active');
        tabBatchBtn.classList.remove('active');
        modeLive.classList.add('active');
        modeBatch.classList.remove('active');
    });

    tabBatchBtn.addEventListener('click', () => {
        tabBatchBtn.classList.add('active');
        tabLiveBtn.classList.remove('active');
        modeBatch.classList.add('active');
        modeLive.classList.remove('active');
    });

    // ==========================================
    // BATCH MODE (File Upload)
    // ==========================================
    const fileInput = document.getElementById('audioFileInput');
    const fileDropArea = document.getElementById('fileDropArea');
    const fileMsg = document.querySelector('.file-msg');
    const uploadBtn = document.getElementById('uploadBtn');
    const latencyBatchMs = document.getElementById('latencyBatchMs');
    const detectedLanguage = document.getElementById('detectedLanguage');
    
    const finalTextContainer = document.getElementById('finalText');
    const liveTextContainer = document.getElementById('liveText');
    const clearBtn = document.getElementById('clearBtn');
    const statusText = document.getElementById('connectionStatus');

    // Health check pseudo
    setTimeout(() => {
        statusText.innerText = "Orion STT Sunucusu Hazır";
        statusText.style.color = "#4ade80"; // green
    }, 500);

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) {
            fileMsg.innerText = fileInput.files[0].name;
            uploadBtn.disabled = false;
        } else {
            fileMsg.innerText = "Sürükle bırak veya bir ses dosyası (.wav, .mp3) seçin";
            uploadBtn.disabled = true;
        }
    });

    uploadBtn.addEventListener('click', async () => {
        if (fileInput.files.length === 0) return;
        
        uploadBtn.disabled = true;
        uploadBtn.innerText = "İşleniyor...";
        finalTextContainer.innerHTML += "<br/><i>(Dosya işleniyor, lütfen bekleyin...)</i>";

        const formData = new FormData();
        formData.append("file", fileInput.files[0]);
        formData.append("language", "tr");

        try {
            const response = await fetch("/v1/audio/transcriptions", {
                method: "POST",
                body: formData
            });
            const data = await response.json();
            
            finalTextContainer.innerHTML = finalTextContainer.innerHTML.replace("<i>(Dosya işleniyor, lütfen bekleyin...)</i>", "");
            
            if (response.ok) {
                finalTextContainer.innerHTML += `<p><strong>[Dosya]:</strong> ${data.text}</p>`;
                latencyBatchMs.innerText = (data.duration * 1000).toFixed(0) + " ms";
                detectedLanguage.innerText = data.language.toUpperCase();
            } else {
                finalTextContainer.innerHTML += `<p style="color:red">Hata: ${data.detail}</p>`;
            }
        } catch (e) {
            finalTextContainer.innerHTML = finalTextContainer.innerHTML.replace("<i>(Dosya işleniyor, lütfen bekleyin...)</i>", "");
            finalTextContainer.innerHTML += `<p style="color:red">Hata: Sunucuya bağlanılamadı.</p>`;
        }
        
        uploadBtn.disabled = false;
        uploadBtn.innerText = "Deşifre Et (Transcribe)";
        fileInput.value = "";
        fileMsg.innerText = "Sürükle bırak veya bir ses dosyası (.wav, .mp3) seçin";
    });

    clearBtn.addEventListener('click', () => {
        finalTextContainer.innerHTML = '';
        liveTextContainer.innerText = '';
    });

    // ==========================================
    // LIVE MODE (WebSocket)
    // ==========================================
    const startRecordBtn = document.getElementById('startRecordBtn');
    const stopRecordBtn = document.getElementById('stopRecordBtn');
    const latencyLiveMs = document.getElementById('latencyLiveMs');

    let audioContext;
    let mediaStream;
    let processor;
    let ws;
    let isPlayingDebugAudio = false;

    startRecordBtn.addEventListener('click', async () => {
        try {
            mediaStream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false,
                    channelCount: 1,
                    sampleRate: 16000
                }, 
                video: false 
            });
            
            // Setup WebSocket
            const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            ws = new WebSocket(`${protocol}//${window.location.host}/v1/audio/transcriptions/stream`);
            
            ws.onopen = () => {
                statusText.innerText = "Canlı Dinleme Aktif";
                statusText.style.color = "#f59e0b"; // orange/yellow
                startRecordBtn.classList.add('hidden');
                stopRecordBtn.classList.remove('hidden');
                
                // Initialize Audio Context for 16kHz Mono
                audioContext = new (window.AudioContext || window.webkitAudioContext)({
                    sampleRate: 16000
                });
                
                const source = audioContext.createMediaStreamSource(mediaStream);
                // Create ScriptProcessor for capturing audio (buffer size 4096 = 256ms chunk)
                processor = audioContext.createScriptProcessor(4096, 1, 1);
                
                source.connect(processor);
                processor.connect(audioContext.destination);
                
                processor.onaudioprocess = (e) => {
                    if (isPlayingDebugAudio) {
                        return; // Oynatma sırasında mikrofondan veri gönderme
                    }
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        const float32Array = e.inputBuffer.getChannelData(0);
                        // Convert Float32 [-1.0, 1.0] to Int16
                        const int16Array = new Int16Array(float32Array.length);
                        for (let i = 0; i < float32Array.length; i++) {
                            let s = Math.max(-1, Math.min(1, float32Array[i]));
                            int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                        }
                        ws.send(int16Array.buffer);
                    }
                };
            };
            
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                
                if (data.type === "live") {
                    liveTextContainer.innerText = data.text + "...";
                    latencyLiveMs.innerText = (data.duration * 1000).toFixed(0) + " ms";
                } 
                else if (data.type === "final") {
                    liveTextContainer.innerText = "";
                    if (data.text.trim().length > 0) {
                        finalTextContainer.innerHTML += `<p><strong>[Canlı]:</strong> ${data.text}</p>`;
                    }
                    latencyLiveMs.innerText = (data.duration * 1000).toFixed(0) + " ms";
                    
                    // Auto scroll to bottom
                    const box = document.getElementById('transcriptionBox');
                    box.scrollTop = box.scrollHeight;
                }
            };
            
            ws.onclose = () => {
                stopRecording();
            };

        } catch (e) {
            console.error(e);
            alert("Mikrofona erişilemedi veya sunucuya bağlanılamadı.");
        }
    });

    stopRecordBtn.addEventListener('click', () => {
        stopRecording();
    });

    function stopRecording() {
        if (ws) {
            ws.close();
            ws = null;
        }
        if (processor) {
            processor.disconnect();
            processor = null;
        }
        if (audioContext) {
            audioContext.close();
            audioContext = null;
        }
        if (mediaStream) {
            mediaStream.getTracks().forEach(track => track.stop());
            mediaStream = null;
        }
        
        startRecordBtn.classList.remove('hidden');
        stopRecordBtn.classList.add('hidden');
        statusText.innerText = "Orion STT Sunucusu Hazır";
        statusText.style.color = "#4ade80";
        liveTextContainer.innerText = "";
    }
    
    // ==========================================
    // DEBUG AUDIO
    // ==========================================
    const playDebugAudioBtn = document.getElementById('playDebugAudioBtn');
    if (playDebugAudioBtn) {
        playDebugAudioBtn.addEventListener('click', async () => {
            try {
                playDebugAudioBtn.disabled = true;
                const originalText = playDebugAudioBtn.innerText;
                playDebugAudioBtn.innerText = "🔊 Oynatılıyor...";
                isPlayingDebugAudio = true;

                const response = await fetch('/v1/audio/debug/last');
                if (response.ok) {
                    const blob = await response.blob();
                    const audio = new Audio(URL.createObjectURL(blob));
                    
                    const finishPlayback = () => {
                        isPlayingDebugAudio = false;
                        playDebugAudioBtn.disabled = false;
                        playDebugAudioBtn.innerText = originalText;
                    };
                    
                    audio.onended = finishPlayback;
                    audio.onerror = finishPlayback;
                    await audio.play();
                } else {
                    alert("Henüz işlenmiş bir ses yok veya hata oluştu.");
                    isPlayingDebugAudio = false;
                    playDebugAudioBtn.disabled = false;
                    playDebugAudioBtn.innerText = originalText;
                }
            } catch(e) {
                console.error("Audio playback error:", e);
                alert("Ses alınamadı veya oynatılamadı.");
                isPlayingDebugAudio = false;
                playDebugAudioBtn.disabled = false;
                playDebugAudioBtn.innerText = "Dinle (Modele Giden)";
            }
        });
    }
});
