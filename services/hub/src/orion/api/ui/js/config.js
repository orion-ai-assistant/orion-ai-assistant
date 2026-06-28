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

    isGenerating() {
        return this.generatingChats.has(this.currentChatId);
    },
    startGenerating(chatId) {
        this.generatingChats.add(chatId);
    },
    stopGenerating(chatId) {
        this.generatingChats.delete(chatId);
    },
    isAnyChatGenerating(chatId) {
        return this.generatingChats.has(chatId);
    }
};
