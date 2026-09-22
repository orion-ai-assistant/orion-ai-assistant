/**
 * API Request Handlers and Authentication
 */
const Auth = {
    getToken() {
        return localStorage.getItem("orion_token");
    },
    
    setToken(token) {
        localStorage.setItem("orion_token", token);
    },
    
    clearToken() {
        localStorage.removeItem("orion_token");
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
        const response = await fetch("/api/v1/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Giriş başarısız.");
        }
        this.setToken(data.access_token);
        return data;
    },

    async register(username, password) {
        const response = await fetch("/api/v1/auth/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Kayıt başarısız.");
        }
        return data;
    },

    async getMe() {
        const response = await fetch("/api/v1/auth/me", {
            headers: this.getAuthHeaders()
        });
        if (!response.ok) {
            throw new Error("Oturum süresi dolmuş veya geçersiz.");
        }
        return await response.json();
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
        if (!url.startsWith("/api/v1/auth") && !AppState.sseConnected) {
            return new Response(JSON.stringify({ detail: "Bağlantı yok. Yeniden bağlanılıyor." }), {
                status: 503,
                headers: { "Content-Type": "application/json" }
            });
        }
        options.headers = Auth.getAuthHeaders(options.headers || {});
        const response = await fetch(url, options);
        if (response.status === 401) {
            Auth.handleUnauthorized();
        }
        return response;
    },

    async sendMessage(text) {
        if (!text) return;

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
        const wasNewChat = !chatId;

        // If this specific chat is already generating, block
        if (chatId && (AppState.isAnyChatGenerating(chatId) || AppState.isStopping(chatId))) {
            return;
        }

        this.sendInFlight = true;

        AppState.pendingNewChatRequest = true;
        AppState.pendingChatEvents = [];

        // Render immediately. The matching SSE user_message event is claimed
        // later so slow chat creation never leaves the conversation area blank.
        AppState.showOptimisticUserMessage(text, chatId);
        UI.appendUserMessage(text);
        UI.clearInput();

        try {
            const audioEnabled = document.getElementById('audio-toggle')?.checked || false;
            const response = await this._fetch(`/api/v1/chats/messages`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: AppConfig.getUserId(),
                    chat_id: chatId,
                    input: { 
                        text: text,
                        audio: audioEnabled
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
                    AppState.currentChatId = data.chat_id;
                    AppState.bindOptimisticUserMessage(data.chat_id);
                    const turnId = data.turn_id || data.generation_id || null;
                    AppState.startGenerating(data.chat_id, turnId);
                    UI.setStopButtonVisible(true);
                    const pendingEvents = AppState.pendingChatEvents;
                    AppState.pendingChatEvents = [];
                    AppState.pendingNewChatRequest = false;
                    pendingEvents.forEach(eventData => SSE.processEvent(eventData));
                    if (wasNewChat && window.loadChats) {
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
        if (!AppState.currentChatId || !AppState.isAnyChatGenerating(AppState.currentChatId)) return;

        const targetChatId = AppState.currentChatId;
        const activeTurn = AppState.getActiveTurn(targetChatId);
        const targetTurnId = activeTurn?.turnId || null;

        // Keep accepting this turn until the worker sends its terminal event.
        if (!AppState.beginStopping(targetChatId, targetTurnId)) return;

        // Update stop button to indicate the request is in flight.
        const stopBtn = document.getElementById('stop-btn');
        if (stopBtn) {
            stopBtn.disabled = true;
            stopBtn.innerHTML = '<span class="stop-icon">\u23F3</span><span class="stop-text">Durduruluyor...</span>';
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
            const response = await this._fetch(`/api/v1/chats/${chatId}/history`);
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

    async getChatModels() {
        try {
            const response = await this._fetch('/api/v1/models');
            const data = await response.json();
            return response.ok ? data : {error: data.detail || 'Model listesi alınamadı.'};
        } catch (error) {
            return {error: error.message};
        }
    },

    async getSettings() {
        try {
            const response = await this._fetch(`/api/v1/admin/settings`);
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
