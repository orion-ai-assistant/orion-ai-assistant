import * as api from './api.js';
import * as audioPlayer from './audio-player.js';

let currentMode = "clone";
let activeEngine = "";
// A cloned voice's sample is processed again with every sentence: OmniVoice's limits come from /v1/model_info.
let cloneLimits = null;
let cloneSeconds = null;
let stepDefault = 15;
let cfgDefault = 2.0;
let trimSaveTimer = null;
const TEMP_DEFAULT = 0.9;

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------
async function init() {
    try {
        const info = await api.getModelInfo();
        activeEngine = info.engine;
        const usesVoxCPM2 = activeEngine.startsWith('voxcpm2');
        document.getElementById('cloneTextGroup')?.classList.toggle('hidden', usesVoxCPM2);
        document.getElementById('cloneTextNotice')?.classList.toggle('hidden', !usesVoxCPM2);
        cloneLimits = info.clone || null;
        const hint = document.getElementById('cloneHint');
        if (!cloneLimits) hint?.classList.add('hidden');
        else if (hint) hint.textContent = `Yaklaşık ${cloneLimits.recommended_seconds} saniyelik temiz bir kayıt önerilir. `
            + `Örnek her konuşmada yeni cümleyle birlikte işlenir: daha uzun örnekler her konuşmanın süresini uzatır `
            + `(en çok ${cloneLimits.max_seconds} saniye).`;
        const lowVramText = info.low_vram ? 'ACIK' : 'KAPALI';
        const idleCleanupText = Number.isFinite(info.idle_cleanup_mins) ? `${info.idle_cleanup_mins} dk` : '-';
        const devText = info.device || 'NVIDIA CUDA (GPU)';
        document.getElementById('activeModelDisplay').innerText =
            `Aktif Model: ${activeEngine.toUpperCase()} | Donanım: ${devText} | VRAM Tasarrufu: ${lowVramText}`;

        // Guidance: VoxCPM2 gets louder and louder above 4 (it shouts and clicks), so 1-4 with 3 by default there;
        // OmniVoice keeps 1-10 with 2.
        // VoxCPM2's "Yaratıcılık" is the Router's 0-2 scale (0 -> guidance 2, 1 -> 3, 2 -> 4; sent as `creativity`);
        // OmniVoice keeps its guidance of 1-10 with 2.
        const cfg = document.getElementById('cfgInput');
        if (cfg) {
            const vox = activeEngine.startsWith('voxcpm2');
            cfg.min = vox ? '0' : '1.0';
            cfg.max = vox ? '2' : '10.0';
            cfg.step = '0.1';
            cfgDefault = vox ? 1.0 : 2.0;  // VoxCPM2: creativity 1 = guidance 3, the engine's default
            cfg.value = String(cfgDefault);
            document.getElementById('cfgVal').innerText = "Varsayılan";
            const label = document.getElementById('cfgLabel');
            if (label) label.firstChild.textContent = vox ? 'Yaratıcılık (0-2): ' : 'Yaratıcılık (Guidance Scale): ';
        }
        // Steps: the engine's own default is where the slider starts and says "Varsayılan" (VoxCPM2 10, OmniVoice 15).
        stepDefault = activeEngine.startsWith('voxcpm2') ? 10 : 15;
        const step = document.getElementById('stepInput');
        if (step) step.value = String(stepDefault);
        // Temperature: VoxCPM2 only (0.5-0.9, 0.9 default); VoxCPM2 has no speed setting.
        const isVox = activeEngine.startsWith('voxcpm2');
        document.getElementById('tempContainer')?.classList.toggle('hidden', !isVox);
        document.getElementById('speedContainer')?.classList.toggle('hidden', isVox);
        document.getElementById('leadingTrimContainer')?.classList.toggle('hidden', !isVox);
        if (isVox) {
            try {
                const settings = await api.getAudioSettings();
                const input = document.getElementById('leadingTrimInput');
                input.value = String(settings.leading_trim_ms);
                document.getElementById('leadingTrimVal').innerText = String(settings.leading_trim_ms);
            } catch (e) { console.warn('Başlangıç kırpma ayarı okunamadı:', e); }
        }

        if (activeEngine.startsWith('voxcpm2')) {
            document.getElementById('langContainer')?.classList.add('hidden');
            document.getElementById('voxControls')?.classList.remove('hidden');
            document.getElementById('omniControls')?.classList.add('hidden');
        } else {
            document.getElementById('langContainer')?.classList.remove('hidden');
            document.getElementById('omniControls')?.classList.remove('hidden');
            document.getElementById('voxControls')?.classList.add('hidden');
            loadLanguages();
            loadDesignOptions();
        }
    } catch (e) { console.error("Init fail", e); }

    loadVoices();
    setupEventListeners();
    showPrime('primeBoxDefault', 'primeAudioDefault', '');
    const sel = document.getElementById('voiceSelect');
    if (sel) sel.addEventListener('change', () => showPrime('primeBoxClone', 'primeAudioClone', sel.value));
}

