/**
 * Server-Sent Events Connection Handler
 */
const SSE = {
    connect() {
        AppState.sseShouldReconnect = true;
        AppState.sseConnected = false;
        UI.setConnectionStatus(false, "connecting");
        if (AppState.eventSource) {
            AppState.eventSource.close();
        }
        if (AppState.sseConnectTimer) {
            clearTimeout(AppState.sseConnectTimer);
            AppState.sseConnectTimer = null;
        }

        const token = Auth.getToken();
        if (!token) {
            Auth.handleUnauthorized();
            return;
        }

        AppState.eventSource = new EventSource(`/api/v1/chat/stream?token=${token}`);

        AppState.sseConnectTimer = setTimeout(() => {
            AppState.sseConnectTimer = null;
            if (!AppState.sseConnected && AppState.sseShouldReconnect) {
                if (AppState.eventSource) {
                    AppState.eventSource.close();
                    AppState.eventSource = null;
                }
                UI.setConnectionStatus(false, "reconnecting");
                SSE.scheduleReconnect();
            }
        }, 8000);

        AppState.eventSource.onopen = () => {
            AppState.sseConnected = true;
            AppState.sseReconnectAttempt = 0;
            UI.setConnectionStatus(true);
            console.log("SSE Bağlantısı kuruldu.");
            if (window.loadChats) window.loadChats();
            if (window.loadInitialSettings) window.loadInitialSettings();
            if (window.startUserPolling) window.startUserPolling();
            // Mevcut sohbeti yeniden yükle (geçmişi + devam eden tokenları göster)
            if (AppState.currentChatId && window.loadChat) {
                window.loadChat(AppState.currentChatId);
            }
            if (AppState.sseConnectTimer) {
                clearTimeout(AppState.sseConnectTimer);
                AppState.sseConnectTimer = null;
            }
        };

        AppState.eventSource.onerror = (error) => {
            if (!AppState.sseShouldReconnect) return;
            AppState.sseConnected = false;
            UI.setConnectionStatus(false, "reconnecting");
            console.error("SSE Bağlantı hatası:", error);
            if (AppState.eventSource) {
                AppState.eventSource.close();
                AppState.eventSource = null;
            }
            if (AppState.sseConnectTimer) {
                clearTimeout(AppState.sseConnectTimer);
                AppState.sseConnectTimer = null;
            }
            SSE.scheduleReconnect();
        };

        AppState.eventSource.addEventListener("message", (event) => {
            const data = JSON.parse(event.data);
            SSE.processEvent(data);
        });
    },

    scheduleReconnect() {
        if (AppState.sseReconnectTimer) return;
        const attempt = Math.min(AppState.sseReconnectAttempt, 6);
        const delayMs = Math.min(1000 * Math.pow(2, attempt), 15000);
        AppState.sseReconnectAttempt += 1;
        AppState.sseReconnectTimer = setTimeout(() => {
            AppState.sseReconnectTimer = null;
            if (AppState.sseShouldReconnect) {
                SSE.connect();
            }
        }, delayMs);
    },

    disconnect() {
        AppState.sseShouldReconnect = false;
        AppState.sseConnected = false;
        if (AppState.sseReconnectTimer) {
            clearTimeout(AppState.sseReconnectTimer);
            AppState.sseReconnectTimer = null;
        }
        if (AppState.sseConnectTimer) {
            clearTimeout(AppState.sseConnectTimer);
            AppState.sseConnectTimer = null;
        }
        if (AppState.eventSource) {
            AppState.eventSource.close();
            AppState.eventSource = null;
        }
        UI.setConnectionStatus(false, "disconnected");
    },

    processEvent(data) {
        const chatId = data.chat_id;
        console.log("Gelen olay:", data);

        // Real-time synchronization for chat modifications
        if (data.type === "chat_rename") {
            if (window.loadChats) window.loadChats();
            return;
        }
        if (data.type === "chat_delete") {
            if (window.loadChats) window.loadChats();
            if (chatId === AppState.currentChatId) {
                AppState.currentChatId = null;
                UI.clearChatArea();
                UI.chatArea.innerHTML = '<div class="message bot">Bu sohbet başka bir cihazdan veya yöneticiden silindi.</div>';
                UI.setStopButtonVisible(false);
            }
            return;
        }

        if (chatId && (data.type === "accepted" || data.type === "token" || data.type === "thinking")) {
            if (!AppState.isAnyChatGenerating(chatId)) {
                AppState.startGenerating(chatId);
                if (chatId === AppState.currentChatId) {
                    UI.setStopButtonVisible(true);
                }
            }
        }

        // If we are currently loading the history for this chat, buffer the live events
        if (AppState._loadingHistory && chatId === AppState.currentChatId) {
            if (!AppState._sseBuffer) AppState._sseBuffer = [];
            AppState._sseBuffer.push(data);
            return;
        }

        // ---- Events for non-active chats: just refresh sidebar ----
        if (chatId && chatId !== AppState.currentChatId) {
            if (data.type === "user_message" || data.type === "accepted") {
                if (window.loadChats) window.loadChats();
            }
            if (data.type === "token") {
                UI.appendToken(chatId, data.data.token);
            } else if (data.type === "thinking") {
                UI.appendThinkingToken(chatId, data.data.token);
            } else if (data.type === "done" || data.type === "error") {
                AppState.stopGenerating(chatId);
                UI.finishGeneration(chatId);
            }
            return;
        }

        // ---- Events for the active chat ----
        if (data.type === "accepted") {
            UI.createBotMessagePlaceholder(chatId);
        }
        else if (data.type === "user_message") {
            const messages = UI.chatArea.querySelectorAll('.message.user');
            let alreadyAdded = false;
            if (messages.length > 0) {
                const lastMsg = messages[messages.length - 1];
                if (lastMsg.textContent === data.data.text) {
                    alreadyAdded = true;
                }
            }
            if (!alreadyAdded) {
                UI.appendUserMessage(data.data.text);
                UI.createBotMessagePlaceholder(chatId, true);
            }
        }
        else if (data.type === "thinking") {
            UI.appendThinkingToken(chatId, data.data.token);
        }
        else if (data.type === "token") {
            UI.appendToken(chatId, data.data.token);
        }
        else if (data.type === "done" || data.type === "error") {
            AppState.stopGenerating(chatId);
            if (data.type === "error") {
                UI.appendToken(chatId, `\n[Hata: ${data.data.message}]`);
            }
            UI.finishGeneration(chatId);
            
            // Reload the chat to show the final complete history
            if (window.loadChat) {
                window.loadChat(chatId);
            }
        }
    }
};

window.processSseEvent = (data) => SSE.processEvent(data);
