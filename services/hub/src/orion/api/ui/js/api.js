/**
 * API Request Handlers and Authentication
 */
const Auth = {
    tokenKey: 'orion_token',
    getToken() {
        return localStorage.getItem(this.tokenKey);
    },
    
    setToken(token) {
        localStorage.setItem(this.tokenKey, token);
    },
    
    clearToken() {
        window.RouterProviders?.reset();
        localStorage.removeItem(this.tokenKey);
    },

    getAuthHeaders(additionalHeaders = {}) {
        const token = this.getToken();
        const headers = { ...additionalHeaders };
        if (token) {
            headers["Authorization"] = `Bearer ${token}`;
        }
        return headers;
    },

    async login(username, password) {
        const namespace = this.tokenKey;
        const response = await fetch(HubPicker.apiPath("/api/v1/auth/login"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
        });
        const data = await response.json();
        if (!response.ok) {
            const error = new Error(data.detail || 'Giriş başarısız.');
            if (response.status === 429) error.retryAfter = AuthWait.retrySeconds(response);
            throw error;
        }
        if (namespace !== this.tokenKey || !HubPicker.connected) throw new Error('Hub değişti. Seçili Hub’a yeniden giriş yap.');
        this.setToken(data.access_token);
        return data;
    },

    async register(username, password) {
        const namespace = this.tokenKey;
        const response = await fetch(HubPicker.apiPath("/api/v1/auth/register"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password, invitation_token: this.invitationToken })
        });
        const data = await response.json();
        if (!response.ok) {
            const error = new Error(typeof data.detail === 'string' ? data.detail : 'Kullanıcı adı ve şifreyi kontrol et.');
            if (response.status === 429) error.retryAfter = AuthWait.retrySeconds(response);
            throw error;
        }
        if (namespace !== this.tokenKey || !HubPicker.connected) throw new Error('Hub değişti. Seçili Hub’a yeniden giriş yap.');
        this.setToken(data.access_token);
        this.invitationToken = null;
        history.replaceState(null, '', location.pathname);
        return data;
    },

    async getMe() {
        const namespace = this.tokenKey;
        const response = await fetch(HubPicker.apiPath("/api/v1/auth/me"), {
            headers: this.getAuthHeaders()
        });
        if (!response.ok) {
            throw new Error("Oturum süresi dolmuş veya geçersiz.");
        }
        const data = await response.json();
        if (namespace !== this.tokenKey || !HubPicker.connected) throw new Error('Hub değişti.');
        return data;
    },
    
    handleUnauthorized() {
        this.clearToken();
        document.getElementById("auth-modal").classList.add("show");
        if (AppState && AppState.eventSource) {
            document.getElementById("logout-btn").click(); // Trigger disconnect
        }
    }
};

