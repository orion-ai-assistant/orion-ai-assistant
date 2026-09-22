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
        const turnId = AppState.getEventTurnId(data);
        console.log("Gelen olay:", data);

        if (AppState.pendingNewChatRequest && chatId) {
            // The first server event already proves that the new chat was
            // queued. Adopt its id immediately so tokens do not wait for the
            // slower HTTP response and durable metadata write.
            if (!AppState.adoptPendingNewChat(chatId)) {
                AppState.pendingChatEvents.push(data);
                return;
            }
        }

        if (data.type === 'chat_title_warning') {
            if (chatId === AppState.currentChatId) {
                const notice = document.createElement('div');
                notice.className = 'message bot';
                notice.textContent = data.data?.message || 'Başlık güncellenemedi.';
                UI.chatArea.appendChild(notice);
            }
            return;
        }

        // Real-time synchronization for chat modifications
        if (data.type === "chat_rename") {
            if (window.loadChats) window.loadChats();
            return;
        }
        if (data.type === "chat_delete") {
            if (window.loadChats) window.loadChats();
            if (chatId === AppState.currentChatId) {
                AppState.selectChat(null);
                UI.clearChatArea();
                UI.chatArea.innerHTML = '<div class="message bot">Bu sohbet başka bir cihazdan veya yöneticiden silindi.</div>';
                UI.setStopButtonVisible(false);
            }
            return;
        }

        if (chatId && (data.type === "accepted" || data.type === "user_message")) {
            const activeTurn = AppState.getActiveTurn(chatId);
            if (activeTurn?.status === "stopping" && AppState.isKnownTurn(chatId, turnId)) {
                return;
            }
            if (!AppState.isKnownTurn(chatId, turnId)) {
                AppState.startGenerating(chatId, turnId);
            }
            if (chatId === AppState.currentChatId) {
                UI.setStopButtonVisible(true);
            }
        }

        if (
            chatId
            && ["thinking", "token", "snapshot", "audio", "text_done"].includes(data.type)
            && !AppState.isStreamingTurn(chatId, turnId)
        ) {
            return;
        }

        if (
            chatId
            && ["done", "error"].includes(data.type)
            && !AppState.isKnownTurn(chatId, turnId)
        ) {
            return;
        }

        // Preserve event order while a history snapshot is loading. This must
        // include terminal events: otherwise a fast `done` can be processed
        // before an older partial snapshot is rendered, reopening stale text.
        if (AppState._loadingHistory && chatId === AppState.currentChatId) {
            if (!AppState._sseBuffer) AppState._sseBuffer = [];
            AppState._sseBuffer.push(data);
            return;
        }

        // ---- Events for non-active chats: just refresh sidebar ----
        if (data.type === 'text_done') {
            UI.showTextMetrics(chatId, data.data);
            return;
        }
        if (data.type === 'audio') {
            const timing = AppState.getGenerationMetrics(chatId);
            UI.appendAudio(chatId, { ...data.data, arrival_ms: timing?.total_ms }, chatId === AppState.currentChatId);
            return;
        }
        if (chatId && chatId !== AppState.currentChatId) {
            if (data.type === "user_message" || data.type === "accepted") {
                if (window.loadChats) window.loadChats();
            }
            if (data.type === "token") {
                AppState.markFirstToken(chatId);
                UI.appendToken(chatId, data.data.token);
            } else if (data.type === "snapshot") {
                AppState.markFirstToken(chatId);
                UI.replaceMessageContent(chatId, data.data.content);
            } else if (data.type === "thinking") {
                AppState.markFirstToken(chatId);
                UI.appendThinkingToken(chatId, data.data.token);
            } else if (data.type === "done" || data.type === "error") {
                AppState.stopGenerating(chatId, turnId);
                UI.finishGeneration(chatId, true, data.data);
                AppState.clearGenerationMetrics(chatId);
            }
            return;
        }

        // ---- Events for the active chat ----
        if (data.type === "accepted") {
            // CRITICAL FIX: Don't create duplicate placeholder if already generating
            const activeState = UI._chatDivs[chatId];
            if (!activeState || !activeState.botDiv) {
                UI.createBotMessagePlaceholder(chatId);
            }
        }
        else if (data.type === "user_message") {
            // A new turn must never reuse a leftover live bot element from a
            // stopped generation.
            const activeState = UI._chatDivs[chatId];
            if (activeState && activeState.botDiv) {
                UI.finishGeneration(chatId, true, { status: "stopped" });
            }
            const alreadyRendered = AppState.claimOptimisticUserMessage(chatId, data.data.text);
            if (!alreadyRendered) {
                UI.appendUserMessage(data.data.text);
            }
            // CRITICAL FIX: Don't create duplicate placeholder if already generating
            const currentState = UI._chatDivs[chatId];
            if (!currentState || !currentState.botDiv) {
                UI.createBotMessagePlaceholder(chatId, true);
            }
        }
        else if (data.type === "thinking") {
            console.log(`[DEBUG] Thinking token received for ${chatId}`);
            AppState.markFirstToken(chatId);
            UI.appendThinkingToken(chatId, data.data.token);
        }
        else if (data.type === "token") {
            console.log(`[DEBUG] Content token received for ${chatId}`);
            AppState.markFirstToken(chatId);
            UI.appendToken(chatId, data.data.token);
        }
        else if (data.type === "snapshot") {
            console.log(`[DEBUG] Final content snapshot received for ${chatId}`);
            AppState.markFirstToken(chatId);
            UI.replaceMessageContent(chatId, data.data.content);
        }
        else if (data.type === "done" || data.type === "error") {
            console.log(`Generation ${data.type} for chat ${chatId}. Ready for next message.`);
            
            AppState.stopGenerating(chatId, turnId);
            
            if (data.type === "error") {
                UI.appendToken(chatId, `\n[Hata: ${data.data.message}]`);
            }
            UI.finishGeneration(chatId, true, data.data);
            AppState.clearGenerationMetrics(chatId);
            console.log(`Chat ${chatId} ready for next message`);
            
            // CRITICAL: Always hide stop button when done/error arrives
            if (chatId === AppState.currentChatId) {
                UI.setStopButtonVisible(false);
                console.log(`Stop button hidden for active chat ${chatId}`);
            }
            
            // Sidebar sohbet listesini ve başlıklarını güncelle (aktif sohbet ekranını sıfırlama)
            if (window.loadChats) {
                window.loadChats();
            }
        }
    }
};

window.processSseEvent = (data) => SSE.processEvent(data);
