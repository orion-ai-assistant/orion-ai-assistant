/** Recordings grow independently of local playback and keep one set of controls. */
const PcmSpeech = window.PcmSpeech = {
    sessionId: crypto.randomUUID(), context: null, stream: null,
    recordings: new Map(), nodes: new Set(), blocked: new Set(), timer: null,
    unlock() {
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) return;
        this.context ??= new Context();
        this.context.resume().catch(() => {});
    },
    position(s) {
        if (s.playing && this.context && s.timeline) {
            for (const segment of s.timeline) {
                if (this.context.currentTime < segment.start) break;
                s.position = Math.min(segment.offset + Math.max(0, this.context.currentTime - segment.start), segment.offset + segment.duration);
            }
        }
        return Math.min(s.position || 0, s.bytes / 48000);
    },
    update(s) {
        if (!s.controls) return;
        const {button, slider, time, stop} = s.controls;
        stop.hidden = Boolean(s.file || s.ended);
        const duration = s.file ? s.file.duration || 0 : s.bytes / 48000;
        const pos = s.file ? s.file.currentTime : this.position(s);
        const fmt = n => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
        button.textContent = (s.file ? !s.file.paused : s.playing) ? '❚❚' : '▶';
        button.setAttribute('aria-label', (s.file ? !s.file.paused : s.playing) ? 'Duraklat' : 'Sesli yanıtı oynat');
        slider.max = String(duration || 1); slider.value = String(Math.min(pos, duration));
        slider.disabled = duration <= 0;
        time.textContent = `${fmt(pos)} / ${fmt(duration)}`;
    },
    mount(s, target, arrival) {
        if (!target || s.controls) return;
        const box = document.createElement('div'); box.className = 'audio-player-container';
        const row = document.createElement('div'); row.className = 'pcm-player';
        const button = document.createElement('button'); button.type = 'button';
        const time = document.createElement('span'); time.className = 'pcm-time';
        const slider = document.createElement('input'); slider.type = 'range'; slider.min = '0'; slider.step = '0.01'; slider.setAttribute('aria-label', 'Ses konumu');
        button.onclick = () => {
            if (s.file) {
                if (s.file.paused) { this.stop(); document.querySelectorAll('audio').forEach(a => { if (a !== s.file) a.pause(); }); s.file.play().catch(() => {}); }
                else s.file.pause();
            } else if (s.playing) this.stop(); else this.play(s);
        };
        slider.oninput = () => {
            if (s.file) s.file.currentTime = Number(slider.value);
            else { const resume = s.playing; if (resume) this.stop(); s.position = Number(slider.value); if (resume && (!s.ended || s.position < s.bytes / 48000)) this.play(s); }
            this.update(s);
        };
        const stop = document.createElement('button'); stop.type = 'button'; stop.textContent = '■'; stop.setAttribute('aria-label', 'Durdur');
        stop.onclick = () => { if (this.stream === s) this.stop(); if (AppState.isStreamingTurn(s.chat, s.id)) API.stopGeneration(); };
        row.appendChild(button); row.appendChild(time); row.appendChild(slider); row.appendChild(stop); box.appendChild(row);
        if (arrival != null) {
            const timing = document.createElement('div'); timing.className = 'audio-arrival-time';
            timing.textContent = `♫ ${(arrival / 1000).toFixed(2)} ${OrionI18n.t('secondsShort')}`; box.appendChild(timing);
        }
        target.appendChild(box); s.controls = {box, button, slider, time, stop}; this.update(s);
    },
    recording(chatId, data, autoplay, target) {
        const live = data.stream_id && this.recordings.get(data.stream_id);
        if (live && live.controls?.box.parentElement === target) {
            if (!live.valid) {
                const audio = document.createElement('audio');
                const format = data.format || 'wav';
                audio.src = `data:${format === 'mp3' ? 'audio/mpeg' : 'audio/' + format};base64,${data.audio}`;
                live.file = audio;
                live.controls.box.appendChild(audio);
                for (const event of ['loadedmetadata', 'timeupdate', 'play', 'pause', 'ended']) audio.addEventListener(event, () => this.update(live));
            }
            this.update(live); return;
        }
        if (target.querySelector('.audio-player-container')) return;
        const audio = document.createElement('audio');
        const format = data.format || 'wav';
        audio.src = `data:${format === 'mp3' ? 'audio/mpeg' : 'audio/' + format};base64,${data.audio}`;
        const s = {file: audio, bytes: 0}; this.mount(s, target, data.arrival_ms);
        for (const event of ['loadedmetadata', 'timeupdate', 'play', 'pause', 'ended']) audio.addEventListener(event, () => this.update(s));
        s.controls.box.appendChild(audio);
        if (autoplay && data.status !== 'stopped') { this.stop(); audio.play().catch(() => {}); }
    },
    stop(block = true) {
        const s = this.stream;
        if (s) { this.position(s); s.playing = false; if (block) this.blocked.add(s.id); }
        for (const node of this.nodes) { node.onended = null; try { node.stop(); } catch (_) {} node.disconnect(); }
        this.nodes.clear(); this.stream = null;
        if (this.timer) clearInterval(this.timer); this.timer = null;
        if (s) this.update(s);
    },
    start(event) {
        const d = event.data;
        if (event.chat_id !== AppState.currentChatId || this.recordings.has(d.stream_id)
            || d.encoding !== 'pcm_s16le' || d.sample_rate !== 24000 || d.channels !== 1 || d.bits_per_sample !== 16) return;
        const s = {id: d.stream_id, chat: event.chat_id, sequence: 0, bytes: 0, chunks: [], sent: 0, position: 0, ended: false, valid: true, playing: false};
        this.recordings.set(s.id, s);
        this.mount(s, UI._chatDivs[event.chat_id]?.botDiv, d.arrival_ms);
        if (d.origin_session_id === this.sessionId && !this.blocked.has(s.id)
            && String(UI.currentSettings?.tts_enabled ?? localStorage.getItem('orion_tts_enabled')).toLowerCase() === 'true') this.play(s);
    },
    play(s) {
        if (!s.valid || (s.ended && s.bytes === 0)) return;
        this.stop(); document.querySelectorAll('audio').forEach(a => a.pause()); this.unlock();
        if (!this.context || this.context.state !== 'running') return;
        if (s.ended && s.position >= s.bytes / 48000) s.position = 0;
        this.stream = s; s.playing = true; s.sent = Math.floor(s.position * 48000) & ~1; s.nextTime = 0; s.timeline = [];
        this.schedule(s.ended);
        this.timer = setInterval(() => this.update(s), 50); this.update(s);
    },
    chunk(event) {
        const d = event.data, s = this.recordings.get(d.stream_id);
        if (!s || s.ended || !s.valid || event.chat_id !== s.chat) return;
        try {
            if (d.sequence !== s.sequence) throw Error('sequence');
            const raw = atob(d.chunk);
            if (raw.length % 2 || s.bytes + raw.length > 24000000) throw Error('size');
            const bytes = Uint8Array.from(raw, ch => ch.charCodeAt(0));
            s.sequence++; s.bytes += bytes.length; s.chunks.push(bytes);
            if (s.playing) this.schedule(); this.update(s);
        } catch (_) { s.valid = false; s.ended = true; if (this.stream === s) this.stop(); }
    },
    schedule(force = false) {
        const s = this.stream;
        if (!s || s.sent >= s.bytes || (!force && s.sent === 0 && s.bytes < 8640)) return;
        let cursor = 0;
        for (const bytes of s.chunks) {
            const offset = Math.max(0, s.sent - cursor); cursor += bytes.length;
            if (offset >= bytes.length) continue;
            const buffer = this.context.createBuffer(1, (bytes.length - offset) / 2, 24000);
            const samples = buffer.getChannelData(0), view = new DataView(bytes.buffer, bytes.byteOffset + offset, bytes.byteLength - offset);
            for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
            const node = this.context.createBufferSource(); node.buffer = buffer; node.connect(this.context.destination);
            const start = Math.max(this.context.currentTime + 0.015, s.nextTime);
            s.timeline.push({start, offset: s.sent / 48000, duration: buffer.duration});
            s.nextTime = start + buffer.duration; s.sent += bytes.length - offset;
            this.nodes.add(node);
            node.onended = () => { this.nodes.delete(node); node.disconnect(); this.update(s); if (this.stream === s && s.ended && !this.nodes.size) this.stop(false); };
            node.start(start);
        }
    },
    end(event) {
        const d = event.data, s = this.recordings.get(d.stream_id);
        if (!s) return;
        s.ended = true;
        if (d.chunk_count !== s.sequence || d.total_bytes !== s.bytes) s.valid = false;
        if (this.stream === s) {
            if (d.status !== 'completed' || !s.valid) this.stop();
            else { this.schedule(true); if (!this.nodes.size) this.stop(false); }
        }
        this.update(s);
    },
};
window.addEventListener('pagehide', () => PcmSpeech.stop());
