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

    window.loadChat = async (chatId, forceSnapshot = false) => {
        window.ToolsUI?.dialog?.close();
        if (!AppState.sseConnected) return;
        AppState.selectChat(chatId);
        const selectionVersion = AppState.chatSelectionVersion;
        AppState._loadingHistory = true;
        AppState._sseBuffer = [];
        
        const currentSeq = ++loadChatSeq;
        
        // Load history from server
        const history = await API.getChatHistory(chatId);
        
        // If another loadChat was called while we were fetching, discard this one
        if (currentSeq !== loadChatSeq || selectionVersion !== AppState.chatSelectionVersion) return;

        const cachedLiveState = !forceSnapshot && AppState.getActiveTurn(chatId) ? UI._chatDivs[chatId] : null;
        const hasCachedLiveMessage = Boolean(cachedLiveState?.botDiv);

        UI.clearChatArea();
        
        let hasPartial = false;
        if (Array.isArray(history)) {
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
                    if (msg.tool_calls) return;
                    if (msg.partial) {
                        // In-progress message from server — set up the live botDiv for this chat
                        hasPartial = true;
                        const activeTurn = AppState.getActiveTurn(chatId);
                        if (!activeTurn || (msg.turn_id && activeTurn.turnId !== msg.turn_id)) {
                            AppState.startGenerating(chatId, msg.turn_id || null);
                        }
                        
                        // When navigating back, the detached live element already
                        // contains every SSE token received in the background.
                        // Keep it; use the Redis snapshot only after reload/reconnect.
                        if (!hasCachedLiveMessage) {
                            if (UI._chatDivs[chatId]) {
                                UI._chatDivs[chatId] = { botDiv: null, thinkDiv: null, thinkBody: null, contentNodes: [], currentContentNode: null };
                            }

                            UI.createBotMessagePlaceholder(chatId);
                            if (Array.isArray(msg.display_parts) && msg.display_parts.length) {
                                for (const part of msg.display_parts) {
                                    if (part.type === 'thinking') UI.appendThinkingToken(chatId, part.content || '');
                                    else if (part.type === 'content') UI.appendToken(chatId, part.content || '');
                                    else if (part.type === 'tool') {
                                        const item = (msg.tool_activity || []).find(entry => entry.call_id === part.call_id);
                                        if (item) window.ToolsUI?.liveActivity(chatId, item);
                                    }
                                }
                            } else {
                                if (msg.thinking) UI.appendThinkingToken(chatId, msg.thinking);
                                UI.appendToken(chatId, msg.content);
                                for (const item of msg.tool_activity || []) window.ToolsUI?.liveActivity(chatId, item);
                            }
                        }
                    } else {
                        UI.appendStaticBotMessage(msg.content, msg.thinking, msg.metrics, msg.audio,
                            msg.tool_activity || [], msg.display_parts);
                    }
                }
            });
        } else {
            UI.chatArea.innerHTML = `<div class="message bot">Hata: ${history ? history.error : 'Geçmiş alınamadı.'}</div>`;
        }
        

        // CRITICAL FIX: Only show stop button if chat is ACTIVELY generating in AppState
        // Do NOT rely solely on hasPartial flag from history, as it may be stale
        // when error event just arrived but history hasn't refreshed yet
        const isActivelyGenerating = AppState.isAnyChatGenerating(chatId);
        
        if (isActivelyGenerating) {
            // Chat is generating live via SSE — re-attach the bot div if not already there
            const chatState = UI._chatDivs[chatId];
            if (chatState && chatState.botDiv && !UI.chatArea.contains(chatState.botDiv)) {
                UI.chatArea.appendChild(chatState.botDiv);
            }
            UI.setStopButtonVisible(true);
            console.log(`loadChat: Stop button shown for actively generating chat ${chatId}`);
        } else {
            UI.setStopButtonVisible(false);
            console.log(`loadChat: Stop button hidden for chat ${chatId} (generating=${isActivelyGenerating}, partial=${hasPartial})`);
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

    const renderWelcomeHero = () => {
        UI.chatArea.innerHTML = `
            <div class="welcome-hero">
                <div class="welcome-badge">✦ Orion AI Platform</div>
                <h2 class="welcome-title">Nasıl yardımcı olabilirim?</h2>
                <p class="welcome-desc">Aşağıdaki mesaj kutusuna yazabilir veya mikrofon butonuna basarak doğrudan konuşabilirsiniz.</p>
                <div class="welcome-chips">
                    <button class="prompt-chip" onclick="document.getElementById('message-input').value = 'Bugün hava nasıl?'; document.getElementById('message-input').focus();">🌤️ Bugün hava nasıl?</button>
                    <button class="prompt-chip" onclick="document.getElementById('message-input').value = 'Bana Python ile ilgili bir örnek kod yaz.'; document.getElementById('message-input').focus();">💻 Python örnek kod</button>
                    <button class="prompt-chip" onclick="document.getElementById('message-input').value = 'Orion Router nedir ve ne işe yarar?'; document.getElementById('message-input').focus();">⚡ Orion Router hakkında</button>
                </div>
            </div>
        `;
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
            document.getElementById("user-profile-badge").textContent = user.username;
            document.getElementById("auth-modal").classList.remove("show");
            document.getElementById("auth-password").value = "";
            const errEl = document.getElementById("auth-error");
            if (errEl) errEl.style.display = "none";

            // Oturum kapatıldıktan sonra tekrar giriş yapıldığında ekranı sıfırla
            if (!AppState.currentChatId || (UI.chatArea && UI.chatArea.innerHTML.includes('Oturum kapatıldı'))) {
                UI.clearChatArea();
                renderWelcomeHero();
            }

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
            if (window.loadChats) window.loadChats();
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
            if (window.loadChats) window.loadChats();
        } catch (error) {
            showError(error.message);
        }
    });

    // Initial check
    const isAuthenticated = await initAuth();

    // Initial UI state
    if (!AppState.currentChatId) {
        UI.clearChatArea();
        renderWelcomeHero();
    }
    UI.setConnectionStatus(false, "connecting");

    // Event Listeners
    document.getElementById('new-chat-btn').addEventListener('click', () => {
        window.ToolsUI?.resetDraft();
        AppState.selectChat(null);
        UI.clearChatArea();
        renderWelcomeHero();
        loadChats();
        if (window.STT) window.STT.reset();
    });

    document.getElementById('logout-btn').addEventListener('click', () => {
        window.ToolsUI?.resetDraft();
        SSE.disconnect();
        if (window.stopUserPolling) window.stopUserPolling();
        settingsInitialRequested = false;
        // State temizliği
        AppState.selectChat(null);
        AppState.activeTurns.clear();
        
        // UI Temizliği
        UI.setConnectionStatus(false, "disconnected");
        UI.chatArea.innerHTML = '<div class="message bot">Oturum kapatıldı. Lütfen tekrar giriş yapın.</div>';
        
        Auth.clearToken();
        document.getElementById("auth-modal").classList.add("show");
    });

    document.getElementById('send-btn').addEventListener('click', () => {
        const text = UI.messageInput.value.trim();
        API.sendMessage(text);
        if (window.STT) {
            if (window.STT.isStreaming) window.STT.stop();
            window.STT.reset();
        }
    });

    document.getElementById('stop-btn').addEventListener('click', () => {
        API.stopGeneration();
    });

    document.getElementById('message-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            const text = UI.messageInput.value.trim();
            API.sendMessage(text);
            if (window.STT) {
                if (window.STT.isStreaming) window.STT.stop();
                window.STT.reset();
            }
        }
    });

    const audioToggle = document.getElementById('audio-toggle');
    if (audioToggle) {
        const savedAudio = localStorage.getItem("orion_tts_enabled");
        if (savedAudio !== null) {
            audioToggle.checked = savedAudio === "true";
        }
        audioToggle.addEventListener('change', async () => {
            const isEnabled = audioToggle.checked;
            localStorage.setItem("orion_tts_enabled", isEnabled ? "true" : "false");

            // Also update input in settings modal if open
            const ttsEnabledInput = document.getElementById('setting-input-tts_enabled');
            if (ttsEnabledInput) {
                ttsEnabledInput.value = isEnabled ? "true" : "false";
                ttsEnabledInput.dataset.original = isEnabled ? "true" : "false";
            }

            // Sync with backend settings
            try {
                await API.saveSettings("tts_enabled", isEnabled ? "true" : "false");
            } catch (err) {
                console.warn("Could not save tts_enabled to backend:", err);
            }
        });
    }

    // Tab Switching
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const targetBtn = e.target.closest('.tab-btn');
            if (!targetBtn) return;
            // Remove active from all tabs and views
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));

            // Add active to clicked tab and corresponding view
            targetBtn.classList.add('active');
            const targetId = targetBtn.getAttribute('data-target');
            const viewEl = document.getElementById(targetId);
            if (viewEl) viewEl.classList.add('active');

            // Sohbetler sadece Sohbet sekmesinde görünsün, diğer sayfalarda ilgili kutucuk aktif olsun
            const chatsContainer = document.getElementById('sidebar-chats-container');
            const settingsCard = document.getElementById('sidebar-settings-card');
            const statusCard = document.getElementById('sidebar-status-card');

            if (chatsContainer) chatsContainer.style.display = targetId === 'chat-view' ? 'flex' : 'none';
            if (settingsCard) settingsCard.style.display = targetId === 'settings-view' ? 'flex' : 'none';
            if (statusCard) statusCard.style.display = targetId === 'status-view' ? 'flex' : 'none';

            if (targetId === 'settings-view') {
                loadInitialSettings();
            }
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
    let settingsLoading = false;
    let settingsInitialRequested = false;
    const settingsVisible = () => !document.hidden && document.getElementById('settings-view')?.classList.contains('active');
    const loadInitialSettings = async (initial = false) => {
        const initialLoad = initial === true && !settingsInitialRequested;
        if ((!settingsVisible() && !initialLoad) || settingsLoading || !Auth.getToken() || !AppState.sseConnected) return;
        if (initialLoad) settingsInitialRequested = true;
        settingsLoading = true;
        try {
            const data = await API.getSettings();
            if ((initialLoad || settingsVisible()) && data && !data.error) {
                UI.renderSettings(data);
                await UI.loadModelChoices(data, true);
                if (audioToggle && data.tts_enabled !== undefined) {
                    const isEnabled = String(data.tts_enabled).toLowerCase() === 'true';
                    audioToggle.checked = isEnabled;
                    localStorage.setItem("orion_tts_enabled", isEnabled ? "true" : "false");
                }
            }
        } finally {
            settingsLoading = false;
        }
    };
    window.loadInitialSettings = () => loadInitialSettings(true);
    setInterval(() => loadInitialSettings(), 5000);
    document.addEventListener('visibilitychange', () => loadInitialSettings());

    let userPollTimer = null;
    const startUserPolling = () => {
        if (userPollTimer) return;
        userPollTimer = setInterval(() => {
            if (!AppState.sseConnected) return;
            if (window.loadChats) window.loadChats();
        }, 8000);
    };
    const stopUserPolling = () => {
        if (!userPollTimer) return;
        clearInterval(userPollTimer);
        userPollTimer = null;
    };
    window.startUserPolling = startUserPolling;
    window.stopUserPolling = stopUserPolling;

    if (isAuthenticated) {
        loadInitialSettings(true);
    }

    // STT Canlı Mikrofon Tetikleyici
    const micBtn = document.getElementById('mic-btn');
    if (micBtn) {
        micBtn.addEventListener('click', () => {
            if (window.STT) window.STT.toggle();
        });
    }
});