async function loadLanguages() {
    try {
        console.log("Fetching languages from API...");
        const data = await api.getLanguages();
        console.log("Languages API response:", data);
        
        const select = document.getElementById('langInput');
        if (!select) return;

        if (data && Array.isArray(data.languages) && data.languages.length > 0) {
            // Build the options as a single string for better performance
            const optionsHTML = data.languages.map(l => {
                const label = (l === 'Auto') ? 'Otomatik Algıla (Auto)' : l;
                return `<option value="${l}">${label}</option>`;
            }).join('');
            
            select.innerHTML = optionsHTML;
            console.log(`Successfully populated ${data.languages.length} languages.`);
        } else {
            console.warn("No languages returned from API or invalid format.");
        }
    } catch (e) {
        console.error("Error loading languages:", e);
    }
}

async function loadDesignOptions() {
    try {
        console.log("Fetching design options from API...");
        const data = await api.getDesignOptions();
        console.log("Design options API response:", data);
        
        const selectors = {
            vdGender: { key: 'gender', defaultText: 'Cinsiyet (Auto)' },
            vdAge: { key: 'age', defaultText: 'Yaş Grubu (Auto)' },
            vdPitch: { key: 'pitch', defaultText: 'Ton (Auto)' },
            vdStyle: { key: 'style', defaultText: 'Stil (Auto)' },
            vdAccent: { key: 'accent', defaultText: 'Aksan Yok / Standart (Auto)' },
            vdDialect: { key: 'dialect', defaultText: 'Çince Lehçe (Auto)' }
        };

        for (const [id, cfg] of Object.entries(selectors)) {
            const select = document.getElementById(id);
            if (!select) continue;
            
            const list = data[cfg.key];
            if (list && Array.isArray(list)) {
                // Keep the default option
                while (select.options.length > 1) {
                    select.remove(1);
                }
                
                list.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    select.appendChild(opt);
                });
                console.log(`Successfully populated ${list.length} options for ${id}`);
            }
        }
    } catch (e) {
        console.error("Error loading design options:", e);
    }
}

// The clone tab previews the exact stored reference; the design tab previews the default warm-up.
function showPrime(boxId, audioId, voice) {
    const box = document.getElementById(boxId), audio = document.getElementById(audioId);
    if (!box || !audio) return;
    if (!activeEngine.startsWith('voxcpm2')) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden');
    audio.pause();
    audio.src = `/v1/prime?voice=${encodeURIComponent(voice || '')}&t=${Date.now()}`;
}

async function loadVoices() {
    try {
        const data = await api.getVoices();
        const select = document.getElementById('voiceSelect');
        if (!select) return;
        while (select.options.length > 1) select.remove(1);
        data.voices.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v;
            opt.textContent = v;
            select.appendChild(opt);
        });
    } catch (e) { }
}

