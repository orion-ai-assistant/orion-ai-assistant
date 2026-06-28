/**
 * Main Initialization and Event Listeners
 */
document.addEventListener("DOMContentLoaded", async () => {
    
    // Auth Initialization
    const loadChats = async () => {
        if (!Auth.getToken() || !AppState.sseConnected) return;
        const chats = await API.getChats();
        if (chats && !chats.error) {
            UI.renderChatList(chats, AppState.currentChatId);
        }
    };
    window.loadChats = loadChats;
    let loadChatSeq = 0;

    window.loadChat = async (chatId) => {
        if (!AppState.sseConnected) return;
        AppState.currentChatId = chatId;
        AppState._loadingHistory = true;
        AppState._sseBuffer = [];
        
        const currentSeq = ++loadChatSeq;
        
        // Load history from server
        const history = await API.getChatHistory(chatId);
        
        // If another loadChat was called while we were fetching, discard this one
        if (currentSeq !== loadChatSeq) return;
        
        UI.clearChatArea();
        
        let hasPartial = false;
        if (history && !history.error) {
            if (history.length === 0) {
                UI.chatArea.innerHTML = '<div class="message bot">Geçmiş bulunamadı.</div>';
            }
            history.forEach(msg => {
                if (msg.role === 'user') {
                    if (Array.isArray(msg.content)) {
                        const textObj = msg.content.find(c => c.type === 'text' && c.text !== '<audio>' && c.text !== '</audio>\\n');
                        UI.appendUserMessage(textObj ? textObj.text : "İçerik");
                    } else {
                        UI.appendUserMessage(msg.content);
                    }
                } else if (msg.role === 'assistant') {
                    if (msg.partial) {
                        // In-progress message from server — set up the live botDiv for this chat
                        hasPartial = true;
                        
                        // Clear any old live state that might have been carried over
                        if (UI._chatDivs[chatId]) {
                            UI._chatDivs[chatId] = { botDiv: null, thinkDiv: null, thinkBody: null };
                        }
                        
                        UI.createBotMessagePlaceholder(chatId);
                        if (msg.thinking) {
                            UI.appendThinkingToken(chatId, msg.thinking);
                        }
                        UI.appendToken(chatId, msg.content);
                    } else {
                        UI.appendStaticBotMessage(msg.content, msg.thinking);
                    }
                }
            });
        } else {
            UI.chatArea.innerHTML = `<div class="message bot">Hata: ${history ? history.error : 'Geçmiş alınamadı.'}</div>`;
        }
        
        if (!hasPartial) {
            AppState.stopGenerating(chatId);
        }

        if (hasPartial || AppState.isAnyChatGenerating(chatId)) {
            // Chat is generating live via SSE — re-attach the bot div if not already there
            const chatState = UI._chatDivs[chatId];
            if (chatState && chatState.botDiv && !UI.chatArea.contains(chatState.botDiv)) {
                UI.chatArea.appendChild(chatState.botDiv);
            }
            UI.setStopButtonVisible(true);
        } else {
            UI.setStopButtonVisible(false);
        }

        
        UI.scrollToBottom();
        loadChats();

        AppState._loadingHistory = false;
        // Replay any buffered SSE events that arrived during the fetch
        const buffered = AppState._sseBuffer;
        AppState._sseBuffer = [];
        if (buffered && buffered.length > 0) {
            buffered.forEach(eventData => {
                if (window.processSseEvent) window.processSseEvent(eventData);
            });
        }
    };

    const initAuth = async () => {
        const token = Auth.getToken();
        if (!token) {
            document.getElementById("auth-modal").classList.add("show");
            return false;
        }
        try {
            const user = await Auth.getMe();
            AppConfig.setUserId(user.username);
            document.getElementById("user-profile-badge").textContent = `Hoşgeldin, ${user.username}`;
            document.getElementById("auth-modal").classList.remove("show");
            SSE.connect();
            return true;
        } catch (error) {
            document.getElementById("auth-modal").classList.add("show");
            return false;
        }
    };

    // Auth Events
    const showError = (msg) => {
        const errEl = document.getElementById("auth-error");
        errEl.textContent = msg;
        errEl.style.display = "block";
    };

    document.getElementById("auth-login-btn").addEventListener("click", async () => {
        const user = document.getElementById("auth-username").value.trim();
        const pass = document.getElementById("auth-password").value.trim();
        if (!user || !pass) {
            showError("Kullanıcı adı ve şifre zorunludur.");
            return;
        }
        try {
            await Auth.login(user, pass);
            await initAuth();
            loadInitialSettings();
        } catch (error) {
            showError(error.message);
        }
    });

    document.getElementById("auth-register-btn").addEventListener("click", async () => {
        const user = document.getElementById("auth-username").value.trim();
        const pass = document.getElementById("auth-password").value.trim();
        if (!user || !pass) {
            showError("Kullanıcı adı ve şifre zorunludur.");
            return;
        }
        try {
            await Auth.register(user, pass);
            await Auth.login(user, pass);
            await initAuth();
            loadInitialSettings();
        } catch (error) {
            showError(error.message);
        }
    });

    // Initial check
    const isAuthenticated = await initAuth();

    // Initial UI state
    UI.clearChatArea();
    UI.setConnectionStatus(false, "connecting");

    // Event Listeners
    document.getElementById('new-chat-btn').addEventListener('click', () => {
        AppState.currentChatId = null;
        UI.clearChatArea();
        UI.chatArea.innerHTML = '<div class="message bot">Yeni sohbet başlatıldı. Lütfen bir mesaj gönderin.</div>';
        loadChats();
    });

    document.getElementById('logout-btn').addEventListener('click', () => {
        SSE.disconnect();
        if (window.stopUserPolling) window.stopUserPolling();
        // State temizliği
        AppState.currentChatId = null;
        AppState.generatingChats.clear();
        
        // UI Temizliği
        UI.setConnectionStatus(false, "disconnected");
        UI.chatArea.innerHTML = '<div class="message bot">Oturum kapatıldı. Lütfen tekrar giriş yapın.</div>';
        
        Auth.clearToken();
        document.getElementById("auth-modal").classList.add("show");
    });

    document.getElementById('send-btn').addEventListener('click', () => {
        const text = UI.messageInput.value.trim();
        API.sendMessage(text);
    });

    document.getElementById('stop-btn').addEventListener('click', () => {
        API.stopGeneration();
    });

    document.getElementById('message-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            const text = UI.messageInput.value.trim();
            API.sendMessage(text);
        }
    });

    // Tab Switching
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            // Remove active from all tabs and views
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));

            // Add active to clicked tab and corresponding view
            e.target.classList.add('active');
            const targetId = e.target.getAttribute('data-target');
            document.getElementById(targetId).classList.add('active');
        });
    });

    // Check Status Button
    document.getElementById('check-status-btn').addEventListener('click', async () => {
        const chatId = document.getElementById('query-chat-id').value.trim();
        const resultBlock = document.getElementById('status-result');
        if (!chatId) {
            resultBlock.textContent = "Lütfen bir Chat ID girin.";
            return;
        }
        resultBlock.textContent = "Sorgulanıyor...";
        const data = await API.checkJobStatus(chatId);
        resultBlock.textContent = JSON.stringify(data, null, 2);
    });

    // Event listener for dynamically rendered setting save buttons is handled globally via window.saveSetting

    // Load initial settings
    const loadInitialSettings = async () => {
        if (!Auth.getToken() || !AppState.sseConnected) return;
        const data = await API.getSettings();
        if (data && !data.error) {
            UI.renderSettings(data);
        }
    };
    window.loadInitialSettings = loadInitialSettings;

    let userPollTimer = null;
    const startUserPolling = () => {
        if (userPollTimer) return;
        userPollTimer = setInterval(() => {
            if (!AppState.sseConnected) return;
            loadInitialSettings();
            if (window.loadChats) window.loadChats();
        }, 5000);
    };
    const stopUserPolling = () => {
        if (!userPollTimer) return;
        clearInterval(userPollTimer);
        userPollTimer = null;
    };
    window.startUserPolling = startUserPolling;
    window.stopUserPolling = stopUserPolling;

    if (isAuthenticated) {
        loadInitialSettings();
    }
});
