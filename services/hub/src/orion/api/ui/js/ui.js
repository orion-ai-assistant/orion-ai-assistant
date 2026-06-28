/**
 * UI Manipulation Functions
 */
const UI = {
    chatArea: document.getElementById('chat-area'),
    statusIndicator: document.getElementById('status-indicator'),
    messageInput: document.getElementById('message-input'),
    stopBtn: document.getElementById('stop-btn'),

    // Per-chat state: { chatId -> { botDiv, thinkDiv, thinkBody } }
    _chatDivs: {},

    _getOrCreateChatState(chatId) {
        if (!this._chatDivs[chatId]) {
            this._chatDivs[chatId] = { botDiv: null, thinkDiv: null, thinkBody: null };
        }
        return this._chatDivs[chatId];
    },

    scrollToBottom() {
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
    },

    appendUserMessage(text) {
        const div = document.createElement('div');
        div.className = 'message user';
        div.textContent = text;
        this.chatArea.appendChild(div);
        this.scrollToBottom();
    },

    appendStaticBotMessage(content, thinking = null) {
        const div = document.createElement('div');
        div.className = 'message bot';
        
        if (thinking) {
            const thinkDiv = document.createElement('details');
            thinkDiv.className = 'think-block';
            thinkDiv.open = false;
            
            const summary = document.createElement('summary');
            summary.className = 'think-summary';
            const charCount = thinking.length;
            summary.innerHTML = `<span class="think-icon">💭</span> Düşünce <span class="think-char-count">(${charCount} karakter)</span>`;
            thinkDiv.appendChild(summary);
            
            const thinkBody = document.createElement('pre');
            thinkBody.className = 'think-body';
            thinkBody.textContent = thinking;
            thinkDiv.appendChild(thinkBody);
            
            div.appendChild(thinkDiv);
        }
        
        const textNode = document.createTextNode(content);
        div.appendChild(textNode);
        
        this.chatArea.appendChild(div);
        this.scrollToBottom();
    },

    createBotMessagePlaceholder(chatId, isWaiting = false) {
        if (!chatId) return;
        const state = this._getOrCreateChatState(chatId);

        // If there's an existing typing placeholder for this chat, reuse it
        if (state.botDiv && state.botDiv.classList.contains('typing')) {
            state.botDiv.innerHTML = '';
            state.botDiv.classList.remove('typing');
            return;
        }

        state.thinkDiv = null;
        state.thinkBody = null;
        state.botDiv = document.createElement('div');
        state.botDiv.className = 'message bot typing';
        state.botDiv.setAttribute('data-chat-id', chatId);

        if (isWaiting) {
            state.botDiv.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
        }

        // Only append to DOM if this is the active chat
        if (chatId === AppState.currentChatId) {
            this.chatArea.appendChild(state.botDiv);
            this.scrollToBottom();
        }
    },

    appendThinkingToken(chatId, token) {
        if (!chatId) return;
        const state = this._getOrCreateChatState(chatId);

        if (!state.botDiv) {
            this.createBotMessagePlaceholder(chatId);
        }
        if (state.botDiv.classList.contains('typing')) {
            state.botDiv.innerHTML = '';
            state.botDiv.classList.remove('typing');
        }

        if (!state.thinkDiv) {
            state.thinkDiv = document.createElement('details');
            state.thinkDiv.className = 'think-block';
            state.thinkDiv.open = true;

            const summary = document.createElement('summary');
            summary.className = 'think-summary';
            summary.innerHTML = '<span class="think-icon">💭</span> Düşünüyor<span class="think-dots">...</span>';
            state.thinkDiv.appendChild(summary);

            state.thinkBody = document.createElement('pre');
            state.thinkBody.className = 'think-body';
            state.thinkDiv.appendChild(state.thinkBody);

            state.botDiv.appendChild(state.thinkDiv);
        }

        state.thinkBody.appendChild(document.createTextNode(token));
        if (chatId === AppState.currentChatId) {
            this.scrollToBottom();
        }
    },

    appendToken(chatId, token) {
        if (!chatId) return;
        const state = this._getOrCreateChatState(chatId);

        if (!state.botDiv) {
            this.createBotMessagePlaceholder(chatId);
        }

        if (state.botDiv.classList.contains('typing')) {
            state.botDiv.innerHTML = '';
            state.botDiv.classList.remove('typing');
        }

        const textNode = document.createTextNode(token);
        state.botDiv.appendChild(textNode);
        if (chatId === AppState.currentChatId) {
            this.scrollToBottom();
        }
    },

    finishGeneration(chatId, hasTokens = true) {
        if (!chatId) return;
        const state = this._chatDivs[chatId];
        if (!state) return;

        if (state.thinkDiv) {
            state.thinkDiv.open = false;
            const summary = state.thinkDiv.querySelector('.think-summary');
            if (summary) {
                const charCount = (state.thinkBody?.textContent || '').length;
                summary.innerHTML = `<span class="think-icon">💭</span> Düşünce <span class="think-char-count">(${charCount} karakter)</span>`;
            }
            state.thinkDiv = null;
            state.thinkBody = null;
        }
        if (state.botDiv && state.botDiv.classList.contains('typing')) {
            state.botDiv.innerHTML = hasTokens ? '' : '<em>[Yanıt gelmedi]</em>';
            state.botDiv.classList.remove('typing');
        }
        state.botDiv = null;

        // Update stop button if this is the active chat
        if (chatId === AppState.currentChatId) {
            this.setStopButtonVisible(false);
        }
    },

    setConnectionStatus(isConnected, errorMessage = null) {
        const statusText = document.getElementById('connection-status-text');
        const sendBtn = document.getElementById('send-btn');
        const messageInput = document.getElementById('message-input');
        const stopBtn = document.getElementById('stop-btn');

        if (isConnected) {
            this.statusIndicator.classList.add('connected');
            if (statusText) statusText.textContent = 'Bağlı';
            if (sendBtn) sendBtn.disabled = false;
            if (messageInput) messageInput.disabled = false;
            if (stopBtn) stopBtn.disabled = false;
            this._removeConnectionNotice();
        } else {
            this.statusIndicator.classList.remove('connected');
            const label = errorMessage === 'reconnecting'
                ? 'Bağlantı koptu, yeniden bağlanıyor...'
                : (errorMessage === 'connecting' ? 'Bağlanıyor...' : 'Bağlantı yok');
            if (statusText) statusText.textContent = label;
            if (sendBtn) sendBtn.disabled = true;
            if (messageInput) messageInput.disabled = true;
            if (stopBtn) stopBtn.disabled = true;
            this._showConnectionNotice(label);
        }
    },

    _showConnectionNotice(message) {
        const existing = this.chatArea.querySelector('[data-connection-notice="true"]');
        if (existing) {
            existing.textContent = message;
            return;
        }
        const div = document.createElement('div');
        div.className = 'message bot';
        div.setAttribute('data-connection-notice', 'true');
        div.style.opacity = '0.85';
        div.textContent = message;
        this.chatArea.appendChild(div);
        this.scrollToBottom();
    },

    _removeConnectionNotice() {
        const existing = this.chatArea.querySelector('[data-connection-notice="true"]');
        if (existing) existing.remove();
    },

    setStopButtonVisible(isVisible) {
        this.stopBtn.style.display = isVisible ? 'block' : 'none';
    },

    clearInput() {
        this.messageInput.value = '';
    },

    async saveSetting(key) {
        const input = document.getElementById('setting-input-' + key);
        if (!input) return;
        const val = input.value.trim();

        const resultBlock = document.getElementById('settings-result');
        resultBlock.style.color = "var(--text-color)";
        resultBlock.textContent = `${key} kaydediliyor...`;

        const data = await API.saveSettings(key, val);

        if (data.error) {
            resultBlock.style.color = "var(--danger-color)";
            resultBlock.textContent = `Hata: ${data.error}`;
        } else {
            resultBlock.style.color = "#22c55e";
            resultBlock.textContent = `${key} başarıyla kaydedildi!`;
            input.style.borderColor = "#22c55e";
            setTimeout(() => { input.style.borderColor = "var(--border-color)"; }, 2000);
        }
    },

    renderSettings(settings) {
        const dashboard = document.getElementById('settings-dashboard');
        if (!dashboard) return;

        if (!settings || settings.error) {
            dashboard.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--danger-color);">Ayarlar yüklenemedi.</div>';
            return;
        }

        const categories = {
            "Router Konfigürasyonu": ["router_api_key", "router_model_group"],
            "Yapay Zeka (AI) Ayarları": ["system_prompt", "llm_timeout_seconds", "embed_timeout_seconds", "chat_history_max_messages", "first_token_delay_ms", "token_delay_ms", "thinking_level", "temperature"],
            "Sistem Parametreleri": ["result_ttl_seconds", "sse_heartbeat_seconds", "worker_max_concurrency", "stop_key_ttl_seconds", "redis_cache_ttl_seconds"]
        };

        let html = '';
        const handledKeys = new Set();

        Object.entries(categories).forEach(([categoryName, keys]) => {
            let groupHtml = `<div style="background: var(--bg-color); border: 1px solid var(--border-color); border-radius: 8px; padding: 15px;">
                <h3 style="margin-top: 0; margin-bottom: 15px; font-size: 1.05rem; color: var(--primary-color); border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">${categoryName}</h3>
                <div style="display: flex; flex-direction: column; gap: 10px;">`;

            let hasKeys = false;
            keys.forEach(key => {
                if (settings[key] !== undefined) {
                    hasKeys = true;
                    handledKeys.add(key);
                    groupHtml += `
                    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.02); padding-bottom: 8px;">
                        <label style="flex: 1; font-weight: 500; font-size: 0.9rem; color: var(--text-color);">${key}</label>
                        <div style="flex: 2; display: flex; gap: 10px;">
                            <input type="text" id="setting-input-${key}" value="${settings[key]}" style="flex: 1; padding: 8px 12px; background: var(--secondary-color); border: 1px solid var(--border-color); color: white; border-radius: 6px; font-size: 0.9rem; outline: none; transition: border-color 0.2s;">
                            <button class="btn btn-primary" onclick="window.saveSetting('${key}')" style="padding: 8px 16px; font-size: 0.85rem;">Kaydet</button>
                        </div>
                    </div>`;
                }
            });
            groupHtml += `</div></div>`;
            if (hasKeys) html += groupHtml;
        });

        const unhandledKeys = Object.keys(settings).filter(k => !handledKeys.has(k)).sort();
        if (unhandledKeys.length > 0) {
            let groupHtml = `<div style="background: var(--bg-color); border: 1px solid var(--border-color); border-radius: 8px; padding: 15px;">
                <h3 style="margin-top: 0; margin-bottom: 15px; font-size: 1.05rem; color: #94a3b8; border-bottom: 1px solid var(--border-color); padding-bottom: 8px;">Diğer Ayarlar</h3>
                <div style="display: flex; flex-direction: column; gap: 10px;">`;
            unhandledKeys.forEach(key => {
                groupHtml += `
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.02); padding-bottom: 8px;">
                    <label style="flex: 1; font-weight: 500; font-size: 0.9rem; color: var(--text-color);">${key}</label>
                    <div style="flex: 2; display: flex; gap: 10px;">
                        <input type="text" id="setting-input-${key}" value="${settings[key]}" style="flex: 1; padding: 8px 12px; background: var(--secondary-color); border: 1px solid var(--border-color); color: white; border-radius: 6px; font-size: 0.9rem; outline: none; transition: border-color 0.2s;">
                        <button class="btn btn-primary" onclick="window.saveSetting('${key}')" style="padding: 8px 16px; font-size: 0.85rem;">Kaydet</button>
                    </div>
                </div>`;
            });
            groupHtml += `</div></div>`;
            html += groupHtml;
        }

        dashboard.innerHTML = html;
    },

    clearChatArea() {
        this.chatArea.innerHTML = '';
    },

    renderChatList(chats, currentChatId) {
        const listEl = document.getElementById('chat-list');
        if (!listEl) return;

        listEl.innerHTML = '';
        if (!chats || chats.length === 0) {
            listEl.innerHTML = '<div style="color: #94a3b8; font-size: 0.85rem; text-align: center; padding: 10px;">Henüz sohbet yok.</div>';
            return;
        }

        chats.forEach(chat => {
            const item = document.createElement('div');
            item.className = 'chat-item';
            item.dataset.chatId = chat.chat_id;
            if (chat.chat_id === currentChatId) item.classList.add('active');

            let dateStr = "";
            try {
                const d = new Date(chat.updated_at);
                dateStr = d.toLocaleDateString('tr-TR', { hour: '2-digit', minute: '2-digit' });
            } catch(e) {}

            const displayName = chat.name || `Sohbet ${chat.chat_id.substring(0, 8)}...`;

            item.innerHTML = `
                <div class="chat-item-body" style="flex:1;min-width:0;cursor:pointer;">
                    <div class="chat-item-name" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:0.9rem;">${displayName}</div>
                    <small style="color:#94a3b8;font-size:0.75rem;">${dateStr}</small>
                </div>
                <div class="chat-item-actions" style="display:flex;gap:4px;flex-shrink:0;opacity:0;transition:opacity 0.15s;">
                    <button class="chat-action-btn rename-btn" title="Yeniden Adlandır" style="background:none;border:none;cursor:pointer;color:#94a3b8;padding:2px 5px;font-size:0.8rem;border-radius:4px;">✏️</button>
                    <button class="chat-action-btn delete-btn" title="Sil" style="background:none;border:none;cursor:pointer;color:#ef4444;padding:2px 5px;font-size:0.8rem;border-radius:4px;">🗑️</button>
                </div>
            `;

            // Show/hide action buttons on hover
            item.addEventListener('mouseenter', () => {
                item.querySelector('.chat-item-actions').style.opacity = '1';
            });
            item.addEventListener('mouseleave', () => {
                item.querySelector('.chat-item-actions').style.opacity = '0';
            });

            // Click body → load chat
            item.querySelector('.chat-item-body').addEventListener('click', () => {
                if (window.loadChat) window.loadChat(chat.chat_id);
            });

            // Rename button
            item.querySelector('.rename-btn').addEventListener('click', async (e) => {
                e.stopPropagation();
                const nameEl = item.querySelector('.chat-item-name');
                const currentName = nameEl.textContent;
                const newName = prompt('Yeni sohbet adı:', currentName);
                if (!newName || newName.trim() === currentName) return;
                const result = await API.renameChat(chat.chat_id, newName.trim());
                if (result && !result.error) {
                    nameEl.textContent = newName.trim();
                    chat.name = newName.trim();
                } else {
                    alert('Hata: ' + (result?.error || 'Bilinmeyen hata'));
                }
            });

            // Delete button
            item.querySelector('.delete-btn').addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm(`"${displayName}" sohbetini silmek istediğinize emin misiniz?`)) return;
                const result = await API.deleteChat(chat.chat_id);
                if (result && !result.error) {
                    item.remove();
                    if (chat.chat_id === AppState.currentChatId) {
                        AppState.currentChatId = null;
                        UI.clearChatArea();
                        UI.chatArea.innerHTML = '<div class="message bot">Sohbet silindi. Yeni bir sohbet başlatın.</div>';
                    }
                } else {
                    alert('Hata: ' + (result?.error || 'Bilinmeyen hata'));
                }
            });

            listEl.appendChild(item);
        });
    }
};

window.saveSetting = (key) => UI.saveSetting(key);
