/**
 * UI Manipulation Functions
 */
const UI = {
    chatArea: document.getElementById('chat-area'),
    statusIndicator: document.getElementById('status-indicator'),
    messageInput: document.getElementById('message-input'),
    stopBtn: document.getElementById('stop-btn'),

    // Per-chat state: { chatId -> { botDiv, thinkDiv, thinkBody } }
    _chatDivs: {},

    _getOrCreateChatState(chatId) {
        if (!this._chatDivs[chatId]) {
            this._chatDivs[chatId] = {
                botDiv: null,
                thinkDiv: null,
                thinkBody: null
            };
        }
        return this._chatDivs[chatId];
    },

    scrollToBottom() {
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
    },

    createMetricsElement(firstTokenMs, totalMs) {
        if (!totalMs && !firstTokenMs) return null;
        const meta = document.createElement('div');
        meta.className = 'message-meta';

        const fmtFirst = firstTokenMs == null ? '—' : `${(Number(firstTokenMs) / 1000).toFixed(2)} sn`;
        const fmtTotal = (Number(totalMs || 0) / 1000).toFixed(2);

        meta.innerHTML = `
            <span class="meta-item" title="İlk Token Süresi">
                <span class="meta-icon" aria-hidden="true">⚡</span> <strong>${fmtFirst}</strong>
            </span>
            <span class="meta-item" title="Metnin tamamlanma süresi; ses üretimi dahil değildir">
                <span class="meta-icon" aria-hidden="true">◷</span> <strong>${fmtTotal} sn</strong>
            </span>
        `;
        return meta;
    },

    appendUserMessage(text) {
        const hero = this.chatArea.querySelector('.welcome-hero');
        if (hero) hero.remove();

        const div = document.createElement('div');
        div.className = 'message user';
        div.textContent = text;
        this.chatArea.appendChild(div);
        this.scrollToBottom();
    },

    appendStaticBotMessage(content, thinking = null, metrics = null, audio = null) {
        const hero = this.chatArea.querySelector('.welcome-hero');
        if (hero) hero.remove();

        const div = document.createElement('div');
        div.className = 'message bot';

        if (thinking) {
            const thinkDiv = document.createElement('details');
            thinkDiv.className = 'think-block';
            thinkDiv.open = false;

            const summary = document.createElement('summary');
            summary.className = 'think-summary';
            const charCount = thinking.length;
            summary.innerHTML = `<span class="think-icon">💭</span> Düşünce <span class="think-char-count">(${charCount} karakter)</span>`;
            thinkDiv.appendChild(summary);

            const thinkBody = document.createElement('pre');
            thinkBody.className = 'think-body';
            thinkBody.textContent = thinking;
            thinkDiv.appendChild(thinkBody);

            div.appendChild(thinkDiv);
        }

        const textNode = document.createTextNode(content);
        div.appendChild(textNode);

        if (metrics && (metrics.total_ms || metrics.first_token_ms)) {
            const meta = this.createMetricsElement(metrics.first_token_ms, metrics.total_ms);
            if (meta) div.appendChild(meta);
        }
        if (audio?.audio) this.appendAudio(AppState.currentChatId, audio, false, div);

        this.chatArea.appendChild(div);
        this.scrollToBottom();
    },

    createBotMessagePlaceholder(chatId, isWaiting = false) {
        if (!chatId) return;
        const hero = this.chatArea.querySelector('.welcome-hero');
        if (hero) hero.remove();
        const state = this._getOrCreateChatState(chatId);

        // If there's an existing typing placeholder for this chat, reuse it
        if (state.botDiv && state.botDiv.classList.contains('typing')) {
            state.botDiv.innerHTML = '';
            state.botDiv.classList.remove('typing');
            return;
        }

        state.thinkDiv = null;
        state.thinkBody = null;
        state.botDiv = document.createElement('div');
        state.botDiv.className = 'message bot typing';
        state.botDiv.setAttribute('data-chat-id', chatId);

        if (isWaiting) {
            state.botDiv.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
        }

        // Only append to DOM if this is the active chat
        if (chatId === AppState.currentChatId) {
            this.chatArea.appendChild(state.botDiv);
            this.scrollToBottom();
        }
    },

    appendThinkingToken(chatId, token) {
        if (!chatId) return;
        const state = this._getOrCreateChatState(chatId);

        if (!state.botDiv) {
            this.createBotMessagePlaceholder(chatId);
        }
        if (state.botDiv.classList.contains('typing')) {
            state.botDiv.innerHTML = '';
            state.botDiv.classList.remove('typing');
        }

        if (!state.thinkDiv) {
            state.thinkDiv = document.createElement('details');
            state.thinkDiv.className = 'think-block';
            state.thinkDiv.open = true;

            const summary = document.createElement('summary');
            summary.className = 'think-summary';
            summary.innerHTML = '<span class="think-icon">💭</span> Düşünüyor<span class="think-dots">...</span>';
            state.thinkDiv.appendChild(summary);

            state.thinkBody = document.createElement('pre');
            state.thinkBody.className = 'think-body';
            state.thinkDiv.appendChild(state.thinkBody);

            state.botDiv.appendChild(state.thinkDiv);
        }

        state.thinkBody.appendChild(document.createTextNode(token));
        if (chatId === AppState.currentChatId) {
            this.scrollToBottom();
        }
    },

    appendToken(chatId, token) {
        if (!chatId) return;
        const state = this._getOrCreateChatState(chatId);

        if (!state.botDiv) {
            this.createBotMessagePlaceholder(chatId);
        }

        if (state.botDiv.classList.contains('typing')) {
            state.botDiv.innerHTML = '';
            state.botDiv.classList.remove('typing');
        }

        const textNode = document.createTextNode(token);
        state.botDiv.appendChild(textNode);
        if (chatId === AppState.currentChatId) {
            this.scrollToBottom();
        }
    },

    replaceMessageContent(chatId, content) {
        if (!chatId) return;
        const state = this._getOrCreateChatState(chatId);
        if (!state.botDiv) {
            this.createBotMessagePlaceholder(chatId);
        }
        if (state.botDiv.classList.contains('typing')) {
            state.botDiv.innerHTML = '';
            state.botDiv.classList.remove('typing');
            state.thinkDiv = null;
            state.thinkBody = null;
        }

        Array.from(state.botDiv.childNodes).forEach(node => {
            if (node.nodeType === Node.TEXT_NODE) node.remove();
        });

        const contentNode = document.createTextNode(content || '');
        const trailingElement = state.botDiv.querySelector('.audio-player-container, .message-meta');
        state.botDiv.insertBefore(contentNode, trailingElement || null);
        if (chatId === AppState.currentChatId) {
            this.scrollToBottom();
        }
    },

    appendAudio(chatId, audioData, autoPlay = true, targetDiv = null) {
        if (!chatId || !audioData || !audioData.audio) return;
        const state = this._getOrCreateChatState(chatId);

        const botDiv = targetDiv || state.botDiv;
        if (!botDiv) return;

        // Tekrar aynı mesaja ikinci oynatıcı eklenmesini önle
        if (botDiv.querySelector('.audio-player-container')) return;

        const audioContainer = document.createElement('div');
        audioContainer.className = 'audio-player-container';

        const audioElement = document.createElement('audio');
        audioElement.controls = true;
        audioElement.autoplay = autoPlay && chatId === AppState.currentChatId;
        audioElement.setAttribute('aria-label', 'Sesli yanıtı oynat');
        const fmt = audioData.format || 'wav';
        audioElement.src = `data:audio/${fmt};base64,${audioData.audio}`;

        audioContainer.appendChild(audioElement);
        if (audioData.arrival_ms != null) {
            const timing = document.createElement('div');
            timing.className = 'audio-arrival-time';
            timing.textContent = `♫ ${(audioData.arrival_ms / 1000).toFixed(2)} sn`;
            timing.title = 'Mesaj gönderildikten sonra sesin ulaşma süresi';
            audioContainer.appendChild(timing);
        }
        botDiv.appendChild(audioContainer);

        if (chatId === AppState.currentChatId) {
            this.scrollToBottom();
            if (autoPlay) {
                // Tarayıcı autoplay politikası gereği promise yakalama
                const playPromise = audioElement.play();
                if (playPromise !== undefined) {
                    playPromise.catch(err => {
                        console.warn("Autoplay tarayıcı kullanıcı etkileşimi kuralı nedeniyle engellendi:", err);
                    });
                }
            }
        }
    },

    showTextMetrics(chatId, metrics) {
        const botDiv = this._chatDivs[chatId]?.botDiv;
        if (!botDiv) return;
        const meta = this.createMetricsElement(metrics.first_token_ms, metrics.total_ms);
        if (!meta) return;
        const previous = botDiv.querySelector('.message-meta');
        if (previous) previous.replaceWith(meta);
        else botDiv.appendChild(meta);
    },

    finishGeneration(chatId, hasTokens = true, metrics = null) {
        if (!chatId) return;
        const fallback = AppState.getGenerationMetrics(chatId);
        metrics = { ...metrics };
        for (const key of ['first_token_ms', 'total_ms']) {
            if (metrics[key] === undefined) metrics[key] = fallback?.[key];
        }
        const state = this._chatDivs[chatId];
        const staleTypingDivs = this.chatArea.querySelectorAll(
            `.message.typing[data-chat-id="${CSS.escape(String(chatId))}"]`
        );
        staleTypingDivs.forEach(div => {
            if (!state || div !== state.botDiv) div.remove();
        });
        if (!state) {
            if (chatId === AppState.currentChatId) this.setStopButtonVisible(false);
            return;
        }

        const botDivRef = state.botDiv;

        if (state.thinkDiv) {
            state.thinkDiv.open = false;
            const summary = state.thinkDiv.querySelector('.think-summary');
            if (summary) {
                const charCount = (state.thinkBody?.textContent || '').length;
                summary.innerHTML = `<span class="think-icon">💭</span> Düşünce <span class="think-char-count">(${charCount} karakter)</span>`;
            }
            state.thinkDiv = null;
            state.thinkBody = null;
        }
        if (botDivRef && botDivRef.classList.contains('typing')) {
            botDivRef.innerHTML = hasTokens ? '' : '<em>[Yanıt gelmedi]</em>';
            botDivRef.classList.remove('typing');
        }

        // Add metrics badge from Router to the bot message
        if (botDivRef && (metrics.total_ms || metrics.first_token_ms)) {
            const metaEl = this.createMetricsElement(metrics.first_token_ms, metrics.total_ms);
            if (metaEl && !botDivRef.querySelector('.message-meta')) {
                botDivRef.appendChild(metaEl);
            }
        }

        state.botDiv = null;

        // Update stop button if this is the active chat
        if (chatId === AppState.currentChatId) {
            this.setStopButtonVisible(false);
        }
    },

    setConnectionStatus(isConnected, errorMessage = null) {
        const statusText = document.getElementById('connection-status-text');
        const sendBtn = document.getElementById('send-btn');
        const messageInput = document.getElementById('message-input');
        const stopBtn = document.getElementById('stop-btn');

        if (isConnected) {
            this.statusIndicator.classList.add('connected');
            if (statusText) statusText.textContent = 'Bağlı';
            if (sendBtn) sendBtn.disabled = false;
            if (messageInput) messageInput.disabled = false;
            if (stopBtn) stopBtn.disabled = false;
            this._removeConnectionNotice();
        } else {
            this.statusIndicator.classList.remove('connected');
            const label = errorMessage === 'reconnecting'
                ? 'Bağlantı koptu, yeniden bağlanıyor...'
                : (errorMessage === 'connecting' ? 'Bağlanıyor...' : 'Bağlantı yok');
            if (statusText) statusText.textContent = label;
            if (sendBtn) sendBtn.disabled = true;
            if (messageInput) messageInput.disabled = true;
            if (stopBtn) stopBtn.disabled = true;
            this._showConnectionNotice(label);
        }
    },

    _showConnectionNotice(message) {
        const existing = this.chatArea.querySelector('[data-connection-notice="true"]');
        if (existing) {
            existing.textContent = message;
            return;
        }
        const div = document.createElement('div');
        div.className = 'message bot';
        div.setAttribute('data-connection-notice', 'true');
        div.style.opacity = '0.85';
        div.textContent = message;
        this.chatArea.appendChild(div);
        this.scrollToBottom();
    },

    _removeConnectionNotice() {
        const existing = this.chatArea.querySelector('[data-connection-notice="true"]');
        if (existing) existing.remove();
    },

    setStopButtonVisible(isVisible) {
        isVisible = AppState.isGenerating();
        if (!this.stopBtn) return;
        this.stopBtn.style.display = isVisible ? 'flex' : 'none';
        if (isVisible && AppState.isStopping(AppState.currentChatId)) {
            this.stopBtn.disabled = true;
            this.stopBtn.textContent = 'Durduruluyor...';
            return;
        }
        if (isVisible) {
            this.stopBtn.disabled = false;
            this.stopBtn.innerHTML = '<span class="stop-icon">■</span><span class="stop-text">Durdur</span>';
        }
    },

    clearInput() {
        this.messageInput.value = '';
    },

    handleSettingChange(key) {
        const input = document.getElementById('setting-input-' + key);
        const saveBtn = document.getElementById('setting-save-' + key);
        if (!input || !saveBtn) return;

        const currentVal = input.value.trim();
        const originalVal = (input.dataset.original !== undefined ? input.dataset.original : '').trim();

        if (currentVal !== originalVal) {
            saveBtn.style.display = 'inline-flex';
            saveBtn.classList.add('visible');
            input.classList.add('dirty');
        } else {
            saveBtn.style.display = 'none';
            saveBtn.classList.remove('visible');
            input.classList.remove('dirty');
        }
    },

    async saveSetting(key) {
        const input = document.getElementById('setting-input-' + key);
        const saveBtn = document.getElementById('setting-save-' + key);
        if (!input) return;
        const val = input.value.trim();

        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.innerHTML = `<span>⏳</span> Kaydediliyor...`;
        }

        const resultBlock = document.getElementById('settings-result');
        if (resultBlock) {
            resultBlock.style.display = 'block';
            resultBlock.className = 'settings-toast-banner info';
            resultBlock.textContent = `${key} güncelleniyor...`;
        }

        const data = await API.saveSettings(key, val);

        if (data.error) {
            if (resultBlock) {
                resultBlock.className = 'settings-toast-banner error';
                resultBlock.textContent = `Hata: ${data.error}`;
            }
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Tekrar Dene';
            }
        } else {
            input.dataset.original = val;
            input.classList.remove('dirty');
            input.classList.add('saved-flash');
            setTimeout(() => input.classList.remove('saved-flash'), 1200);

            if (resultBlock) {
                resultBlock.className = 'settings-toast-banner success';
                resultBlock.textContent = `✓ ${key} başarıyla kaydedildi!`;
                setTimeout(() => {
                    if (resultBlock.textContent.includes(key)) {
                        resultBlock.style.display = 'none';
                    }
                }, 3000);
            }

            if (saveBtn) {
                saveBtn.innerHTML = `<span>✓</span> Kaydedildi`;
                saveBtn.classList.add('saved');
                setTimeout(() => {
                    saveBtn.disabled = false;
                    saveBtn.textContent = 'Kaydet';
                    saveBtn.classList.remove('saved', 'visible');
                    saveBtn.style.display = 'none';
                }, 1200);
            }

            if (key === 'tts_enabled') {
                const isEnabled = String(val).toLowerCase() === 'true';
                const audioToggle = document.getElementById('audio-toggle');
                if (audioToggle) {
                    audioToggle.checked = isEnabled;
                }
                localStorage.setItem("orion_tts_enabled", isEnabled ? "true" : "false");
            }
        }
    },

    renderSettings(settings) {
        this.currentSettings = settings;
        const dashboard = document.getElementById('settings-dashboard');
        if (!dashboard) return;

        if (!settings || settings.error) {
            dashboard.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--danger-color);">Ayarlar yüklenemedi.</div>';
            return;
        }

        // Eğer zaten render edilmişse sadece değişmemiş olan değerleri güncelle
        const alreadyRendered = Boolean(dashboard.querySelector('[id^="setting-input-"]'));
        if (alreadyRendered) {
            Object.keys(settings).forEach(key => {
                const el = document.getElementById(`setting-input-${key}`);
                const saveBtn = document.getElementById(`setting-save-${key}`);
                if (el && document.activeElement !== el && !el.classList.contains('dirty')) {
                    const serverVal = settings[key] !== undefined ? String(settings[key]) : '';
                    if (key === 'tts_enabled' || key === 'ai_chat_titles_enabled' || key === 'stt_enabled') {
                        el.value = String(settings[key]).toLowerCase() === 'true' ? 'true' : 'false';
                        el.dataset.original = el.value;
                    } else {
                        if (el.tagName === 'SELECT' && !Array.from(el.options).some(option => option.value === serverVal)) {
                            el.add(new Option(serverVal, serverVal));
                        }
                        el.value = serverVal;
                        el.dataset.original = serverVal;
                    }
                    if (saveBtn) saveBtn.style.display = 'none';
                }
            });
            return;
        }

        const categoryMeta = {
            "Seslendirme ve Sesli Yazma": {
                icon: "🎙️",
                keys: ["tts_enabled", "tts_voice", "tts_model", "tts_timeout_seconds", "stt_enabled", "stt_model"],
                desc: "Yanıtları seslendirme (TTS) ve mikrofonla canlı yazma (STT) ayarları"
            },
            "Router Konfigürasyonu": {
                icon: "⚡",
                keys: ["router_api_key", "router_model_group"],
                desc: "Orion Router yönlendirme ve API güvenlik anahtarları"
            },
            "Yapay Zeka (AI) Ayarları": {
                icon: "🧠",
                keys: ["ai_chat_titles_enabled", "chat_title_model", "system_prompt", "llm_timeout_seconds", "embed_timeout_seconds", "chat_history_max_messages", "first_token_delay_ms", "token_delay_ms", "thinking_level", "temperature"],
                desc: "Model zekası, sistem promptu, düşünme seviyesi ve token gecikmeleri"
            },
            "Sistem Parametreleri": {
                icon: "⚙️",
                keys: ["result_ttl_seconds", "sse_heartbeat_seconds", "worker_max_concurrency", "stop_key_ttl_seconds", "redis_cache_ttl_seconds"],
                desc: "Redis önbellek süreleri, heartbeat ve eşzamanlı kuyruk yapılandırması"
            }
        };

        const keyHints = {
            "ai_chat_titles_enabled": "Her yeni sorudan sonra önceki başlık ve son soruya göre başlığı güncelle.",
            "tts_enabled": "TTS ses sentezleyici aktif olsun mu?",
            "stt_enabled": "Mikrofonla canlı sesli yazmayı aç veya kapat.",
            "stt_model": "Canlı akış için şimdilik yalnızca yerel STT desteklenir.",
            "chat_title_model": "Başlık üretimi için ayrı model seçin. Boşsa sohbet modeli kullanılır.",
            "router_api_key": "Orion Router API Key",
            "system_prompt": "Asistanın ana rolü ve sistem talimatı",
            "temperature": "Yaratıcılık katsayısı (0.0 - 2.0)",
            "chat_history_max_messages": "Hafızada tutulacak maksimum mesaj sayısı"
        };

        const keyPlaceholders = {
            "router_api_key": "Orion Router API Key",
            "system_prompt": "Sistem promptu...",
            "tts_voice": "Ses adı...",
            "tts_model": "Model adı...",
            "thinking_level": "Düşünce seviyesi...",
            "router_model_group": "Model grubu..."
        };

        let html = '';
        const handledKeys = new Set();

        Object.entries(categoryMeta).forEach(([categoryName, meta]) => {
            let hasKeys = false;
            let rowsHtml = '';

            meta.keys.forEach(key => {
                if (settings[key] !== undefined) {
                    hasKeys = true;
                    handledKeys.add(key);

                    let val = settings[key] !== undefined ? String(settings[key]) : '';
                    if (key === 'router_api_key' && val === 'sk-60f3eaf169d7c485-0icocf-0a3db541') {
                        val = '';
                    } else if (key === 'thinking_level' && val.toLowerCase() === 'default') {
                        val = '';
                    }
                    const hint = keyHints[key] || '';
                    const placeholder = keyPlaceholders[key] || key;

                    let inputHtml = '';
                    if ((key === 'tts_enabled' || key === 'ai_chat_titles_enabled' || key === 'stt_enabled')) {
                        const isChecked = String(settings[key]).toLowerCase() === 'true';
                        inputHtml = `
                            <select id="setting-input-${key}" data-original="${isChecked ? 'true' : 'false'}" onchange="UI.handleSettingChange('${key}')" class="setting-select">
                                <option value="true" ${isChecked ? 'selected' : ''}>Açık (True)</option>
                                <option value="false" ${!isChecked ? 'selected' : ''}>Kapalı (False)</option>
                            </select>`;
                    } else if (['chat_title_model', 'router_model_group', 'tts_model', 'tts_voice', 'stt_model'].includes(key)) {
                        inputHtml = `<select id="setting-input-${key}" data-original="${val.replace(/"/g, '&quot;')}" onchange="UI.handleSettingChange('${key}')" class="setting-select"></select>`;
                    } else if (key === 'system_prompt') {
                        inputHtml = `<textarea id="setting-input-${key}" data-original="${val.replace(/"/g, '&quot;')}" oninput="UI.handleSettingChange('${key}')" class="setting-textarea" rows="4" placeholder="${placeholder}">${val}</textarea>`;
                    } else {
                        inputHtml = `<input type="text" id="setting-input-${key}" data-original="${val.replace(/"/g, '&quot;')}" value="${val.replace(/"/g, '&quot;')}" oninput="UI.handleSettingChange('${key}')" class="setting-input" placeholder="${placeholder}">`;
                    }

                    rowsHtml += `
                    <div class="setting-row">
                        <div class="setting-info">
                            <label class="setting-label">${({ai_chat_titles_enabled: "AI ile sohbet başlığı", chat_title_model: "Başlık modeli", stt_enabled: "Canlı sesli yazma", stt_model: "Sesli yazma modeli", tts_model: "Seslendirme modeli", tts_voice: "Ses"})[key] || key}</label>
                            ${hint ? `<span class="setting-hint">${hint}</span>` : ''}
                        </div>
                        <div class="setting-control">
                            ${inputHtml}
                            <button id="setting-save-${key}" class="btn btn-save setting-save-btn" onclick="window.saveSetting('${key}')" style="display: none;">
                                <span>Kaydet</span>
                            </button>
                        </div>
                    </div>`;
                }
            });

            if (hasKeys) {
                html += `
                <div class="settings-card">
                    <div class="settings-card-header">
                        <div class="settings-card-icon">${meta.icon}</div>
                        <div>
                            <h3>${categoryName}</h3>
                            <p>${meta.desc}</p>
                        </div>
                    </div>
                    <div class="settings-card-body">
                        ${rowsHtml}
                    </div>
                </div>`;
            }
        });

        // Diğer kategorize edilmemiş ayarlar
        const unhandledKeys = Object.keys(settings).filter(k => !handledKeys.has(k)).sort();
        if (unhandledKeys.length > 0) {
            let rowsHtml = '';
            unhandledKeys.forEach(key => {
                const val = settings[key] !== undefined ? String(settings[key]) : '';
                rowsHtml += `
                <div class="setting-row">
                    <div class="setting-info">
                        <label class="setting-label">${({ai_chat_titles_enabled: "AI ile sohbet başlığı", chat_title_model: "Başlık modeli", stt_enabled: "Canlı sesli yazma", stt_model: "Sesli yazma modeli", tts_model: "Seslendirme modeli", tts_voice: "Ses"})[key] || key}</label>
                    </div>
                    <div class="setting-control">
                        <input type="text" id="setting-input-${key}" data-original="${val.replace(/"/g, '&quot;')}" value="${val.replace(/"/g, '&quot;')}" oninput="UI.handleSettingChange('${key}')" class="setting-input">
                        <button id="setting-save-${key}" class="btn btn-save setting-save-btn" onclick="window.saveSetting('${key}')" style="display: none;">
                            <span>Kaydet</span>
                        </button>
                    </div>
                </div>`;
            });

            html += `
            <div class="settings-card">
                <div class="settings-card-header">
                    <div class="settings-card-icon">📦</div>
                    <div>
                        <h3>Diğer Parametreler</h3>
                        <p>Ek konfigürasyon seçenekleri</p>
                    </div>
                </div>
                <div class="settings-card-body">
                    ${rowsHtml}
                </div>
            </div>`;
        }

        dashboard.innerHTML = `<div class="settings-catalog-toolbar"><div><strong>Model ve sesler</strong><span id="catalog-status" role="status">Bu sayfa açıkken otomatik güncellenir</span></div><button type="button" class="catalog-refresh" id="refresh-models" onclick="UI.loadModelChoices(UI.currentSettings)"><span aria-hidden="true">↻</span> Yenile</button></div>` + html;

    },

    async loadModelChoices(settings, silent = false) {
        const button = document.getElementById('refresh-models');
        const status = document.getElementById('catalog-status');
        if (!silent && button?.disabled) return;
        if (button && !silent) button.disabled = true;
        if (status && !silent) status.textContent = 'Güncelleniyor…';
        const result = await API.getChatModels();
        if (button && !silent) button.disabled = false;
        if (result.error) {
            if (status) status.textContent = result.error;
            return;
        }
        this.routerCatalog = result;
        for (const key of ['router_model_group', 'chat_title_model', 'tts_model', 'stt_model']) {
            const select = document.getElementById(`setting-input-${key}`);
            if (!select) continue;
            if (document.activeElement === select) continue;
            const current = select.options.length ? select.value : String(settings[key] || '');
            const original = select.dataset.original;
            const capability = key === 'tts_model' ? 'tts' : key === 'stt_model' ? 'stt' : 'chat';
            const models = result.models.filter(model => model.capability === capability &&
                (key !== 'stt_model' || (model.provider === 'local' && model.name === 'local-stt')));
            select.replaceChildren();
            if (key === 'chat_title_model') select.add(new Option('Sohbet modelini kullan', ''));
            for (const model of models) select.add(new Option(model.name, model.name));
            if (current && !Array.from(select.options).some(option => option.value === current)) {
                const missing = new Option(`${current} — listesi şu an alınamıyor`, current);
                select.add(missing);
            }
            select.value = current;
            select.dataset.original = original;
        }
        const ttsModel = document.getElementById('setting-input-tts_model');
        if (ttsModel) ttsModel.onchange = () => {
            UI.handleSettingChange('tts_model');
            UI.loadVoiceChoices(true);
        };
        this.loadVoiceChoices(false);
        if (status && !silent) status.textContent = result.unavailable?.length
            ? 'Erişilebilen listeler güncellendi · Diğerleri tekrar kontrol edilecek'
            : 'Güncel · Bu sayfa açıkken otomatik kontrol edilir';
    },

    loadVoiceChoices(modelChanged) {
        const select = document.getElementById('setting-input-tts_voice');
        const model = document.getElementById('setting-input-tts_model')?.value;
        if (!select || !this.routerCatalog) return;
        if (document.activeElement === select) return;
        const provider = this.routerCatalog.models.find(item => item.name === model)?.provider;
        const voices = this.routerCatalog.voices[provider] || [];
        const unavailable = this.routerCatalog.unavailable || [];
        const inaccessible = unavailable.includes('voices') || (provider === 'local' && unavailable.includes('local-tts-info'));
        const current = select.options.length ? select.value : String(this.currentSettings.tts_voice || '');
        select.replaceChildren(new Option('Varsayılan ses', ''));
        for (const voice of voices) select.add(new Option(voice, voice));
        if (current && !voices.includes(current) && (!modelChanged || !voices.length)) {
            const missing = new Option(`${current} — ${voices.length ? 'listede bulunamadı' : 'ses listesine şu an erişilemiyor'}`, current);
            select.add(missing);
        }
        select.value = modelChanged && voices.length && !voices.includes(current) ? '' : current;
        let notice = document.getElementById('tts-availability');
        if (!notice) {
            notice = document.createElement('span');
            notice.id = 'tts-availability';
            notice.className = 'setting-hint tts-availability';
            select.parentElement.appendChild(notice);
        }
        notice.textContent = String(this.currentSettings.tts_enabled).toLowerCase() === 'false'
            ? 'Seslendirme kapalı. Kayıtlı ses tercihiniz korunuyor.'
            : inaccessible || !voices.length
                ? (provider === 'local' ? 'Orion TTS’ye erişilemiyor.' : 'Ses listesine erişilemiyor.')
                : '';
        if (modelChanged) this.handleSettingChange('tts_voice');
    },

    clearChatArea() {
        this.chatArea.innerHTML = '';
    },

    renderChatList(chats, currentChatId) {
        const listEl = document.getElementById('chat-list');
        if (!listEl) return;

        listEl.innerHTML = '';
        if (!chats || chats.length === 0) {
            listEl.innerHTML = '<div style="color: #94a3b8; font-size: 0.85rem; text-align: center; padding: 10px;">Henüz sohbet yok.</div>';
            return;
        }

        chats.forEach(chat => {
            const item = document.createElement('div');
            item.className = 'chat-item';
            item.dataset.chatId = chat.chat_id;
            if (chat.chat_id === currentChatId) item.classList.add('active');

            let dateStr = "";
            try {
                const d = new Date(chat.updated_at);
                dateStr = d.toLocaleDateString('tr-TR', { hour: '2-digit', minute: '2-digit' });
            } catch (e) { }

            const displayName = chat.name || `Sohbet ${chat.chat_id.substring(0, 8)}...`;

            item.innerHTML = `
                <div class="chat-item-icon">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                    </svg>
                </div>
                <div class="chat-item-body">
                    <div class="chat-item-name">${displayName}</div>
                    <span class="chat-item-date">${dateStr}</span>
                </div>
                <div class="chat-item-actions">
                    <button class="chat-action-btn rename-btn" title="Yeniden Adlandır">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
                    </button>
                    <button class="chat-action-btn delete-btn" title="Sil">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                    </button>
                </div>
            `;

            // Show/hide action buttons on hover
            item.addEventListener('mouseenter', () => {
                item.querySelector('.chat-item-actions').style.opacity = '1';
            });
            item.addEventListener('mouseleave', () => {
                item.querySelector('.chat-item-actions').style.opacity = '0';
            });

            // Click body → load chat
            item.addEventListener('click', (event) => {
                if (event.target.closest('.chat-item-actions')) return;
                if (window.loadChat) window.loadChat(chat.chat_id);
            });

            // Rename button
            item.querySelector('.rename-btn').addEventListener('click', async (e) => {
                e.stopPropagation();
                const nameEl = item.querySelector('.chat-item-name');
                const currentName = nameEl.textContent;
                const newName = prompt('Yeni sohbet adı:', currentName);
                if (!newName || newName.trim() === currentName) return;
                const result = await API.renameChat(chat.chat_id, newName.trim());
                if (result && !result.error) {
                    nameEl.textContent = newName.trim();
                    chat.name = newName.trim();
                } else {
                    alert('Hata: ' + (result?.error || 'Bilinmeyen hata'));
                }
            });

            // Delete button
            item.querySelector('.delete-btn').addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm(`"${displayName}" sohbetini silmek istediğinize emin misiniz?`)) return;
                const result = await API.deleteChat(chat.chat_id);
                if (result && !result.error) {
                    item.remove();
                    if (chat.chat_id === AppState.currentChatId) {
                        AppState.selectChat(null);
                        UI.clearChatArea();
                        UI.chatArea.innerHTML = '<div class="message bot">Sohbet silindi. Yeni bir sohbet başlatın.</div>';
                    }
                } else {
                    alert('Hata: ' + (result?.error || 'Bilinmeyen hata'));
                }
            });

            listEl.appendChild(item);
        });
    }
};

window.saveSetting = (key) => UI.saveSetting(key);
