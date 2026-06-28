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

        // Check if connected
        if (!AppState.sseConnected) {
            alert("Bağlantı yok. Yeniden bağlanmayı bekleyin.");
            return;
        }

        // Determine chatId (may be null for new chat)
        const chatId = AppState.currentChatId;

        // If this specific chat is already generating, block
        if (chatId && AppState.isAnyChatGenerating(chatId)) {
            return;
        }

        // Update UI for sending
        UI.appendUserMessage(text);
        UI.clearInput();

        try {
            const response = await this._fetch(`/api/v1/chats/messages`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    user_id: AppConfig.getUserId(),
                    chat_id: chatId,
                    input: { text: text },
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
                } else {
                    const wasNewChat = !AppState.currentChatId;
                    AppState.currentChatId = data.chat_id;
                    AppState.startGenerating(data.chat_id);
                    UI.createBotMessagePlaceholder(data.chat_id, true);
                    UI.setStopButtonVisible(true);
                    if (wasNewChat && window.loadChats) {
                        window.loadChats();
                    }
                }
            } else {
                UI.appendToken(chatId || 'error', `\n[API Hatası: ${JSON.stringify(data)}]`);
            }
        } catch (error) {
            UI.appendToken(chatId || 'error', `\n[İstek Hatası: ${error.message}]`);
        }
    },

    async stopGeneration() {
        if (!AppState.currentChatId || !AppState.isAnyChatGenerating(AppState.currentChatId)) return;
        
        try {
            await this._fetch(`/api/v1/chats/${AppState.currentChatId}/stop`, {
                method: 'POST'
            });
            AppState.stopGenerating(AppState.currentChatId);
            UI.finishGeneration(AppState.currentChatId);
        } catch (error) {
            console.error("Durdurma hatası:", error);
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
            return await response.json();
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
            return await response.json();
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