// ---------------------------------------------------------------------------
// UI Handlers
// ---------------------------------------------------------------------------
function setupEventListeners() {
    const safeSetClick = (id, fn) => {
        const el = document.getElementById(id);
        if (el) el.onclick = fn;
    };

    safeSetClick('tab-clone', (e) => switchMode('clone', e.currentTarget));
    safeSetClick('tab-design', (e) => switchMode('design', e.currentTarget));
    safeSetClick('generateBtn', () => startGeneration(false));
    safeSetClick('generateStreamBtn', () => startGeneration(true));
    safeSetClick('cloneBtn', handleClone);
    safeSetClick('delVoiceBtn', handleDeleteVoice);
    safeSetClick('savePrimeBtn', saveDefaultPrime);
    const cloneFile = document.getElementById('cloneFile');
    if (cloneFile) cloneFile.onchange = showCloneDuration;
    
    // Toggle buttons for advanced sections
    document.querySelectorAll('.toggle-btn').forEach(btn => {
        btn.onclick = () => {
            const targetId = btn.dataset.target;
            document.getElementById(targetId)?.classList.toggle('hidden');
        };
    });

    // Update display spans for ranges
    ['speed', 'cfg', 'step', 'temp'].forEach(id => {
        const input = document.getElementById(`${id}Input`);
        const display = document.getElementById(`${id}Val`);
        if (input && display) {
            input.oninput = () => {
                const def = { step: stepDefault, cfg: cfgDefault, temp: TEMP_DEFAULT }[id];
                display.innerText = def !== undefined && parseFloat(input.value) === def ? "Varsayılan" : input.value;
            };
        }
    });

    const trimInput = document.getElementById('leadingTrimInput');
    if (trimInput) {
        trimInput.oninput = () => {
            const value = Number(trimInput.value);
            document.getElementById('leadingTrimVal').innerText = String(value);
            const status = document.getElementById('leadingTrimStatus');
            status.innerText = 'Uygulanıyor…';
            clearTimeout(trimSaveTimer);
            trimSaveTimer = setTimeout(async () => {
                try {
                    await api.setAudioSettings({ leading_trim_ms: value });
                    status.innerText = `Uygulandı: ${value} ms`;
                } catch (e) { status.innerText = e.message; }
            }, 180);
        };
    }

    // Every engine keeps its own settings: what was set for one is not carried over to the other (per browser).
    // v2: the defaults changed (VoxCPM2 creativity 1, steps 10); values kept from before are not carried over.
    const store = id => `orionTts:v2:${activeEngine}:${id}`;
    ['speed', 'cfg', 'step', 'temp', 'seed'].forEach(id => {
        const input = document.getElementById(`${id}Input`);
        if (!input) return;
        try {
            const saved = localStorage.getItem(store(id));
            if (saved !== null && saved !== '') {
                input.value = saved;
                input.oninput?.();
            }
        } catch {}
        const keep = () => { try { localStorage.setItem(store(id), input.value); } catch {} };
        const shown = input.oninput;
        input.oninput = () => { shown?.(); keep(); };
        input.onchange = keep;
    });

    // Manual controls visibility for streaming
    const streamAuto = document.getElementById('streamAutoMode');
    if (streamAuto) {
        streamAuto.onchange = (e) => {
            document.getElementById('streamManualControls')?.classList.toggle('hidden', e.target.checked);
        };
    }
}

