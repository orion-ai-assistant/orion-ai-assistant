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
        if (history?.status === 404) {
            UI.applyHistoryDeletion(chatId, true);
            return;
        }

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
                        UI.appendUserMessage(msg.display_text ?? '', msg.attachments, msg.failed_turn, msg.turn_id);
                    } else if (Array.isArray(msg.content)) {
                        const text = msg.content.filter(c => c.type === 'text').map(c => c.text).join('\n');
                        const images = msg.content.filter(c => c.type === 'image_url').map(c => c.image_url.url);
                        UI.appendUserMessage(text, images, msg.failed_turn, msg.turn_id);
                    } else UI.appendUserMessage(msg.content, [], msg.failed_turn, msg.turn_id);
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
                            msg.tool_activity || [], msg.display_parts, msg.failed_turn || Boolean(msg.generation_error),
                            msg.failed_turn_key || msg.turn_id);
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

    const initAuth = async (verifiedUser = null) => {
        if (!HubPicker.connected) {
            document.getElementById('auth-modal').classList.add('show');
            return false;
        }
        if (Auth.invitationToken) {
            document.getElementById('auth-modal').classList.add('show');
            return false;
        }
        const token = Auth.getToken();
        if (!token) {
            document.getElementById("auth-modal").classList.add("show");
            return false;
        }
        try {
            const user = verifiedUser || await Auth.getMe();
            HubPicker.rememberSuccess();
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
            DevicePairing.start();
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

    const showAuthFailure = error => {
        if (error.retryAfter) {
            AuthWait.start(document.getElementById('auth-error'),
                [document.getElementById('auth-login-btn'), document.getElementById('auth-register-btn')], error.retryAfter);
        } else showError(error.message);
    };

    const fragment = new URLSearchParams(location.hash.slice(1));
    Auth.invitationToken = fragment.get('invite');
    await HubPicker.init({invitation: Boolean(Auth.invitationToken)});
    const registerButton = document.getElementById('auth-register-btn');
    const loginButton = document.getElementById('auth-login-btn');
    const description = document.getElementById('auth-description');
    description.removeAttribute('data-i18n');
    let registrationMode = false;
    let canRegister = HubPicker.self?.registration_allowed === true && HubPicker.selected?.id === HubPicker.self?.id;
    const loginTab = document.getElementById('auth-login-tab');
    const registerTab = document.getElementById('auth-register-tab');
    const setRegistrationMode = enabled => {
        if (enabled && !canRegister) return;
        registrationMode = enabled;
        HubPicker.registrationAllowed = canRegister;
        loginTab.setAttribute('aria-selected', String(!enabled));
        registerTab.setAttribute('aria-selected', String(enabled));
        registerTab.disabled = !canRegister;
        document.getElementById('auth-error').style.display = 'none';
        document.getElementById('auth-title').textContent = enabled ? 'Yeni hesap oluştur' : 'Orion Hub';
        document.getElementById('auth-confirm-group').hidden = !enabled;

        document.getElementById('auth-confirm-password').value = '';
        document.getElementById('auth-password').value = '';
        document.getElementById('auth-password').autocomplete = enabled ? 'new-password' : 'current-password';
        loginButton.hidden = enabled;
        registerButton.removeAttribute('data-i18n');
        registerButton.classList.toggle('btn-primary', enabled);
        registerButton.classList.toggle('btn-secondary', !enabled);
        registerButton.textContent = 'Hesap oluştur';
        registerButton.hidden = !enabled;
        description.textContent = enabled ? 'Kullanıcı adı ve şifre seç. Hesabın oluşturulunca sohbete geçeceksin.' : 'Hesabınla giriş yap veya yeni hesap oluştur.';
    };
    loginTab.onclick = () => setRegistrationMode(false);
    registerTab.onclick = () => setRegistrationMode(true);
    setRegistrationMode(false);
    registerButton.hidden = true;
    if (Auth.invitationToken) {
        loginButton.hidden = true;
        description.textContent = 'Davet kontrol ediliyor…';
        try {
            const response = await fetch(HubPicker.apiPath('/api/v1/auth/invitations/preview'), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({invitation_token: Auth.invitationToken})});
            const data = await response.json();
            if (!response.ok) {
                const error = new Error(typeof data.detail === 'string' ? data.detail : 'Davet bağlantısı geçersiz.');
                if (response.status === 429) error.retryAfter = AuthWait.retrySeconds(response);
                throw error;
            }
            description.textContent = 'Orion’a davet edildin. Kullanıcı adı ve şifre seç; hesabın oluşturulunca sohbete geçeceksin.';
            registerButton.hidden = false;
            registerButton.removeAttribute('data-i18n');
            registerButton.textContent = 'Hesap oluştur ve başla';
            canRegister = true;
            setRegistrationMode(true);
        } catch (error) {
            description.textContent = 'Yeni bir davet bağlantısı için PC sahibiyle iletişime geç.';
            showAuthFailure(error);
        }
    } else {
        const localRegistration = HubPicker.self?.registration_allowed === true && HubPicker.selected?.id === HubPicker.self?.id;
        canRegister = localRegistration;
        setRegistrationMode(false);
        description.textContent = localRegistration
            ? 'Hesabınla giriş yap veya bu bilgisayarda yeni hesap oluştur.'
            : 'Hesabınla giriş yap. Yeni hesap için PC sahibinden davet bağlantısı al.';
    }

    document.getElementById("auth-login-btn").addEventListener("click", async () => {
        if (!HubPicker.connected || HubPicker.authenticating) return;
        const user = document.getElementById("auth-username").value.trim();
        const pass = document.getElementById("auth-password").value;
        if (!user || !pass) {
            showError("Kullanıcı adı ve şifre zorunludur.");
            return;
        }
        HubPicker.authenticating = true;
        HubPicker.updateButtons();
        const label = loginButton.textContent;
        loginButton.textContent = 'Giriş yapılıyor…';
        const namespace = Auth.tokenKey;
        try {
            await Auth.login(user, pass);
            if (namespace !== Auth.tokenKey || !HubPicker.connected) return;
            await initAuth({username: user});
            loadInitialSettings();
            if (window.loadChats) window.loadChats();
        } catch (error) {
            showAuthFailure(error);
        } finally {
            HubPicker.authenticating = false;
            HubPicker.updateButtons();
            loginButton.textContent = label;
            if (!document.getElementById('auth-error')._authWaitTimer) loginButton.disabled = !HubPicker.connected;
        }
    });

    document.getElementById("auth-register-btn").addEventListener("click", async () => {
        if (!HubPicker.connected || HubPicker.authenticating) return;
        if (!registrationMode) { setRegistrationMode(true); return; }
        const user = document.getElementById("auth-username").value.trim();
        const pass = document.getElementById("auth-password").value;
        if (!user || !pass) {
            showError("Kullanıcı adı ve şifre zorunludur.");
            return;
        }
        if (pass !== document.getElementById('auth-confirm-password').value) {
            showError('Şifreler eşleşmiyor.');
            return;
        }
        const namespace = Auth.tokenKey;
        HubPicker.authenticating = true;
        HubPicker.updateButtons();
        try {
            await Auth.register(user, pass);
            setRegistrationMode(false);
            registerButton.hidden = true;
            if (namespace !== Auth.tokenKey || !HubPicker.connected) return;
            await initAuth({username: user});
            loadInitialSettings();
            if (window.loadChats) window.loadChats();
        } catch (error) {
            showAuthFailure(error);
        } finally {
            HubPicker.authenticating = false;
            HubPicker.updateButtons();
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
        DevicePairing.stop();
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
            if (window.STT.isStreaming || window.STT.isStarting) window.STT.stop();
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
                    if (window.STT.isStreaming || window.STT.isStarting) window.STT.stop();
                    window.STT.reset();
                }
            }
        }
    });

    messageInputEl.addEventListener('input', () => UI.resizeMessageInput());
    messageInputEl.addEventListener('focus', () => UI.resizeMessageInput());
    window.addEventListener('resize', () => UI.resizeMessageInput());
    UI.resizeMessageInput();

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
            if (targetId !== 'settings-view') UI.clearSavedRouterApiKeyInput();
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
                DevicePairing.renderControls();

                loadInitialSettings();

                window.ToolsUI?.refreshBrowserStatus?.();

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
    var settingsLoading = false;
    var settingsInitialRequested = false;
    var settingsSnapshot = null;
    var settingsContext = null;
    function settingsVisible() { return !document.hidden && document.getElementById('settings-view')?.classList.contains('active'); }
    async function loadInitialSettings(initial = false, manual = false) {
        const accountContext = Auth.tokenKey + ':' + Auth.getToken();
        if(settingsContext!==accountContext){settingsContext=accountContext;settingsInitialRequested=false;settingsSnapshot=null;}
        const initialLoad = initial === true && !settingsInitialRequested;
        if ((!settingsVisible() && !initialLoad) || settingsLoading || !Auth.getToken() || !AppState.sseConnected) return;
        if (initialLoad) settingsInitialRequested = true;
        settingsLoading = true;
        const refreshButton = document.getElementById('settings-refresh');
        if (manual && refreshButton) refreshButton.disabled = true;
        try {
            const data = await API.getSettings();
            if (settingsContext===accountContext && Auth.tokenKey + ':' + Auth.getToken()===accountContext && (initialLoad || settingsVisible()) && data && !data.error) {
                DevicePairing.renderControls();
                const snapshot=JSON.stringify(data);
                if(snapshot!==settingsSnapshot){UI.renderSettings(data);settingsSnapshot=snapshot;}
                else window.RouterProviders?.mount();
                await UI.loadModelChoices(data, !manual);
                if (data.tts_enabled !== undefined) {
                    const isEnabled = String(data.tts_enabled).toLowerCase() === 'true';
                    localStorage.setItem("orion_tts_enabled", isEnabled ? "true" : "false");
                }
            }
        } finally {
            settingsLoading = false;
            if (manual && refreshButton) refreshButton.disabled = false;
        }
    };
    document.getElementById('settings-refresh').onclick = () => loadInitialSettings(false, true);
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
            wrapper.setAttribute('draggable', 'false');
            wrapper.ondragstart = (e) => e.preventDefault();
            const previewEl = UI.createAttachmentCard(fileObj);

            const removeBtn = document.createElement('button');
            removeBtn.setAttribute('draggable', 'false');
            removeBtn.innerHTML = '&times;';
            removeBtn.type = 'button';
            removeBtn.className = 'attachment-remove';
            removeBtn.setAttribute('aria-label', 'Eki kaldır');
            removeBtn.onclick = () => {
                UI.selectedImages.splice(index, 1);
                renderImagePreviews();
            };

            wrapper.appendChild(previewEl);
            wrapper.appendChild(removeBtn);

            imagePreviewContainer.appendChild(wrapper);
        });
    };

    let attachmentSequence = 0;
    const processFiles = (files) => {
        for (const file of files) {
            if (UI.selectedImages.some(item => item.name === file.name && item.size === file.size)) {
                UI.showToast?.('Bu dosya (' + file.name + ') zaten eklendi.');
                continue;
            }
            const isText = file.type.startsWith('text/') || /\.(txt|md|py|js|json|csv|log|yaml|yml|html|css|sql|sh|bat)$/i.test(file.name);
            const media = /^(image|audio|video)\//.test(file.type);
            if (!isText && !media) {
                UI.showToast?.('Desteklenmeyen dosya türü: ' + file.name);
                continue;
            }
            // Reserve the slot immediately, before any asynchronous read/decode.
            const item = {
                id: `attachment-${Date.now()}-${++attachmentSequence}`, name: file.name, size: file.size,
                mime_type: file.type || 'text/plain', isText, data: '', loading: true
            };
            UI.selectedImages.push(item);
            renderImagePreviews();
            const fail = () => {
                const index = UI.selectedImages.indexOf(item);
                if (index < 0) return;
                UI.selectedImages.splice(index, 1);
                renderImagePreviews();
                UI.showToast?.('Dosya okunamadı: ' + file.name);
            };
            const finish = (data, mime) => {
                // Removed files and cleared drafts must not reappear on completion.
                if (!UI.selectedImages.includes(item)) return;
                if (UI.selectedImages.some(other => other !== item && other.data && other.data === data)) {
                    const index = UI.selectedImages.indexOf(item);
                    if (index >= 0) UI.selectedImages.splice(index, 1);
                    renderImagePreviews();
                    UI.showToast?.('Bu dosya (' + (file.name || 'Görsel') + ') zaten ekli.');
                    return;
                }
                item.data = data;
                item.mime_type = mime;
                item.loading = false;
                renderImagePreviews();
            };
            const reader = new FileReader();
            reader.onerror = fail;
            reader.onabort = fail;
            reader.onload = () => {
                if (!UI.selectedImages.includes(item)) return;
                if (file.type.startsWith('image/')) {
                    const img = new Image();
                    img.onerror = fail;
                    img.onload = () => {
                        try {
                            const canvas = document.createElement('canvas');
                            canvas.width = img.width;
                            canvas.height = img.height;
                            const ctx = canvas.getContext('2d');
                            ctx.drawImage(img, 0, 0);
                            finish(canvas.toDataURL('image/jpeg', 0.9), 'image/jpeg');
                        } catch { fail(); }
                    };
                    img.src = reader.result;
                } else finish(reader.result, item.mime_type);
            };
            if (isText) reader.readAsText(file);
            else reader.readAsDataURL(file);
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

    let isInternalDrag = false;
    document.addEventListener('dragstart', () => {
        isInternalDrag = true;
    });
    document.addEventListener('dragend', () => {
        isInternalDrag = false;
    });

    // Prevent default browser behavior (navigating to file when dropped outside drop zone)
    window.addEventListener('dragover', (e) => {
        if (!e.target.closest || !e.target.closest('.input-dock')) {
            e.preventDefault();
            if (e.dataTransfer) e.dataTransfer.dropEffect = 'none';
        }
    });
    window.addEventListener('drop', (e) => {
        if (!e.target.closest || !e.target.closest('.input-dock')) {
            e.preventDefault();
        }
    });

    const inputWrapper = document.querySelector('.input-dock');
    if (inputWrapper) {
        inputWrapper.addEventListener('dragover', (e) => {
            if (isInternalDrag) {
                if (e.dataTransfer) e.dataTransfer.dropEffect = 'none';
                return;
            }
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
            if (isInternalDrag) {
                return;
            }
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

// Drag selection may span messages; Select All belongs to the clicked message.
let activeMessage = null;
const messageSelector = '.message.bot, .message.user';
document.addEventListener('pointerdown', event => {
    activeMessage = event.target.closest(messageSelector);
});
document.addEventListener('keydown', event => {
    if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'a') return;
    if (event.target.closest('input, textarea, [contenteditable="true"]')) return;
    const selection = window.getSelection();
    const selectedMessage = selection?.anchorNode?.parentElement?.closest(messageSelector);
    const message = activeMessage?.isConnected ? activeMessage : selectedMessage;
    if (!message || !selection) return;
    const walker = document.createTreeWalker(message, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
            return node.parentElement?.closest('.audio-player-container, .message-meta, button, [hidden]')
                ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
        }
    });
    let first = null, last = null, node;
    while ((node = walker.nextNode())) { first ||= node; last = node; }
    event.preventDefault();
    selection.removeAllRanges();
    if (!first) return;
    const range = document.createRange();
    range.setStart(first, 0); range.setEnd(last, last.textContent.length);
    selection.addRange(range);
});
