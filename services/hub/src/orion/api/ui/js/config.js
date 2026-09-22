/**
 * Global Configuration and State
 */
const AppConfig = {
    userId: null,
    getUserId: function() { return this.userId; },
    setUserId: function(id) { this.userId = id; }
};

const AppState = {
    currentChatId: null,
    activeTurns: new Map(),
    eventSource: null,
    sseConnected: false,
    sseShouldReconnect: false,
    sseReconnectAttempt: 0,
    sseReconnectTimer: null,
    sseConnectTimer: null,
    pendingNewChatRequest: false,
    pendingChatEvents: [],
    optimisticUserMessage: null,
    generationStartedAt: new Map(),
    firstTokenAt: new Map(),

    isGenerating() {
        return this.isAnyChatGenerating(this.currentChatId);
    },
    showOptimisticUserMessage(text, chatId = null) {
        this.optimisticUserMessage = { text, chatId };
    },
    bindOptimisticUserMessage(chatId) {
        if (this.optimisticUserMessage) {
            this.optimisticUserMessage.chatId = chatId;
        }
    },
    claimOptimisticUserMessage(chatId, text) {
        const pending = this.optimisticUserMessage;
        const matches = Boolean(
            pending
            && pending.chatId === chatId
            && pending.text === text
        );
        if (matches) this.optimisticUserMessage = null;
        return matches;
    },
    clearOptimisticUserMessage() {
        this.optimisticUserMessage = null;
    },
    getEventTurnId(data) {
        return data?.turn_id || data?.generation_id || null;
    },
    getActiveTurn(chatId) {
        return chatId ? this.activeTurns.get(chatId) || null : null;
    },
    startGenerating(chatId, turnId = null) {
        if (!chatId) return false;
        const existing = this.activeTurns.get(chatId);
        if (existing && turnId && existing.turnId === turnId) {
            existing.status = "generating";
            return true;
        }
        this.activeTurns.set(chatId, {
            turnId,
            status: "generating",
            startedAt: performance.now(),
            firstTokenAt: null,
        });
        if (!this.generationStartedAt.has(chatId)) {
            this.generationStartedAt.set(chatId, performance.now());
        }
        return true;
    },
    stopGenerating(chatId, turnId = null) {
        const active = this.getActiveTurn(chatId);
        if (!active) return;
        if (turnId && active.turnId && active.turnId !== turnId) return;
        this.activeTurns.delete(chatId);
    },
    beginStopping(chatId, turnId = null) {
        const active = this.getActiveTurn(chatId);
        if (!active) return false;
        if (active.status === "stopping") return false;
        if (turnId && active.turnId && active.turnId !== turnId) return false;
        active.status = "stopping";
        return true;
    },
    finishStopping(chatId) {
        this.stopGenerating(chatId);
    },
    isStopping(chatId) {
        return this.getActiveTurn(chatId)?.status === "stopping";
    },
    reopenGeneration(chatId) {
        return true;
    },
    isGenerationClosed(chatId) {
        return false;
    },
    isKnownTurn(chatId, turnId) {
        const active = this.getActiveTurn(chatId);
        if (!active) return false;
        return !turnId || !active.turnId || active.turnId === turnId;
    },
    isStreamingTurn(chatId, turnId) {
        const active = this.getActiveTurn(chatId);
        if (!active || !["generating", "stopping"].includes(active.status)) return false;
        return !turnId || !active.turnId || active.turnId === turnId;
    },
    markFirstToken(chatId) {
        if (!this.firstTokenAt.has(chatId)) {
            this.firstTokenAt.set(chatId, performance.now());
        }
    },
    getGenerationMetrics(chatId) {
        const startedAt = this.generationStartedAt.get(chatId);
        if (startedAt === undefined) return null;
        const now = performance.now();
        const firstTokenAt = this.firstTokenAt.get(chatId);
        return {
            first_token_ms: firstTokenAt === undefined ? null : Math.max(1, Math.round(firstTokenAt - startedAt)),
            total_ms: Math.max(1, Math.round(now - startedAt)),
        };
    },
    clearGenerationMetrics(chatId) {
        this.generationStartedAt.delete(chatId);
        this.firstTokenAt.delete(chatId);
    },
    isAnyChatGenerating(chatId) {
        return this.activeTurns.has(chatId);
    }
};