async function saveDefaultPrime() {
    const button = document.getElementById('savePrimeBtn');
    const status = document.getElementById('primeSaveStatus');
    const text = document.getElementById('textInput').value;
    if (!text.trim()) {
        status.innerText = 'Önce söyletmek istediğiniz metni yazın.';
        return;
    }
    button.disabled = true;
    status.innerText = 'Ses hazırlanıyor…';
    try {
        const seedInput = document.getElementById('seedInput');
        let seed = parseInt(seedInput.value, 10);
        // Resolve "random" once, so full speech generation can use the identical seed afterwards.
        if (!Number.isFinite(seed) || seed === -1) {
            seed = crypto.getRandomValues(new Uint32Array(1))[0] & 0x7fffffff;
            seedInput.value = String(seed);
            seedInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
        const response = await api.savePrime({
            text,
            design: document.getElementById('voxDescInput')?.value || '',
            seed,
            creativity: parseFloat(document.getElementById('cfgInput').value),
            steps: parseInt(document.getElementById('stepInput').value, 10),
            temperature: parseFloat(document.getElementById('tempInput').value)
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || 'Hazırlık sesi kaydedilemedi.');
        }
        const audio = document.getElementById('primeAudioDefault');
        const oldUrl = audio.dataset.objectUrl;
        if (oldUrl) URL.revokeObjectURL(oldUrl);
        const url = URL.createObjectURL(await response.blob());
        audio.dataset.objectUrl = url;
        audio.src = url;
        audio.load();
        status.innerText = `Kaydedildi; aynı seed ile tam üretimde de kullanılabilir (${seed}).`;
    } catch (e) { status.innerText = e.message; }
    finally { button.disabled = false; }
}

function switchMode(mode, btn) {
    currentMode = mode;
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById('mode-' + mode).classList.add('active');
    btn.classList.add('active');
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
async function startGeneration(isStream) {
    const text = document.getElementById('textInput').value;
    if (!text) return alert("Lütfen metin girin.");

    let finalVoice = "", finalInstruct = "";

    if (currentMode === "clone") {
        finalVoice = document.getElementById('voiceSelect').value;
        if (!finalVoice) return alert("Klon modu için bir ses seçmelisiniz.");
    } else {
        if (activeEngine.startsWith('voxcpm2')) {
            finalInstruct = document.getElementById('voxDescInput').value;
        } else {
            const params = ['vdGender', 'vdAge', 'vdPitch', 'vdStyle', 'vdAccent', 'vdDialect']
                .map(id => document.getElementById(id).value).filter(Boolean);
            finalInstruct = params.join(", ");
        }
    }

    const langValue = document.getElementById('langInput').value;
    const payload = {
        input: text,
        voice: finalVoice,
        model: finalInstruct,
        speed: parseFloat(document.getElementById('speedInput').value),
        ...(activeEngine.startsWith('voxcpm2')
            ? { creativity: parseFloat(document.getElementById('cfgInput').value) }
            : { guidance_scale: parseFloat(document.getElementById('cfgInput').value) }),
        steps: parseInt(document.getElementById('stepInput').value),
        ...(activeEngine.startsWith('voxcpm2')
            ? { temperature: parseFloat(document.getElementById('tempInput').value) } : {}),
        seed: parseInt(document.getElementById('seedInput').value),
        language: langValue === "Auto" ? null : langValue,
        stream: isStream
    };

    const btn = document.getElementById('generateBtn');
    const streamBtn = document.getElementById('generateStreamBtn');
    const loader = document.getElementById('genLoader');

    btn.disabled = true; streamBtn.disabled = true; loader.style.display = "block";
    document.getElementById('ttfbMetric').innerText = "";
    document.getElementById('totalTimeMetric').innerText = "";
    audioPlayer.stopAudio();

    const startTime = performance.now();

    try {
        const res = await api.generateSpeech(payload);
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "Üretim başarısız");
        }

        document.getElementById('resultContainer').style.display = "block";

        const contentType = (res.headers.get('content-type') || '').toLowerCase();
        const treatAsStream = isStream && contentType.includes('audio/pcm');

        if (treatAsStream) {
            document.getElementById('audioPlayer').style.display = "none";
            const downloadLink = document.getElementById('downloadLink');
            downloadLink.style.display = "none";
            if (downloadLink.dataset.objectUrl) {
                URL.revokeObjectURL(downloadLink.dataset.objectUrl);
                delete downloadLink.dataset.objectUrl;
            }

            const isAuto = document.getElementById('streamAutoMode').checked;
            let PROBE, MULT, MIN, MAX;

            if (isAuto) {
                const charCount = text.length;
                // Empirik testlere göre: 65 karakter için PROBE=1000, MIN=1.5 yetti.
                // 126 karakter için ~2.9s gerekti → sınırı 120'ye çektik.
                if (charCount < 120)      { PROBE = 1000; MULT = 1.5; MIN = 1.5; MAX = 5.0; }
                else if (charCount < 350) { PROBE = 1500; MULT = 1.5; MIN = 2.5; MAX = 8.0; }
                else                      { PROBE = 2500; MULT = 1.5; MIN = 4.0; MAX = 15.0; }
            } else {
                PROBE = parseInt(document.getElementById('streamProbeMs').value) || 500;
                MULT = parseFloat(document.getElementById('streamMultiplier').value) || 1.5;
                MIN = parseFloat(document.getElementById('streamMinBuffer').value) || 0.3;
                MAX = parseFloat(document.getElementById('streamMaxBuffer').value) || 6.0;
            }

            const streamBlob = await audioPlayer.playStream(res, text, startTime, PROBE, MULT, MIN, MAX, 0.15);
            const streamUrl = URL.createObjectURL(streamBlob);
            downloadLink.href = streamUrl;
            downloadLink.download = 'speech-stream.wav';
            downloadLink.dataset.objectUrl = streamUrl;
            downloadLink.style.display = "block";
        } else {
            document.getElementById('audioPlayer').style.display = "block";
            document.getElementById('downloadLink').style.display = "block";
            const blob = await res.blob();
            const total = ((performance.now() - startTime) / 1000).toFixed(2);
            document.getElementById('totalTimeMetric').innerText = `Toplam Süre: ${total}s`;
            document.getElementById('ttfbMetric').innerText = `Hazırlanma: ${total}s`;
            const url = URL.createObjectURL(blob);
            const player = document.getElementById('audioPlayer');
            player.src = url;
            document.getElementById('downloadLink').href = url;
            player.play();
        }
    } catch (e) { alert(e.message); }
    finally { btn.disabled = false; streamBtn.disabled = false; loader.style.display = "none"; }
}

