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
    generatingChats: new Set(),  // chat_id'ler burada tutulur
    eventSource: null,
    sseConnected: false,
    sseShouldReconnect: false,
    sseReconnectAttempt: 0,
    sseReconnectTimer: null,
    sseConnectTimer: null,
    generationStartedAt: new Map(),
    firstTokenAt: new Map(),

    isGenerating() {
        return this.generatingChats.has(this.currentChatId);
    },
    startGenerating(chatId) {
        this.generatingChats.add(chatId);
        if (!this.generationStartedAt.has(chatId)) {
            this.generationStartedAt.set(chatId, performance.now());
        }
    },
    stopGenerating(chatId) {
        this.generatingChats.delete(chatId);
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
            first_token_ms: Math.max(1, Math.round((firstTokenAt ?? now) - startedAt)),
            total_ms: Math.max(1, Math.round(now - startedAt)),
        };
    },
    clearGenerationMetrics(chatId) {
        this.generationStartedAt.delete(chatId);
        this.firstTokenAt.delete(chatId);
    },
    isAnyChatGenerating(chatId) {
        return this.generatingChats.has(chatId);
    }
};
