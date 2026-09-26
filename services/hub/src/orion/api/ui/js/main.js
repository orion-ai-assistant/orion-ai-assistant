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
            history.forEach((msg, idx) => {
                if (msg.role === 'user') {
                    if (msg.attachments) {
                        UI.appendUserMessage(msg.display_text ?? '', msg.attachments);
                    } else if (Array.isArray(msg.content)) {
                        const text = msg.content.filter(c => c.type === 'text').map(c => c.text).join('\n');
                        const images = msg.content.filter(c => c.type === 'image_url').map(c => c.image_url.url);
                        UI.appendUserMessage(text, images);
                    } else UI.appendUserMessage(msg.content, []);
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

    const messageInputEl = document.getElementById('message-input');
    messageInputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            const text = UI.messageInput.value.trim();
            if (text || (UI.selectedImages && UI.selectedImages.length > 0)) {
                API.sendMessage(text);
                if (window.STT) {
                    if (window.STT.isStreaming) window.STT.stop();
                    window.STT.reset();
                }
            }
        }
    });

    messageInputEl.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
        if (this.value === '') {
            this.style.height = '38px';
        }
    });

    const originalClearInput2 = UI.clearInput.bind(UI);
    UI.clearInput = function() {
        originalClearInput2();
        if (messageInputEl) {
            messageInputEl.style.height = '38px';
        }
    };

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

    // --- Image Upload Logic ---
    UI.selectedImages = [];

    const imageUploadBtn = document.getElementById('composer-image');
    const imageUploadInput = document.getElementById('image-upload');
    const imagePreviewContainer = document.getElementById('image-preview-container');

    const renderImagePreviews = () => {
        if (!imagePreviewContainer) return;
        imagePreviewContainer.innerHTML = '';
        if (UI.selectedImages.length === 0) {
            imagePreviewContainer.style.display = 'none';
            return;
        }
        imagePreviewContainer.style.display = 'flex';

        UI.selectedImages.forEach((fileObj, index) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'image-preview-item';
            const previewEl = UI.createAttachmentCard(fileObj);

            const removeBtn = document.createElement('button');
            removeBtn.innerHTML = '&times;';
            removeBtn.style = 'position: absolute; top: 4px; right: 4px; background: rgba(0,0,0,0.7); border: 1px solid rgba(255,255,255,0.15); color: white; border-radius: 50%; width: 20px; height: 20px; cursor: pointer; font-size: 13px; line-height: 18px; text-align: center; padding: 0; display:flex; align-items:center; justify-content:center; transition: all 0.2s;';
            removeBtn.onmouseover = () => { removeBtn.style.background = '#ef4444'; removeBtn.style.borderColor = '#ef4444'; };
            removeBtn.onmouseout = () => { removeBtn.style.background = 'rgba(0,0,0,0.7)'; removeBtn.style.borderColor = 'rgba(255,255,255,0.15)'; };
            removeBtn.onclick = () => {
                UI.selectedImages.splice(index, 1);
                renderImagePreviews();
            };

            wrapper.appendChild(previewEl);
            wrapper.appendChild(removeBtn);

            // Name tooltip
            wrapper.title = fileObj.name;

            imagePreviewContainer.appendChild(wrapper);
        });
    };

    const processFiles = (files) => {
        for (let i = 0; i < files.length; i++) {
            const file = files[i];

            // Basic deduplication check by name + size
            const isDuplicate = UI.selectedImages.some(item => item.name === file.name && item.size === file.size);
            if (isDuplicate) {
                if (UI.showToast) UI.showToast("Bu dosya (" + file.name + ") zaten eklendi.");
                continue;
            }

            if (file.type.startsWith('image/')) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const img = new Image();
                    img.onload = () => {
                        // Canvas tabanlı JPEG dönüştürme (WebP gibi yerel modellerin desteklemediği formatlar için)
                        const canvas = document.createElement('canvas');
                        canvas.width = img.width;
                        canvas.height = img.height;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(img, 0, 0);
                        // Dönüştür ve listeye ekle
                        const dataUrl = canvas.toDataURL('image/jpeg', 0.9);
                        UI.selectedImages.push({
                            data: dataUrl,
                            name: file.name,
                            size: file.size
                        });
                        renderImagePreviews();
                    };
                    img.src = e.target.result;
                };
                reader.readAsDataURL(file);
            } else if (file.type.startsWith('video/') || file.type.startsWith('audio/')) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    UI.selectedImages.push({
                        data: e.target.result,
                        name: file.name,
                        size: file.size
                    });
                    renderImagePreviews();
                };
                reader.readAsDataURL(file);
            } else if (file.type.startsWith('text/') || /\.(txt|md|py|js|json|csv|log|yaml|yml|html|css|sql|sh|bat)$/i.test(file.name)) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    UI.selectedImages.push({
                        data: e.target.result,
                        name: file.name,
                        size: file.size,
                        isText: true
                    });
                    renderImagePreviews();
                };
                reader.readAsText(file);
            }
        }
    };

    if (imageUploadBtn && imageUploadInput) {
        imageUploadBtn.addEventListener('click', () => {
            imageUploadInput.click();
            // Hide the composer menu if open
            const composerMenu = document.getElementById('composer-menu');
            if (composerMenu) composerMenu.hidden = true;
            const composerPlus = document.getElementById('composer-plus');
            if (composerPlus) composerPlus.setAttribute('aria-expanded', 'false');
        });

        imageUploadInput.addEventListener('change', (e) => {
            if (e.target.files) {
                processFiles(e.target.files);
            }
            e.target.value = ''; // Reset
        });
    }

    const messageInput = document.getElementById('message-input');
    if (messageInput) {
        messageInput.addEventListener('paste', (e) => {
            if (e.clipboardData && e.clipboardData.files && e.clipboardData.files.length > 0) {
                processFiles(e.clipboardData.files);
                e.preventDefault(); // Don't paste image as text
            }
        });
    }

    const inputWrapper = document.querySelector('.input-dock');
    if (inputWrapper) {
        inputWrapper.addEventListener('dragover', (e) => {
            e.preventDefault();
            inputWrapper.style.boxShadow = '0 0 0 2px #007bff';
        });
        inputWrapper.addEventListener('dragleave', (e) => {
            e.preventDefault();
            inputWrapper.style.boxShadow = '';
        });
        inputWrapper.addEventListener('drop', (e) => {
            e.preventDefault();
            inputWrapper.style.boxShadow = '';
            if (e.dataTransfer && e.dataTransfer.files) {
                processFiles(e.dataTransfer.files);
            }
        });
    }

    const originalClearInput = UI.clearInput.bind(UI);
    UI.clearInput = function() {
        originalClearInput();
        if (this.selectedImages && this.selectedImages.length > 0) {
            this.selectedImages = [];
            renderImagePreviews();
        }
    };

    // Ensure initial state is clean
    renderImagePreviews();
    // --- End Image Upload Logic ---

});