// The chosen file's length, read in the browser: shown at once, with a warning past the recommendation.
async function showCloneDuration() {
    const line = document.getElementById('cloneDuration');
    const file = document.getElementById('cloneFile').files[0];
    cloneSeconds = null;
    line.style.display = "none";
    if (!file || !cloneLimits) return;
    try {
        const context = new AudioContext();
        const audio = await context.decodeAudioData(await file.arrayBuffer());
        context.close();
        if (document.getElementById('cloneFile').files[0] !== file) return;  // Another file was chosen meanwhile.
        cloneSeconds = audio.duration;
    } catch (e) { return; }  // The server reads the file anyway and says what is wrong with it.
    const seconds = cloneSeconds.toFixed(1);
    if (cloneSeconds > cloneLimits.max_seconds) {
        line.style.color = "#ef4444";
        line.textContent = `Süre: ${seconds} sn. En çok ${cloneLimits.max_seconds} saniye olabilir; daha kısa bir parça seçin.`;
    } else if (cloneSeconds > cloneLimits.recommended_seconds + 1) {
        line.style.color = "#f59e0b";
        line.textContent = `Süre: ${seconds} sn. Kabul edilir, ama her konuşma daha yavaş olur; yaklaşık ${cloneLimits.recommended_seconds} saniye önerilir.`;
    } else {
        line.style.color = "#10b981";
        line.textContent = `Süre: ${seconds} sn.`;
    }
    line.style.display = "block";
}

async function handleClone() {
    const btn = document.getElementById('cloneBtn');
    const loader = document.getElementById('cloneLoader');
    const name = document.getElementById('cloneName').value.trim();
    const text = activeEngine.startsWith('voxcpm2') ? '' : document.getElementById('cloneText').value;
    const file = document.getElementById('cloneFile').files[0];

    if (!name || !file) return alert("İsim ve dosya gerekli.");
    if (cloneLimits && cloneSeconds > cloneLimits.max_seconds) {
        return alert(`Ses örneği ${cloneSeconds.toFixed(1)} saniye; en çok ${cloneLimits.max_seconds} saniye olabilir.`);
    }

    btn.disabled = true; loader.style.display = "block";
    const formData = new FormData();
    formData.append('name', name); formData.append('file', file); formData.append('text', text);

    try {
        const res = await api.cloneVoice(formData);
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail || "Klonlama başarısız");
        const status = document.getElementById('cloneStatus');
        status.style.color = body.warning ? "#f59e0b" : "#10b981";
        status.textContent = body.warning || "Ses tensöre dönüştürüldü ve kaydedildi!";
        status.style.display = "block";
        setTimeout(() => status.style.display = "none", body.warning ? 8000 : 3000);
        loadVoices();
    } catch (e) { alert(e.message); }
    finally { btn.disabled = false; loader.style.display = "none"; }
}

async function handleDeleteVoice() {
    const name = document.getElementById('voiceSelect').value;
    if (!name) return alert("Silmek için bir ses seçin.");
    if (!confirm(`'${name}' isimli klon sesi silmek istediğinize emin misiniz?`)) return;
    if (await api.deleteVoice(name)) {
        alert("Ses başarıyla silindi.");
        loadVoices();
    } else {
        alert("Silme başarısız.");
    }
}

document.addEventListener('DOMContentLoaded', init);
