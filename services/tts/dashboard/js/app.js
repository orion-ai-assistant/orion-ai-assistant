import * as api from './api.js';
import * as audioPlayer from './audio-player.js';

let currentMode = "clone";
let activeEngine = "";

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------
async function init() {
    try {
        const info = await api.getModelInfo();
        activeEngine = info.engine;
        const lowVramText = info.low_vram ? 'ACIK' : 'KAPALI';
        const idleCleanupText = Number.isFinite(info.idle_cleanup_mins) ? `${info.idle_cleanup_mins} dk` : '-';
        document.getElementById('activeModelDisplay').innerText =
            `Aktif Model: ${activeEngine.toUpperCase()} | VRAM Tasarrufu: ${lowVramText} | Bos Temizleme: ${idleCleanupText}`;

        if (activeEngine === 'voxcpm2') {
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
    
    // Toggle buttons for advanced sections
    document.querySelectorAll('.toggle-btn').forEach(btn => {
        btn.onclick = () => {
            const targetId = btn.dataset.target;
            document.getElementById(targetId)?.classList.toggle('hidden');
        };
    });

    // Update display spans for ranges
    ['speed', 'cfg', 'step'].forEach(id => {
        const input = document.getElementById(`${id}Input`);
        const display = document.getElementById(`${id}Val`);
        if (input && display) {
            input.oninput = () => {
                display.innerText = id === 'step' && input.value == 15 ? "Varsayılan" : input.value;
            };
        }
    });

    // Manual controls visibility for streaming
    const streamAuto = document.getElementById('streamAutoMode');
    if (streamAuto) {
        streamAuto.onchange = (e) => {
            document.getElementById('streamManualControls')?.classList.toggle('hidden', e.target.checked);
        };
    }
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
        if (activeEngine === 'voxcpm2') {
            finalInstruct = document.getElementById('voxDescInput').value;
        } else {
            const params = ['vdGender', 'vdAge', 'vdPitch', 'vdStyle', 'vdAccent', 'vdDialect']
                .map(id => document.getElementById(id).value).filter(Boolean);
            finalInstruct = params.join(", ");
        }
        if (!finalInstruct && activeEngine === 'voxcpm2') finalInstruct = "A clear voice";
    }

    const langValue = document.getElementById('langInput').value;
    const payload = {
        input: text,
        voice: finalVoice,
        model: finalInstruct,
        speed: parseFloat(document.getElementById('speedInput').value),
        guidance_scale: parseFloat(document.getElementById('cfgInput').value),
        steps: parseInt(document.getElementById('stepInput').value),
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
            document.getElementById('downloadLink').style.display = "none";

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

            await audioPlayer.playStream(res, text, startTime, PROBE, MULT, MIN, MAX, 0.15);
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

async function handleClone() {
    const btn = document.getElementById('cloneBtn');
    const loader = document.getElementById('cloneLoader');
    const name = document.getElementById('cloneName').value;
    const text = document.getElementById('cloneText').value;
    const file = document.getElementById('cloneFile').files[0];

    if (!name || !file) return alert("İsim ve dosya gerekli.");

    btn.disabled = true; loader.style.display = "block";
    const formData = new FormData();
    formData.append('name', name); formData.append('file', file); formData.append('text', text);

    try {
        const res = await api.cloneVoice(formData);
        if (!res.ok) throw new Error("Klonlama başarısız");
        document.getElementById('cloneStatus').style.display = "block";
        setTimeout(() => document.getElementById('cloneStatus').style.display = "none", 3000);
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