const API = {
    sendInFlight: false,

    async _fetch(url, options = {}) {
        const namespace = Auth.tokenKey;
        if (!url.startsWith("/api/v1/auth") && !AppState.sseConnected) {
            return new Response(JSON.stringify({ detail: "Bağlantı yok. Yeniden bağlanılıyor." }), {
                status: 503,
                headers: { "Content-Type": "application/json" }
            });
        }
        options.headers = Auth.getAuthHeaders({"Accept-Language": OrionI18n.language, ...(options.headers || {})});
        const response = await fetch(HubPicker.apiPath(url), options);
        if (namespace !== Auth.tokenKey) throw new Error('Hub değişti.');
        if (response.status === 401) {
            Auth.handleUnauthorized();
        }
        return response;
    },

    async sendMessage(text, images = []) {
        const attachments = [...(UI.selectedImages || [])];
        if (!text && !attachments.length && !images.length) return;
        if (attachments.some(item => item.loading)) {
            UI.showToast?.('Dosyalar hazırlanıyor, tamamlanınca gönder.');
            return;
        }
        const displayText = text || '';
        text = displayText;
        text = text || 'Ekleri incele.';

        // Prevent rapid Enter/click events from creating duplicate jobs before
        // the first POST response marks the chat as generating.
        if (this.sendInFlight) return;

        // Check if connected
        if (!AppState.sseConnected) {
            alert("Bağlantı yok. Yeniden bağlanmayı bekleyin.");
            return;
        }

        // Determine chatId (may be null for new chat)
        const chatId = AppState.currentChatId;
        const selectionVersion = AppState.chatSelectionVersion;
        const wasNewChat = !chatId;

        // If this specific chat is already generating, block
        if (chatId && (AppState.isAnyChatGenerating(chatId) || AppState.isStopping(chatId))) {
            return;
        }

        window.PcmSpeech?.stop();
        window.PcmSpeech?.unlock();
        this.sendInFlight = true;

        AppState.pendingNewChatRequest = true;
        AppState.pendingChatEvents = [];

        // Render immediately. The matching SSE user_message event is claimed
        // later so slow chat creation never leaves the conversation area blank.
        // NOTE: Do NOT call finishGeneration here - the SSE done event already
        // finalizes the previous generation. Calling it here would attach stale
        // metrics from the previous turn onto the new empty bot bubble.
        AppState.showOptimisticUserMessage(text, chatId);
        UI.appendUserMessage(displayText, attachments.length ? attachments : images);
        UI.clearInput();

        try {
            const audioEnabled = String(UI.currentSettings?.tts_enabled ?? localStorage.getItem('orion_tts_enabled')).toLowerCase() === 'true';
            const response = await this._fetch(`/api/v1/chats/messages`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    origin_session_id: window.PcmSpeech?.sessionId,
                    user_id: AppConfig.getUserId(),
                    chat_id: chatId,
                    ...(!chatId && window.ToolsUI?.draftPayload() ? {tool_selection: window.ToolsUI.draftPayload()} : {}),
                    input: { 
                        text: text,
                        audio: audioEnabled,
                        ...(attachments.length ? {
                            attachments: attachments.map(({ id, name, mime_type, data, size, isText }) =>
                                ({ id, name, mime_type, data, size, isText: Boolean(isText) }))
                        } : { images }),
                        metadata: { display_text: displayText }
                    },
                    stream_mode: "once"
                })
            });


            if (response.status === 401) {
                throw new Error("Oturum süresi doldu.");
            }

            const data = await response.json();

            if (response.ok) {
                if (data.status === "failed") {
                    // No generation started
                    AppState.pendingNewChatRequest = false;
                    AppState.pendingChatEvents = [];
                    AppState.clearOptimisticUserMessage();
                    UI.setStopButtonVisible(false);
                } else {
                    const adoptedFromSse = Boolean(
                        wasNewChat
                        && AppState.currentChatId === data.chat_id
                        && !AppState.pendingNewChatRequest
                    );
                    if (selectionVersion === AppState.chatSelectionVersion) {
                        AppState.currentChatId = data.chat_id;
                    }
                    AppState.bindOptimisticUserMessage(data.chat_id);
                    const turnId = data.turn_id || data.generation_id || null;
                    if (!adoptedFromSse) {
                        AppState.startGenerating(data.chat_id, turnId);
                    }
                    UI.setStopButtonVisible(AppState.isAnyChatGenerating(data.chat_id));
                    const pendingEvents = AppState.pendingChatEvents;
                    AppState.pendingChatEvents = [];
                    AppState.pendingNewChatRequest = false;
                    pendingEvents.forEach(eventData => SSE.processEvent(eventData));
                    if (wasNewChat && window.loadChats) {
                        if (window.ToolsUI) window.ToolsUI.draft = null;
                        window.loadChats();
                    }
                }
            } else {
                AppState.pendingNewChatRequest = false;
                AppState.pendingChatEvents = [];
                AppState.clearOptimisticUserMessage();
                if (chatId) {
                    AppState.stopGenerating(chatId);
                    UI.finishGeneration(chatId, false);
                    AppState.clearGenerationMetrics(chatId);
                }
                UI.setStopButtonVisible(false);
                const message = `[API Hatası: ${JSON.stringify(data)}]`;
                if (chatId) UI.appendToken(chatId, `\n${message}`);
                else UI.appendStaticBotMessage(message);
            }
        } catch (error) {
            AppState.pendingNewChatRequest = false;
            AppState.pendingChatEvents = [];
            AppState.clearOptimisticUserMessage();
            if (chatId) {
                AppState.stopGenerating(chatId);
                UI.finishGeneration(chatId, false);
                AppState.clearGenerationMetrics(chatId);
            }
            UI.setStopButtonVisible(false);
            const message = `[İstek Hatası: ${error.message}]`;
            if (chatId) UI.appendToken(chatId, `\n${message}`);
            else UI.appendStaticBotMessage(message);
        } finally {
            this.sendInFlight = false;
        }
    },

    async stopGeneration() {
        window.PcmSpeech?.stop();
        document.querySelectorAll("audio").forEach(audio => audio.pause());
        if (!AppState.currentChatId || !AppState.isAnyChatGenerating(AppState.currentChatId)) return;

        const targetChatId = AppState.currentChatId;
        const activeTurn = AppState.getActiveTurn(targetChatId);
        const targetTurnId = activeTurn?.turnId || null;
        if (targetTurnId) window.PcmSpeech?.blocked?.add(targetTurnId);

        // Keep accepting this turn until the worker sends its terminal event.
        if (!AppState.beginStopping(targetChatId, targetTurnId)) return;

        // Update stop button to indicate the request is in flight.
        const stopBtn = document.getElementById('stop-btn');
        if (stopBtn) {
            stopBtn.disabled = true;
            stopBtn.setAttribute('aria-label', OrionI18n.t('stopping'));
            stopBtn.title = OrionI18n.t('stopping');
        }

        try {
            // Prefer the turn-scoped endpoint; fall back to chat-scoped for older backend.
            const stopPath = targetTurnId
                ? `/api/v1/chats/${targetChatId}/turns/${targetTurnId}/stop`
                : `/api/v1/chats/${targetChatId}/stop`;
            const response = await this._fetch(stopPath, { method: 'POST' });
            if (!response.ok) throw new Error(`Stop HTTP ${response.status}`);
        } catch (error) {
            console.error("Durdurma iste\u011Fi ba\u015Far\u0131s\u0131z:", error);
            const current = AppState.getActiveTurn(targetChatId);
            if (current === activeTurn && current.status === "stopping") {
                current.status = "generating";
                if (AppState.currentChatId === targetChatId) UI.setStopButtonVisible(true);
            }
        }
    },

    async checkJobStatus(chatId) {
        if (!chatId) return;
        try {
            const response = await this._fetch(`/api/v1/chats/${chatId}`);
            if (response.status === 401) return { error: "Oturum süresi doldu." };
            return await response.json();
        } catch (error) {
            console.error("Durum sorgulama hatası:", error);
            return { error: error.message };
        }
    },

    async getChats() {
        try {
            const response = await this._fetch(`/api/v1/chats`);
            if (response.status === 401) return { error: "Oturum süresi doldu." };
            const data = await response.json();
            if (!response.ok) return { error: data.detail || data.error || "Sohbetler alınamadı." };
            return data;
        } catch (error) {
            console.error("Chat listesi getirme hatası:", error);
            return { error: error.message };
        }
    },

    async getChatHistory(chatId) {
        if (!chatId) return;
        try {
            const response = await this._fetch(`/api/v1/chats/${chatId}/history`, {signal: AbortSignal.timeout(10000)});
            if (response.status === 401) return { error: "Oturum süresi doldu." };
            const data = await response.json();
            if (!response.ok) return { error: data.detail || data.error || "Sohbet geçmişi alınamadı." };
            return data;
        } catch (error) {
            console.error("Chat geçmişi getirme hatası:", error);
            return { error: error.message };
        }
    },

    async saveSettings(key, value) {
        if (!key) return;
        try {
            const payload = {
                user_id: AppConfig.getUserId(),
                values: {}
            };
            payload.values[key] = value;

            const response = await this._fetch(`/api/v1/admin/settings`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (response.status === 401) return { error: "Oturum süresi doldu." };
            const data = await response.json();
            if (!response.ok) {
                return { error: data.detail || data.error || "Ayar kaydedilemedi" };
            }
            return data;
        } catch (error) {
            console.error("Ayar kaydetme hatası:", error);
            return { error: error.message };
        }
    },

    async getChatModels(includeVoices = false) {
        try {
            const response = await this._fetch(`/api/v1/models?include_voices=${includeVoices}`);
            const data = await response.json();
            return response.ok ? data : {error: data.detail || 'Model listesi alınamadı.'};
        } catch (error) {
            return {error: error.message};
        }
    },

    getVoiceCatalog() {
        return this.getChatModels(true);
    },

    async getSettings() {
        try {
            const response = await this._fetch(`/api/v1/admin/settings`, {
                headers: {'X-Orion-STT-Language': navigator.language || navigator.languages?.[0] || ''}
            });
            if (response.status === 401) return { error: "Oturum süresi doldu." };
            const data = await response.json();
            if (!response.ok) {
                return { error: data.detail || data.error || "Ayarlar getirilemedi" };
            }
            return data;
        } catch (error) {
            console.error("Ayar getirme hatası:", error);
            return { error: error.message };
        }
    },

    async renameChat(chatId, name) {
        try {
            const response = await this._fetch(`/api/v1/chats/${chatId}/rename`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            });
            if (!response.ok) {
                const d = await response.json().catch(() => ({}));
                return { error: d.detail || "Yeniden adlandırma başarısız." };
            }
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    },

    async deleteChat(chatId) {
        try {
            const response = await this._fetch(`/api/v1/chats/${chatId}`, { method: 'DELETE' });
            if (!response.ok) {
                const d = await response.json().catch(() => ({}));
                return { error: d.detail || "Silme başarısız." };
            }
            return await response.json();
        } catch (error) {
            return { error: error.message };
        }
    }
};
