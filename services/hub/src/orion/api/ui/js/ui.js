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
            this._chatDivs[chatId] = {
                botDiv: null,
                thinkDiv: null,
                thinkBody: null,
                contentNodes: [],
                currentContentNode: null
            };
        }
        return this._chatDivs[chatId];
    },
    scrollToBottom() {
        this.chatArea.scrollTop = this.chatArea.scrollHeight;
        requestAnimationFrame(() => {
            if (this.chatArea) this.chatArea.scrollTop = this.chatArea.scrollHeight;
        });
    },
    createMetricsElement(firstTokenMs, totalMs) {
        if (!totalMs && !firstTokenMs) return null;
        const meta = document.createElement('div');
        meta.className = 'message-meta';
        const fmtFirst = firstTokenMs == null ? '—' : `${(Number(firstTokenMs) / 1000).toFixed(2)} ${OrionI18n.t('secondsShort')}`;
        const fmtTotal = (Number(totalMs || 0) / 1000).toFixed(2);
        meta.innerHTML = `
            <span class="meta-item" title="${OrionI18n.t('firstTokenTime')}">
                <span class="meta-icon" aria-hidden="true">⚡</span> <strong>${fmtFirst}</strong>
            </span>
            <span class="meta-item" title="${OrionI18n.t('completionTimeHint')}">
                <span class="meta-icon" aria-hidden="true">◷</span> <strong>${fmtTotal} ${OrionI18n.t('secondsShort')}</strong>
            </span>
        `;
        return meta;
    },
    addMessageCopy(div, readText) {
        if (div.querySelector('.message-copy')) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'message-copy';
        button.innerHTML = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1" aria-hidden="true"><rect x="4.5" y="4.5" width="6" height="6"/><path d="M8.5 4.5V1.5H1.5V8.5H4.5"/></svg>';
        button.title = OrionI18n.t('copyMessage');
        button.setAttribute('aria-label', OrionI18n.t('copyMessage'));
        button.onclick = async () => {
            try {
                await navigator.clipboard.writeText(readText());
                this.showToast(OrionI18n.t('messageCopied'), 'copy');
            } catch (_) { button.title = 'Kopyalanamadı. Metni seçerek kopyalayabilirsin.'; }
        };
        let meta = div.querySelector('.message-meta');
        if (!meta) { meta = document.createElement('div'); meta.className = 'message-meta'; div.appendChild(meta); }
        meta.appendChild(button);
    },
    createAttachmentCard(item) {
        const file = typeof item === 'string' ? { data: item, name: OrionI18n.t('file') } : item;
        const card = document.createElement('button');
        card.type = 'button';
        card.className = 'media-square-item';
        card.title = file.name || OrionI18n.t('file');
        card.setAttribute('aria-label', `${card.title} — ${OrionI18n.t('preview')}`);
        card.setAttribute('draggable', 'false');
        card.ondragstart = (e) => e.preventDefault();
        if (file.loading) {
            card.textContent = `${file.name} — ${OrionI18n.t('preparing')}`;
            card.disabled = true;
            return card;
        }
        const video = !file.isText && ((file.mime_type || '').startsWith('video/') || (file.data || '').startsWith('data:video/'));
        const audio = !file.isText && ((file.mime_type || '').startsWith('audio/') || (file.data || '').startsWith('data:audio/'));
        if (!file.isText && !audio && !file.media_path) {
            const preview = document.createElement(video ? 'video' : 'img');
            preview.src = file.data;
            preview.setAttribute('draggable', 'false');
            preview.ondragstart = (e) => e.preventDefault();
            if (video) {
                preview.muted = true;
                preview.playsInline = true;
                preview.preload = 'metadata';
                preview.onloadedmetadata = () => { preview.currentTime = Math.min(0.01, preview.duration || 0); };
            } else preview.alt = file.name || OrionI18n.t('image');
            card.appendChild(preview);
        } else {
            const icon = document.createElement('span');
            icon.className = 'attachment-icon';
            icon.textContent = audio ? '♫' : video ? '▶' : file.media_path ? '▧' : '📄';
            card.appendChild(icon);
        }
        if (video || audio) {
            const play = document.createElement('span');
            play.className = 'attachment-play';
            play.textContent = '▶';
            card.appendChild(play);
        }
        if (file.isText || audio || file.media_path) {
            const label = document.createElement('span');
            label.className = 'attachment-name';
            label.textContent = file.name || OrionI18n.t('file');
            card.appendChild(label);
        }
        if (video) card.className += ' attachment-video';
        card.onclick = () => this.openAttachment(file);
        return card;
    },
    openAttachment(file) {
        const modal = document.createElement('dialog');
        modal.className = 'attachment-dialog';
        const title = document.createElement('div');
        title.className = 'attachment-title';
        title.textContent = file.name || OrionI18n.t('file');
        const mime = file.mime_type || (file.data || '').split(';')[0].slice(5);
        const kind = file.isText ? 'pre' : mime.startsWith('video/') ? 'video' : mime.startsWith('audio/') ? 'audio' : 'img';
        const content = document.createElement(kind);
        content.setAttribute('draggable', 'false');
        content.ondragstart = (e) => e.preventDefault();
        if (file.isText) content.textContent = file.data;
        else if (!file.media_path) content.src = file.data;
        if (kind === 'video' || kind === 'audio') { content.controls = true; content.autoplay = true; }
        if (kind === 'img') content.alt = file.name || OrionI18n.t('image');
        if (file.isText) modal.className += ' attachment-text-dialog';
        modal.append(content, title);
        modal.setAttribute('aria-label', file.name || OrionI18n.t('file'));
        modal.onclick = e => { if (e.target === modal) modal.close(); };
        const request = new AbortController();
        let objectUrl;
        modal.onclose = () => { request.abort(); if (content.pause) content.pause(); if (objectUrl) URL.revokeObjectURL(objectUrl); modal.remove(); };
        document.body.appendChild(modal);
        modal.showModal();
        if (file.media_path) {
            title.textContent += ' — Yükleniyor…';
            API.mediaBlob(file.media_path, request.signal).then(blob => {
                if (request.signal.aborted) return;
                objectUrl = URL.createObjectURL(blob);
                content.src = objectUrl;
                title.textContent = file.name || OrionI18n.t('file');
            }).catch(error => { if (!request.signal.aborted) title.textContent = error.message; });
        }
    },
    appendUserMessage(text, images = []) {
        // Restore attachments from messages saved by the old client.
        const files = [...images];
        text = (text || '').replace(/\n*\[Ek Dosya: ([^\n]+)\]\r?\n```\r?\n([\s\S]*?)\r?\n```/g, (_, name, data) => {
            if (!files.some(f => f.isText && f.name === name && f.data === data)) files.push({ name, data, isText: true });
            return '';
        }).trim();
        const hero = this.chatArea.querySelector('.welcome-hero');
        if (hero) hero.remove();
        const chatId = AppState.currentChatId;
        const curState = chatId ? this._chatDivs[chatId] : null;
        if (curState?.botDiv && curState.botDiv.classList.contains('typing')) {
            curState.botDiv.remove();
            curState.botDiv = null;
        }
        const group = document.createElement('div');
        group.className = 'user-message-group';
        if (files.length) {
            const row = document.createElement('div');
            row.className = 'user-media-container';
            files.forEach(file => row.appendChild(this.createAttachmentCard(file)));
            group.appendChild(row);
        }
        if (text) {
            const div = document.createElement('div');
            div.className = 'message user';
            div.style.marginBottom = '0';
            div.textContent = text;
            group.appendChild(div);
        }
        this.chatArea.appendChild(group);
        this.scrollToBottom();
    },
    appendStaticBotMessage(content, thinking = null, metrics = null, audio = null, toolActivity = [], displayParts = null) {
        const hero = this.chatArea.querySelector('.welcome-hero');
        if (hero) hero.remove();
        const div = document.createElement('div');
        div.className = 'message bot';
        if (Array.isArray(displayParts) && displayParts.length) {
            for (const part of displayParts) {
                if (part.type === 'thinking') {
                    const block = this._createThinkingBlock(false);
                    block.body.textContent = part.content || '';
                    this._closeThinkingBlock(block.details, block.body);
                    div.appendChild(block.details);
                } else if (part.type === 'content') {
                    div.appendChild(MarkdownView.create(part.content || ''));
                } else if (part.type === 'tool') {
                    const item = toolActivity.find(entry => entry.call_id === part.call_id);
                    if (item) window.ToolsUI?.renderActivity(div, item);
                }
            }
        } else if (thinking) {
            const thinkDiv = document.createElement('details');
            thinkDiv.className = 'think-block';
            thinkDiv.open = false;
            const summary = document.createElement('summary');
            summary.className = 'think-summary';
            const charCount = thinking.length;
            summary.innerHTML = `<span class="think-icon">💭</span> ${OrionI18n.t('thinking')} <span class="think-char-count">(${charCount} ${OrionI18n.t('chars')})</span>`;
            thinkDiv.appendChild(summary);
            const thinkBody = document.createElement('pre');
            thinkBody.className = 'think-body';
            thinkBody.textContent = thinking;
            thinkDiv.appendChild(thinkBody);
            div.appendChild(thinkDiv);
        }
        if (!Array.isArray(displayParts) || !displayParts.length) {
            for (const item of toolActivity) window.ToolsUI?.renderActivity(div, item);
            div.appendChild(MarkdownView.create(content || ''));
        }
        if (metrics && (metrics.total_ms || metrics.first_token_ms)) {
            const meta = this.createMetricsElement(metrics.first_token_ms, metrics.total_ms);
            if (meta) div.appendChild(meta);
        }
        this.addMessageCopy(div, () => content || '');
        if (audio?.audio || audio?.media_path || audio?.audio_path) this.appendAudio(AppState.currentChatId, audio, false, div);
        this.chatArea.appendChild(div);
        this.scrollToBottom();
        return div;
    },
    createBotMessagePlaceholder(chatId, isWaiting = true) {
        if (!chatId) return;
        const hero = this.chatArea.querySelector('.welcome-hero');
        if (hero) hero.remove();
        const state = this._getOrCreateChatState(chatId);
        // Ensure botDiv is never positioned BEFORE the latest user message
        const lastUserMsg = this.chatArea.querySelector('.user-message-group:last-of-type, .message.user:last-of-type');
        if (state.botDiv && lastUserMsg) {
            if (state.botDiv.compareDocumentPosition(lastUserMsg) & Node.DOCUMENT_POSITION_FOLLOWING) {
                if (state.botDiv.classList.contains('typing')) {
                    state.botDiv.remove();
                }
                state.botDiv = null;
            }
        }
        // If there's an existing typing placeholder for this chat that is correctly positioned, reuse it
        if (state.botDiv && state.botDiv.classList.contains('typing')) {
            return;
        }
        state.thinkDiv = null;
        state.thinkBody = null;
        state.contentNodes = [];
        state.currentContentNode = null;
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
    _createThinkingBlock(open = true) {
        const details = document.createElement('details');
        details.className = 'think-block';
        details.open = open;
        const summary = document.createElement('summary');
        summary.className = 'think-summary';
        summary.textContent = `💭 ${OrionI18n.t('thinkingProgress')}`;
        const body = document.createElement('pre');
        body.className = 'think-body';
        details.append(summary, body);
        return { details, body };
    },
    _closeThinkingBlock(details, body) {
        if (!details) return;
        details.open = false;
        const summary = details.querySelector('.think-summary');
        if (summary) summary.textContent = `💭 ${OrionI18n.t('thinking')} (${(body?.textContent || '').length} ${OrionI18n.t('chars')})`;
    },
    finishThinking(chatId) {
        const state = this._chatDivs[chatId];
        if (!state?.thinkDiv) return;
        this._closeThinkingBlock(state.thinkDiv, state.thinkBody);
        state.thinkDiv = null;
        state.thinkBody = null;
    },
    _ensureValidBotDiv(chatId) {
        const state = this._getOrCreateChatState(chatId);
        const lastUserMsg = this.chatArea?.querySelector('.user-message-group:last-of-type, .message.user:last-of-type');
        if (state.botDiv && lastUserMsg && (state.botDiv.compareDocumentPosition(lastUserMsg) & Node.DOCUMENT_POSITION_FOLLOWING)) {
            if (state.botDiv.classList.contains('typing')) {
                state.botDiv.remove();
            }
            state.botDiv = null;
            state.contentNodes = [];
            state.currentContentNode = null;
        }
        if (!state.botDiv) {
            this.createBotMessagePlaceholder(chatId);
        }
        return state;
    },
    appendThinkingToken(chatId, token) {
        if (!chatId || !token) return;
        const state = this._ensureValidBotDiv(chatId);
        state.currentContentNode = null;
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
            summary.innerHTML = `<span class="think-icon">💭</span> ${OrionI18n.t('thinkingActive')}<span class="think-dots">...</span>`;
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
        if (!chatId || !token) return;
        this.finishThinking(chatId);
        const state = this._ensureValidBotDiv(chatId);
        if (state.botDiv.classList.contains('typing')) {
            state.botDiv.innerHTML = '';
            state.botDiv.classList.remove('typing');
        }
        if (!state.currentContentNode) {
            state.currentContentNode = MarkdownView.create();
            state.contentNodes ||= [];
            state.contentNodes.push(state.currentContentNode);
            state.botDiv.appendChild(state.currentContentNode);
        }
        MarkdownView.update(state.currentContentNode, state.currentContentNode.markdownSource + token);
        if (chatId === AppState.currentChatId) {
            this.scrollToBottom();
        }
    },
    replaceMessageContent(chatId, content) {
        if (!chatId) return;
        this.finishThinking(chatId);
        const state = this._ensureValidBotDiv(chatId);
        if (state.botDiv.classList.contains('typing')) {
            state.botDiv.innerHTML = '';
            state.botDiv.classList.remove('typing');
            state.thinkDiv = null;
            state.thinkBody = null;
        }
        const nodes = state.contentNodes ||= [];
        const earlierLength = nodes.reduce((sum, node) => sum + node.markdownSource.length, 0) -
            (state.currentContentNode?.markdownSource.length || 0);
        if (!state.currentContentNode) {
            state.currentContentNode = MarkdownView.create();
            nodes.push(state.currentContentNode);
            const trailingElement = state.botDiv.querySelector('.audio-player-container, .message-meta');
            state.botDiv.insertBefore(state.currentContentNode, trailingElement || null);
        }
        MarkdownView.update(state.currentContentNode, (content || '').slice(earlierLength));
        if (chatId === AppState.currentChatId) {
            this.scrollToBottom();
        }
    },
    appendAudio(chatId, audioData, autoPlay = true, targetDiv = null) {
        if (!chatId || !audioData || !(audioData.audio || audioData.media_path || audioData.audio_path)) return;
        const audioStarted = performance.now();
        const state = this._getOrCreateChatState(chatId);
        const botDiv = targetDiv || state.botDiv;
        if (!botDiv) return;
        if (!audioData.audio) {
            if (botDiv.querySelector('.audio-player-container')) return;
            const box = document.createElement('div'); box.className = 'audio-player-container';
            const button = document.createElement('button'); button.type = 'button'; button.textContent = '▶ Ses';
            const request = new AbortController();
            const record = {request, url: null, box};
            (this._mediaPlayers ||= new Set()).add(record);
            button.onclick = async () => {
                button.disabled = true; button.textContent = 'Yükleniyor…';
                try {
                    let blob;
                    if (audioData.media_path) blob = await API.mediaBlob(audioData.media_path, request.signal);
                    else {
                        const response = await API._fetch(audioData.audio_path, {signal: request.signal});
                        if (!response.ok) throw new Error('Ses yüklenemedi.');
                        const data = await response.json();
                        const bytes = Uint8Array.from(atob(data.audio), c => c.charCodeAt(0));
                        blob = new Blob([bytes], {type: data.format === 'mp3' ? 'audio/mpeg' : 'audio/' + data.format});
                    }
                    if (request.signal.aborted) return;
                    record.url = URL.createObjectURL(blob);
                    const player = document.createElement('audio'); player.controls = true; player.src = record.url;
                    player.addEventListener('play', () => window.PcmSpeech?.stop());
                    box.replaceChildren(player);
                    player.play().catch(() => {});
                } catch (error) {
                    if (!request.signal.aborted) { button.disabled = false; button.textContent = 'Ses yüklenemedi · Tekrar dene'; }
                }
            };
            box.appendChild(button); botDiv.appendChild(box);
            return;
        }
        if (window.PcmSpeech?.recording) {
            window.PcmSpeech.recording(chatId, audioData, autoPlay && chatId === AppState.currentChatId, botDiv);
            return;
        }
        // Tekrar aynı mesaja ikinci oynatıcı eklenmesini önle
        if (botDiv.querySelector('.audio-player-container')) return;
        const audioContainer = document.createElement('div');
        audioContainer.className = 'audio-player-container';
        const audioElement = document.createElement('audio');
        audioElement.addEventListener('play', () => window.PcmSpeech?.stop());
        audioElement.controls = true;
        audioElement.autoplay = autoPlay && chatId === AppState.currentChatId;
        audioElement.setAttribute('aria-label', OrionI18n.t('playVoiceResponse'));
        const fmt = audioData.format || 'wav';
        const mime = fmt === 'mp3' ? 'audio/mpeg' : `audio/${fmt}`;
        audioElement.src = `data:${mime};base64,${audioData.audio}`;
        audioElement.addEventListener('playing', () => {
            console.debug(`Orion audio format=${fmt} start_ms=${Math.round(performance.now() - audioStarted)}`);
        }, {once: true});
        audioContainer.appendChild(audioElement);
        if (audioData.arrival_ms != null) {
            const timing = document.createElement('div');
            timing.className = 'audio-arrival-time';
            timing.textContent = `♫ ${(audioData.arrival_ms / 1000).toFixed(2)} ${OrionI18n.t('secondsShort')}`;
            timing.title = OrionI18n.t('voiceArrivalHint');
            audioContainer.appendChild(timing);
        }
        botDiv.appendChild(audioContainer);
        if (chatId === AppState.currentChatId) {
            this.scrollToBottom();
            if (autoPlay) {
                // Tarayıcı autoplay politikası gereği promise yakalama
                const playPromise = audioElement.play();
                if (playPromise !== undefined) {
                    playPromise.catch(err => {
                        console.warn("Autoplay tarayıcı kullanıcı etkileşimi kuralı nedeniyle engellendi:", err);
                    });
                }
            }
        }
    },
    showTextMetrics(chatId, metrics) {
        const botDiv = this._chatDivs[chatId]?.botDiv;
        if (!botDiv) return;
        const meta = this.createMetricsElement(metrics.first_token_ms, metrics.total_ms);
        if (!meta) return;
        const previous = botDiv.querySelector('.message-meta');
        if (previous) {
            const copy = previous.querySelector(".message-copy");
            if (copy) meta.appendChild(copy);
            previous.replaceWith(meta);
        }
        else botDiv.appendChild(meta);
    },
    finishGeneration(chatId, hasTokens = true, metrics = null) {
        if (!chatId) return;
        const fallback = AppState.getGenerationMetrics(chatId);
        metrics = { ...metrics };
        for (const key of ['first_token_ms', 'total_ms']) {
            if (metrics[key] === undefined) metrics[key] = fallback?.[key];
        }
        const state = this._chatDivs[chatId];
        const staleTypingDivs = this.chatArea.querySelectorAll(
            `.message.typing[data-chat-id="${CSS.escape(String(chatId))}"]`
        );
        staleTypingDivs.forEach(div => {
            if (!state || div !== state.botDiv) div.remove();
        });
        if (!state) {
            if (chatId === AppState.currentChatId) this.setStopButtonVisible(false);
            return;
        }
        const botDivRef = state.botDiv;
        for (const node of state.contentNodes || []) MarkdownView.render(node);
        if (state.thinkDiv) {
            state.thinkDiv.open = false;
            const summary = state.thinkDiv.querySelector('.think-summary');
            if (summary) {
                const charCount = (state.thinkBody?.textContent || '').length;
                summary.innerHTML = `<span class="think-icon">💭</span> ${OrionI18n.t('thinking')} <span class="think-char-count">(${charCount} ${OrionI18n.t('chars')})</span>`;
            }
            state.thinkDiv = null;
            state.thinkBody = null;
        }
        if (botDivRef && botDivRef.classList.contains('typing')) {
            botDivRef.innerHTML = hasTokens ? '' : `<em>[${OrionI18n.t('noResponse')}]</em>`;
            botDivRef.classList.remove('typing');
        }
        // Add metrics badge from Router to the bot message
        if (botDivRef && (metrics.total_ms || metrics.first_token_ms)) {
            const metaEl = this.createMetricsElement(metrics.first_token_ms, metrics.total_ms);
            if (metaEl && !botDivRef.querySelector('.message-meta')) {
                botDivRef.appendChild(metaEl);
            }
        }
        if (botDivRef) {
            const rawText = (state.contentNodes || []).map(node => node.markdownSource || '').join('\n');
            this.addMessageCopy(botDivRef, () => rawText);
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
            if (statusText) statusText.textContent = OrionI18n.t('statusConnected');
            if (sendBtn) sendBtn.disabled = false;
            if (messageInput) messageInput.disabled = false;
            if (stopBtn) stopBtn.disabled = false;
            this._removeConnectionNotice();
        } else {
            this.statusIndicator.classList.remove('connected');
            const label = errorMessage === 'reconnecting'
                ? OrionI18n.t('statusReconnecting')
                : (errorMessage === 'connecting' ? OrionI18n.t('statusConnecting') : OrionI18n.t('statusDisconnected'));
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
        isVisible = AppState.isGenerating();
        if (!this.stopBtn) return;
        this.stopBtn.style.display = isVisible ? 'flex' : 'none';
        if (isVisible && AppState.isStopping(AppState.currentChatId)) {
            this.stopBtn.disabled = true;
            this.stopBtn.setAttribute('aria-label', OrionI18n.t('stopping'));
            this.stopBtn.title = OrionI18n.t('stopping');
            return;
        }
        if (isVisible) {
            this.stopBtn.disabled = false;
            this.stopBtn.setAttribute('aria-label', OrionI18n.t('stop'));
            this.stopBtn.title = OrionI18n.t('stop');
        }
    },
    resizeMessageInput(followTail = false) {
        const input = this.messageInput;
        if (!input) return;
        const styles = getComputedStyle(input);
        const minHeight = parseFloat(styles.minHeight) || 38;
        const maxHeight = parseFloat(styles.maxHeight) || 200;
        input.style.height = 'auto';
        const contentHeight = input.scrollHeight;
        input.style.height = `${Math.min(maxHeight, Math.max(minHeight, contentHeight))}px`;
        input.style.overflowY = contentHeight > maxHeight ? 'auto' : 'hidden';
        if (followTail) input.scrollTop = input.scrollHeight;
    },
    clearInput() {
        this.messageInput.value = '';
        this.resizeMessageInput();
    },
    showToast(message = OrionI18n.t('saved'), kind = '') {
        if (!this._toast) {
            this._toast = document.createElement('div');
            this._toast.className = 'app-toast';
            this._toast.setAttribute('role', 'status');
            this._toast.setAttribute('aria-live', 'polite');
            this._toast.setAttribute('popover', 'manual');
            document.body.appendChild(this._toast);
        }
        const toast = this._toast;
        toast.className = kind === 'copy' ? 'app-toast copy-toast' : 'app-toast';
        clearTimeout(this._toastTimer);
        if (toast.hidePopover) toast.hidePopover();
        toast.textContent = message;
        toast.hidden = false;
        if (toast.showPopover) toast.showPopover();
        this._toastTimer = setTimeout(() => {
            if (toast.hidePopover) toast.hidePopover();
            toast.hidden = true;
        }, 1600);
    },
    _settingTimers: {},
    _settingSaveQueue: Promise.resolve(),
    settingSaveStatus(key, message, failed = false) {
        const status = document.getElementById(`setting-status-${key}`);
        if (status) {
            status.textContent = message;
            status.classList.toggle('error', failed);
        }
        const retry = document.getElementById(`setting-retry-${key}`);
        if (retry) retry.hidden = !failed;
    },
    handleSettingChange(key) {
        const input = document.getElementById(`setting-input-${key}`);
        if (!input) return;
        clearTimeout(this._settingTimers[key]);
        const dirty = input.value.trim() !== (input.dataset.original || '').trim();
        input.classList.toggle('dirty', dirty || input.dataset.saving === 'true');
        if (!dirty && input.dataset.saving !== 'true') {
            this.settingSaveStatus(key, '');
            return;
        }
        if (input.validity && !input.validity.valid) {
            this.settingSaveStatus(key, input.validationMessage);
            return;
        }
        this.settingSaveStatus(key, '');
        if (key === 'tts_voice') {
            const model = document.getElementById('setting-input-tts_model')?.value || this.currentSettings?.tts_model;
            if (model) this.rememberVoice(model, input.value);
        }
        if (input.tagName === 'SELECT') {
            this.saveSetting(key);
        } else {
            this._settingTimers[key] = setTimeout(() => this.saveSetting(key), 650);
        }
    },
    getRememberedVoices() {
        let fromSettings = {};
        if (this.currentSettings?.tts_model_voices) {
            let s = this.currentSettings.tts_model_voices;
            if (typeof s === 'string') {
                try { s = JSON.parse(s); } catch {}
            }
            if (s && typeof s === 'object') fromSettings = s;
        }
        return fromSettings;
    },
    getRememberedVoice(model) {
        if (!model) return '';
        const map = this.getRememberedVoices();
        if (map && map[model]) {
            return String(map[model]).split(' — ')[0].trim();
        }
        if (this.currentSettings?.tts_model === model && this.currentSettings?.tts_voice) {
            return String(this.currentSettings.tts_voice).split(' — ')[0].trim();
        }
        return '';
    },
    rememberVoice(model, voice, persistRemote = true) {
        if (!model) return;
        const map = this.getRememberedVoices();
        const raw = voice !== undefined && voice !== null ? String(voice).trim() : '';
        const cleanVoice = raw.split(' — ')[0].trim();
        map[model] = cleanVoice;
        if (this.currentSettings) {
            this.currentSettings.tts_model_voices = map;
            if (this.currentSettings.tts_model === model) {
                this.currentSettings.tts_voice = cleanVoice;
            }
        }
        
        if (persistRemote && typeof API !== 'undefined' && API.saveSettings) {
            API.saveSettings('tts_model_voices', JSON.stringify(map)).catch(err => {
                console.warn('Could not sync tts_model_voices to backend:', err);
            });
        }
    },
    saveSetting(key) {
        clearTimeout(this._settingTimers[key]);
        // Serialize writes, including model/voice changes, so the latest choice wins.
        this._settingSaveQueue = this._settingSaveQueue.catch(() => {}).then(async () => {
            const input = document.getElementById(`setting-input-${key}`);
            if (!input || (input.validity && !input.validity.valid)) return;
            const val = input.value.trim();
            if (val === (input.dataset.original || '').trim()) return;
            input.dataset.saving = 'true';
            input.classList.add('dirty');
            this.settingSaveStatus(key, OrionI18n.t('saving'));
            try {
                const data = await API.saveSettings(key, val);
                if (data.error) throw new Error(typeof data.error === 'string' ? data.error : OrionI18n.t('invalidValue'));
                if (key === 'router_api_key') {
                    const masked = data[key] || '';
                    input.dataset.original = val;
                    input.placeholder = masked ? OrionI18n.t('configuredRouterApiKey', masked) : OrionI18n.t('placeholderRouterApiKey');
                    const clearBtn = document.getElementById(`setting-secret-clear-${key}`);
                    if (clearBtn) clearBtn.hidden = !Boolean(masked);
                    if (this.currentSettings) this.currentSettings[key] = masked;
                    const changed = input.value.trim() !== val;
                    input.classList.toggle('dirty', changed);
                    this.settingSaveStatus(key, '');
                    if (!changed) this.showToast(OrionI18n.t('saved'));
                    if (!document.getElementById('settings-view')?.classList.contains('active')) {
                        this.clearSavedRouterApiKeyInput();
                    }
                } else {
                    input.dataset.original = val;
                    if (this.currentSettings) this.currentSettings[key] = data[key] ?? val;
                    if (key === 'tts_voice') {
                        const model = document.getElementById('setting-input-tts_model')?.value || this.currentSettings?.tts_model;
                        if (model) this.rememberVoice(model, val);
                    }
                    const changed = input.value.trim() !== val;
                    input.classList.toggle('dirty', changed);
                    this.settingSaveStatus(key, '');
                    if (!changed) this.showToast(OrionI18n.t('saved'));
                }
                if (key === 'tts_enabled') {
                    const enabled = val.toLowerCase() === 'true';
                    localStorage.setItem('orion_tts_enabled', String(enabled));
                }
            } catch (error) {
                input.classList.add('dirty');
                this.settingSaveStatus(key, OrionI18n.t('saveFailed', error.message), true);
            } finally {
                delete input.dataset.saving;
            }
        });
        return this._settingSaveQueue;
    },
    toggleAdvancedSettings() {
        const panel = document.getElementById('settings-panel-advanced');
        const button = document.getElementById('settings-advanced-toggle');
        if (!panel || !button) return;
        panel.hidden = !panel.hidden;
        button.setAttribute('aria-expanded', String(!panel.hidden));
    },
    toggleSecretVisibility(key) {
        const input = document.getElementById(`setting-input-${key}`);
        if (!input) return;
        input.type = input.type === 'password' ? 'text' : 'password';
        const button = document.getElementById(`setting-secret-toggle-${key}`);
        if (button) button.setAttribute('aria-pressed', String(input.type === 'text'));
    },
    clearSavedRouterApiKeyInput() {
        window.RouterProviders?.clearInput();
        const input = document.getElementById('setting-input-router_api_key');
        if (!input || input.classList.contains('dirty')) return;
        input.value = '';
        input.dataset.original = '';
        input.type = 'password';
        const button = document.getElementById('setting-secret-toggle-router_api_key');
        if (button) button.setAttribute('aria-pressed', 'false');
    },
    async clearRouterApiKey() {
        const input = document.getElementById('setting-input-router_api_key');
        if (input) {
            input.value = '';
            input.dataset.original = '';
            input.classList.remove('dirty');
        }
        this.settingSaveStatus('router_api_key', OrionI18n.t('saving'));
        try {
            await API.saveSettings('router_api_key', '');
            if (this.currentSettings) this.currentSettings.router_api_key = '';
            if (input) {
                input.placeholder = OrionI18n.t('placeholderRouterApiKey');
            }
            const clearBtn = document.getElementById('setting-secret-clear-router_api_key');
            if (clearBtn) clearBtn.hidden = true;
            this.settingSaveStatus('router_api_key', '');
            this.showToast(OrionI18n.t('saved'));
        } catch (err) {
            this.settingSaveStatus('router_api_key', OrionI18n.t('saveFailed', err.message), true);
        }
    },
    renderSettings(settings) {
        this.currentSettings = settings;
        if (settings?.tts_model && settings?.tts_voice) {
            const cleanVoice = String(settings.tts_voice).split(' — ')[0].trim();
            const remembered = this.getRememberedVoices();
            if (cleanVoice && !remembered[settings.tts_model]) {
                this.rememberVoice(settings.tts_model, cleanVoice, false);
            }
        }
        const dashboard = document.getElementById('settings-dashboard');
        if (!dashboard) return;
        if (!settings || settings.error) {
            dashboard.innerHTML = `<div style="text-align: center; padding: 40px; color: var(--danger-color);">${OrionI18n.t('settingsFailed')}</div>`;
            return;
        }
        // Eğer zaten render edilmişse sadece değişmemiş olan değerleri güncelle
        const alreadyRendered = Boolean(dashboard.querySelector('[id^="setting-input-"]'));
        if (alreadyRendered) {
            window.RouterProviders?.mount();
            const oldTtsModel = document.getElementById('setting-input-tts_model')?.value;
            Object.keys(settings).forEach(key => {
                if (key === 'tts_voice' || key === 'stt_language') return;
                const el = document.getElementById(`setting-input-${key}`);
                if (el && document.activeElement !== el && !el.classList?.contains('dirty')) {
                    const serverVal = settings[key] !== undefined ? String(settings[key] ?? '') : '';
                    if (key === 'router_api_key') {
                        const hasKey = Boolean(serverVal && serverVal.trim());
                        el.placeholder = hasKey ? OrionI18n.t('configuredRouterApiKey', serverVal) : OrionI18n.t('placeholderRouterApiKey');
                        const clearBtn = document.getElementById(`setting-secret-clear-${key}`);
                        if (clearBtn) clearBtn.hidden = !hasKey;
                    } else if (key === 'tts_enabled' || key === 'ai_chat_titles_enabled' || key === 'stt_enabled' || key === 'google_ai_direct_stream') {
                        el.value = String(settings[key] ?? '').toLowerCase() === 'true' ? 'true' : 'false';
                        el.dataset.original = el.value;
                    } else {
                        if (el.tagName === 'SELECT' && !Array.from(el.options || []).some(option => option.value === serverVal)) {
                            el.add(new Option(serverVal, serverVal));
                        }
                        el.value = serverVal;
                        el.dataset.original = serverVal;
                    }
                }
            });
            const newTtsModel = document.getElementById('setting-input-tts_model')?.value;
            if (oldTtsModel && newTtsModel && oldTtsModel !== newTtsModel) {
                const ttsModelEl = document.getElementById('setting-input-tts_model');
                if (ttsModelEl) ttsModelEl.dataset.lastModel = newTtsModel;
            }
            const voiceSelect = document.getElementById('setting-input-tts_voice');
            if (voiceSelect && !voiceSelect.classList?.contains('dirty') && settings.tts_voice !== undefined) {
                if (!voiceSelect.dataset) voiceSelect.dataset = {};
                voiceSelect.dataset.original = String(settings.tts_voice ?? '');
            }
            this.loadVoiceChoices(false);
            this.loadSttLanguageChoices();
            return;
        }
        const categoryMeta = {
            [OrionI18n.t('settingsChat')]: {
                section: "general", icon: "💬", keys: ["router_model_group", "chat_title_model", "ai_chat_titles_enabled", "router_api_key"],
                desc: OrionI18n.t('settingsChatHint')
            },
            [OrionI18n.t('settingsVoice')]: {
                section: "general", icon: "🎙️", keys: ["tts_enabled", "tts_model", "tts_voice", "stt_enabled", "stt_model", "stt_language"],
                desc: OrionI18n.t('settingsVoiceHint')
            },
            [OrionI18n.t('settingsBehavior')]: {
                section: "advanced", icon: "🧠",
                keys: ["temperature", "thinking_level", "system_prompt", "chat_history_max_messages", "max_tool_rounds", "max_tool_calls"],
                desc: OrionI18n.t('settingsBehaviorHint')
            },
            [OrionI18n.t('settingsTimeout')]: {
                section: "advanced", icon: "⚡",
                keys: ["llm_timeout_seconds", "embed_timeout_seconds", "tts_timeout_seconds"],
                desc: OrionI18n.t('settingsTimeoutHint')
            },
            [OrionI18n.t('settingsSystem')]: {
                section: "advanced", icon: "⚙️",
                keys: ["first_token_delay_ms", "token_delay_ms", "result_ttl_seconds", "sse_heartbeat_seconds", "worker_max_concurrency", "stop_key_ttl_seconds", "redis_cache_ttl_seconds"],
                desc: OrionI18n.t('settingsSystemHint')
            }
        };
        const keyPlaceholders = {
            "router_api_key": OrionI18n.t('placeholderRouterApiKey'),
            "system_prompt": OrionI18n.t('placeholderSystemPrompt'),
            "tts_voice": OrionI18n.t('placeholderTtsVoice'),
            "tts_model": OrionI18n.t('placeholderTtsModel'),
            "thinking_level": OrionI18n.t('placeholderThinkingLevel'),
            "router_model_group": OrionI18n.t('placeholderRouterModelGroup'),
            "max_tool_rounds": "32",
            "max_tool_calls": "64"
        };
        const escapeSetting = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        const sections = {general: '', advanced: ''};
        const handledKeys = new Set(['tool_selection', 'tts_model_voices', 'google_ai_direct_stream']);
        Object.entries(categoryMeta).forEach(([categoryName, meta]) => {
            let hasKeys = false;
            let rowsHtml = '';
            meta.keys.forEach(key => {
                if (settings[key] !== undefined) {
                    hasKeys = true;
                    handledKeys.add(key);
                    let val = settings[key] !== undefined ? String(settings[key] ?? '') : '';
                    if (key === 'router_api_key' && val === 'sk-60f3eaf169d7c485-0icocf-0a3db541') {
                        val = '';
                    } else if (key === 'thinking_level' && val.toLowerCase() === 'default') {
                        val = '';
                    }
                    const hint = OrionI18n.has(`hint_${key}`) ? OrionI18n.t(`hint_${key}`) : '';
                    const placeholder = keyPlaceholders[key] || key;
                    let inputHtml = '';
                    if ((key === 'tts_enabled' || key === 'ai_chat_titles_enabled' || key === 'stt_enabled' || key === 'google_ai_direct_stream')) {
                        const isChecked = String(settings[key] ?? '').toLowerCase() === 'true';
                        inputHtml = `
                            <select id="setting-input-${key}" data-original="${isChecked ? 'true' : 'false'}" onchange="UI.handleSettingChange('${key}')" class="setting-select setting-select-compact">
                                <option value="true" ${isChecked ? 'selected' : ''}>${OrionI18n.t('enabled')}</option>
                                <option value="false" ${!isChecked ? 'selected' : ''}>${OrionI18n.t('disabled')}</option>
                            </select>`;
                    } else if (['chat_title_model', 'router_model_group', 'tts_model', 'tts_voice', 'stt_model', 'stt_language'].includes(key)) {
                        inputHtml = `<select id="setting-input-${key}" data-original="${escapeSetting(val)}" onchange="UI.handleSettingChange('${key}')" class="setting-select"></select>`;
                    } else if (key === 'temperature' || key === 'thinking_level') {
                        inputHtml = `<input id="setting-input-${key}" type="${key === 'temperature' ? 'number' : 'text'}" ${key === 'temperature' ? 'min="0" max="2" step="0.1"' : ''} data-original="${escapeSetting(val)}" value="${escapeSetting(val)}" placeholder="${OrionI18n.t('optional')}" oninput="UI.handleSettingChange('${key}')" class="setting-input">
                        `;
                    } else if (key === 'system_prompt') {
                        inputHtml = `<textarea id="setting-input-${key}" data-original="${escapeSetting(val)}" oninput="UI.handleSettingChange('${key}')" class="setting-textarea" rows="4" placeholder="${placeholder}">${escapeSetting(val)}</textarea>`;
                    } else if (key === 'router_api_key') {
                        const hasKey = Boolean(val && val.trim());
                        const maskedPlaceholder = hasKey ? OrionI18n.t('configuredRouterApiKey', val) : placeholder;
                        inputHtml = `
                            <div class="setting-secret-wrapper">
                                <div class="setting-secret-input-box">
                                    <input type="password" id="setting-input-${key}" data-original="" value="" autocomplete="new-password" oninput="UI.handleSettingChange('${key}')" class="setting-input setting-secret-input" placeholder="${escapeSetting(maskedPlaceholder)}">
                                    <button type="button" id="setting-secret-toggle-${key}" class="setting-secret-btn" onclick="UI.toggleSecretVisibility('${key}')" title="${OrionI18n.t('toggleApiKeyVisibility')}" aria-label="${OrionI18n.t('toggleApiKeyVisibility')}" aria-pressed="false">
                                        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 4.5C7 4.5 3.1 7.6 1 12c2.1 4.4 6 7.5 11 7.5s8.9-3.1 11-7.5C20.9 7.6 17 4.5 12 4.5Zm0 12a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9Zm0-7.2a2.7 2.7 0 1 0 0 5.4 2.7 2.7 0 0 0 0-5.4Z"/></svg>
                                    </button>
                                    <button type="button" id="setting-secret-clear-${key}" class="setting-secret-clear-btn" onclick="UI.clearRouterApiKey()" title="${OrionI18n.t('clearApiKey')}" aria-label="${OrionI18n.t('clearApiKey')}" ${hasKey ? '' : 'hidden'}>
                                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 6h16M9 6V3h6v3M6 6v15h12V6"/></svg>
                                    </button>
                                </div>
                            </div>`;
                    } else {
                        inputHtml = `<input type="text" id="setting-input-${key}" data-original="${escapeSetting(val)}" value="${escapeSetting(val)}" oninput="UI.handleSettingChange('${key}')" class="setting-input" placeholder="${placeholder}">`;
                    }
                    rowsHtml += `
                    <div class="setting-row">
                        <div class="setting-info">
                            <label class="setting-label" for="setting-input-${key}">${OrionI18n.has(`setting_${key}`) ? OrionI18n.t(`setting_${key}`) : key}</label>
                            ${hint ? `<span class="setting-hint">${hint}</span>` : ''}
                        </div>
                        <div class="setting-control">
                            ${inputHtml}
                            <span id="setting-status-${key}" class="setting-save-status" role="status"></span>
                            <button type="button" id="setting-retry-${key}" class="setting-retry" onclick="UI.saveSetting('${key}')" hidden>${OrionI18n.t('retry')}</button>
                        </div>
                    </div>`;
                }
            });
            if (hasKeys) {
                sections[meta.section] += `
                <div class="settings-card">
                    <div class="settings-card-header">
                        <div class="settings-card-icon">${meta.icon}</div>
                        <div>
                            <h3>${categoryName}</h3>
                            <p>${meta.desc}</p>
                        </div>
                    </div>
                    <div class="settings-card-body">
                        ${rowsHtml}
                    </div>
                </div>`;
            }
        });
        // Diğer kategorize edilmemiş ayarlar
        const unhandledKeys = Object.keys(settings).filter(k => !handledKeys.has(k)).sort();
        if (unhandledKeys.length > 0) {
            let rowsHtml = '';
            unhandledKeys.forEach(key => {
                const val = settings[key] !== undefined ? String(settings[key] ?? '') : '';
                rowsHtml += `
                <div class="setting-row">
                    <div class="setting-info">
                        <label class="setting-label" for="setting-input-${key}">${OrionI18n.has(`setting_${key}`) ? OrionI18n.t(`setting_${key}`) : key}</label>
                    </div>
                    <div class="setting-control">
                        <input type="text" id="setting-input-${key}" data-original="${escapeSetting(val)}" value="${escapeSetting(val)}" oninput="UI.handleSettingChange('${key}')" class="setting-input">
                        <span id="setting-status-${key}" class="setting-save-status" role="status"></span>
                            <button type="button" id="setting-retry-${key}" class="setting-retry" onclick="UI.saveSetting('${key}')" hidden>${OrionI18n.t('retry')}</button>
                    </div>
                </div>`;
            });
            sections.advanced += `
            <div class="settings-card">
                <div class="settings-card-header">
                    <div class="settings-card-icon">📦</div>
                    <div>
                        <h3>${OrionI18n.t('otherParameters')}</h3>
                        <p>${OrionI18n.t('otherParametersHint')}</p>
                    </div>
                </div>
                <div class="settings-card-body">
                    ${rowsHtml}
                </div>
            </div>`;
        }
        dashboard.innerHTML = `
        <section id="settings-panel-general" data-settings-panel="general" aria-labelledby="settings-general-title">
            <h2 id="settings-general-title" class="settings-section-title">${OrionI18n.t('general')}</h2>
            <div class="settings-card">
                <div class="settings-card-header">
                    <div class="settings-card-icon">🌐</div>
                    <div>
                        <h3>${OrionI18n.t('interfaceLanguage')}</h3>
                    </div>
                </div>
                <div class="settings-card-body">
                    <div class="setting-row compact-row">
                        <div class="setting-info">
                            <label class="setting-label" for="ui-language">${OrionI18n.t('language')}</label>
                                                    </div>
                        <div class="setting-control setting-control-compact">
                            <select id="ui-language" class="setting-select setting-select-compact" onchange="OrionI18n.setLanguage(this.value)">
                                ${OrionI18n.languages.map(language => `<option value="${language}">${window.orionUiLocales[language].languageName}</option>`).join('')}
                            </select>
                        </div>
                    </div>
                </div>
            </div>
            ${sections.general}
        </section>
        <button type="button" id="settings-advanced-toggle" class="settings-advanced-toggle" aria-expanded="false" aria-controls="settings-panel-advanced" onclick="UI.toggleAdvancedSettings()">
            ${OrionI18n.t('advanced')} <svg class="settings-advanced-chevron" aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m5 7.5 5 5 5-5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <section id="settings-panel-advanced" data-settings-panel="advanced" aria-labelledby="settings-advanced-toggle" hidden>${sections.advanced}</section>`;
        const uiLangEl = document.getElementById('ui-language');
        if (uiLangEl) uiLangEl.value = OrionI18n.language;
        window.RouterProviders?.mount();
        this.loadSttLanguageChoices();
    },
    async loadSttLanguageChoices() {
        const select = document.getElementById('setting-input-stt_language');
        if (!select || !window.STT) return;
        try {
            const catalog = await window.STT.loadLanguageCatalog();
            if (document.getElementById('setting-input-stt_language') !== select ||
                select.classList.contains('dirty') || select.dataset.saving === 'true') return;
            const value = window.STT.resolveLanguage(this.currentSettings?.stt_language, catalog);
            const displayNames = typeof Intl.DisplayNames === 'function'
                ? new Intl.DisplayNames([OrionI18n.language], {type: 'language'}) : null;
            const choices = Object.entries(catalog.languages).map(([code, entry]) => {
                let name = entry.name;
                try { name = displayNames?.of(code) || name; } catch {}
                return {code, name};
            }).sort((a, b) => a.name.localeCompare(b.name, OrionI18n.language));
              if (value !== 'auto' && !choices.some(choice => choice.code === value)) {
                  choices.push({code: value, name: value});
              }
            select.replaceChildren(new Option(OrionI18n.t('sttAutomaticLanguage'), 'auto'));
            for (const choice of choices) select.add(new Option(`${choice.name} (${choice.code})`, choice.code));
            select.value = value;
            select.dataset.original = value;
        } catch (error) {
            console.warn('STT language choices:', error);
        }
    },
    async loadModelChoices(settings, silent = false) {
        window.ToolsUI?.renderSettings();
        const button = document.getElementById('refresh-models');
        if (!silent && button?.disabled) return;
        if (button && !silent) button.disabled = true;
        if (button && !silent) button.title = OrionI18n.t('updating');
        const result = await API.getChatModels();
        if (button && !silent) button.disabled = false;
        if (result.error) {
            if (button && !silent) button.title = result.error;
            return;
        }
        this.routerCatalog = {...result, voices: this.routerCatalog?.voices || result.voices, model_voice_groups: this.routerCatalog?.model_voice_groups || result.model_voice_groups};
        for (const key of ['router_model_group', 'chat_title_model', 'tts_model', 'stt_model']) {
            const select = document.getElementById(`setting-input-${key}`);
            if (!select) continue;
            const current = select.options.length ? select.value : String(settings[key] || '');
            const original = select.dataset.original;
            const capability = key === 'tts_model' ? 'tts' : key === 'stt_model' ? 'stt' : 'chat';
            const models = result.models.filter(model => model.capability === capability &&
                (key !== 'stt_model' || (model.provider === 'local' && model.name === 'local-stt')));
            const choices = models.map(model => [model.name, model.name]);
            if (key === 'chat_title_model') choices.unshift(['', OrionI18n.t('useChatModel')]);
            if (current && !choices.some(([value]) => value === current)) {
                choices.push([current, `${current} — ${OrionI18n.t('listUnavailable')}`]);
            }
            // Reconcile in place, including the focused picker. Unchanged options
            // retain their DOM nodes and the user's pending selection is preserved.
            choices.forEach(([value, label], index) => {
                let option = Array.from(select.options).find(item => item.value === value);
                if (!option) option = new Option(label, value);
                if (option.text !== label) option.text = label;
                if (select.options[index] !== option) select.insertBefore(option, select.options[index] || null);
            });
            while (select.options.length > choices.length) select.remove(select.options.length - 1);
            select.value = current;
            select.dataset.original = original;
        }
        const ttsModel = document.getElementById('setting-input-tts_model');
        if (ttsModel) {
            ttsModel.onchange = () => {
                UI.handleSettingChange('tts_model');
                UI.loadVoiceChoices(true);
            };
        }
        this.loadVoiceChoices(false);
        // Optional voice services must not hold up model polling or selection.
        if (!this._voiceCatalogLoading && typeof API.getVoiceCatalog === 'function') {
            this._voiceCatalogLoading = true;
            API.getVoiceCatalog().then(catalog => {
                if (!catalog.error && this.routerCatalog) {
                    this.routerCatalog.voices = catalog.voices;
                    this.routerCatalog.model_voice_groups = catalog.model_voice_groups;
                    this.routerCatalog.unavailable = catalog.unavailable;
                    this.loadVoiceChoices(false);
                }
            }).catch(() => {}).finally(() => { this._voiceCatalogLoading = false; });
        }
        if (button && !silent) button.title = result.unavailable?.length
            ? OrionI18n.t('someListsUnavailable')
            : OrionI18n.t('refreshTitle');
    },
    loadVoiceChoices(userInitiated = false) {
        const select = document.getElementById('setting-input-tts_voice');
        const modelEl = document.getElementById('setting-input-tts_model');
        const model = modelEl?.value || this.currentSettings?.tts_model || '';
        
        if (!select || !this.routerCatalog || !model) return;
        const provider = this.routerCatalog.models?.find(item => item.name === model)?.provider;
        const groups = this.routerCatalog.model_voice_groups?.[provider] || [];
        const name = model.toLowerCase();
        const group = groups.find(g => (g.models || []).some(m => m.toLowerCase() === name))
            || groups.find(g => (g.match_patterns || []).some(p => p !== 'default' && name.includes(p.toLowerCase())))
            || groups.find(g => (g.match_patterns || []).includes('default'));
        const voices = group?.voices || this.routerCatalog.voices?.[provider] || [];
        const unavailable = this.routerCatalog.unavailable || [];
        const inaccessible = unavailable.includes('voices') || (provider === 'local' && unavailable.includes('local-tts-info'));
        let targetVoice = this.getRememberedVoice(model);
        select.replaceChildren(new Option(OrionI18n.t('defaultVoice'), ''));
        for (const voice of voices) select.add(new Option(voice, voice));
        if (targetVoice && voices.length && !inaccessible && !voices.some(v => v.toLowerCase() === targetVoice.toLowerCase())) {
            targetVoice = '';
            userInitiated = true;
        }
        if (targetVoice) {
            const matched = voices.find(v => v.toLowerCase() === targetVoice.toLowerCase());
            if (matched) {
                select.value = matched;
            } else {
                const label = `${targetVoice} — ${voices.length ? OrionI18n.t('voiceNotFound') : OrionI18n.t('voiceListUnavailable')}`;
                const missing = new Option(label, targetVoice);
                select.add(missing);
                select.value = targetVoice;
            }
        } else {
            select.value = '';
        }
        let notice = document.getElementById('tts-availability');
        if (!notice) {
            notice = document.createElement('span');
            notice.id = 'tts-availability';
            notice.className = 'setting-hint tts-availability';
            notice.style.marginLeft = '8px';
            const labelInfo = select.parentElement.previousElementSibling;
            if (labelInfo && labelInfo.querySelector('label')) {
                labelInfo.querySelector('label').appendChild(notice);
            } else {
                select.parentElement.appendChild(notice);
            }
        }
        notice.textContent = model === 'local-tts' && (inaccessible || !voices.length)
            ? OrionI18n.t('ttsUnavailableNotice')
            : '';
        notice.hidden = !notice.textContent;
        if (userInitiated) {
            this.handleSettingChange('tts_voice');
        }
    },
    clearChatArea() {
        for (const record of this._mediaPlayers || []) {
            record.request.abort();
            record.box.querySelector('audio')?.pause();
            if (record.url) URL.revokeObjectURL(record.url);
        }
        this._mediaPlayers?.clear();
        window.PcmSpeech?.stop();
        window.PcmSpeech?.recordings?.clear();
        this.chatArea.innerHTML = '';
    },
    renderChatList(chats, currentChatId) {
        const listEl = document.getElementById('chat-list');
        if (!listEl) return;
        listEl.innerHTML = '';
        if (!chats || chats.length === 0) {
            listEl.innerHTML = `<div style="color: #94a3b8; font-size: 0.85rem; text-align: center; padding: 10px;">${OrionI18n.t('noChatsYet')}</div>`;
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
                dateStr = d.toLocaleDateString(OrionI18n.language === 'tr' ? 'tr-TR' : 'en-US', { hour: '2-digit', minute: '2-digit' });
            } catch (e) { }
            const displayName = chat.name || `${OrionI18n.t('chat')} ${chat.chat_id.substring(0, 8)}...`;
            item.innerHTML = `
                <div class="chat-item-icon">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                    </svg>
                </div>
                <div class="chat-item-body">
                    <div class="chat-item-name">${displayName}</div>
                    <span class="chat-item-date">${dateStr}</span>
                </div>
                <div class="chat-item-actions">
                    <button class="chat-action-btn rename-btn" title="${OrionI18n.t('rename')}">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
                    </button>
                    <button class="chat-action-btn delete-btn" title="${OrionI18n.t('delete')}">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                    </button>
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
            item.addEventListener('click', (event) => {
                if (event.target.closest('.chat-item-actions')) return;
                if (window.loadChat) window.loadChat(chat.chat_id);
            });
            // Rename button
            item.querySelector('.rename-btn').addEventListener('click', async (e) => {
                e.stopPropagation();
                const nameEl = item.querySelector('.chat-item-name');
                const currentName = nameEl.textContent;
                const newName = prompt(OrionI18n.t('newChatNamePrompt'), currentName);
                if (!newName || newName.trim() === currentName) return;
                const result = await API.renameChat(chat.chat_id, newName.trim());
                if (result && !result.error) {
                    nameEl.textContent = newName.trim();
                    chat.name = newName.trim();
                } else {
                    alert(`${OrionI18n.t('errorPrefix')}: ${result?.error || OrionI18n.t('unknownError')}`);
                }
            });
            // Delete button
            item.querySelector('.delete-btn').addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm(OrionI18n.t('deleteChatConfirm', displayName))) return;
                const result = await API.deleteChat(chat.chat_id);
                if (result && !result.error) {
                    item.remove();
                    if (chat.chat_id === AppState.currentChatId) {
                        AppState.selectChat(null);
                        UI.clearChatArea();
                        UI.chatArea.innerHTML = `<div class="message bot">${OrionI18n.t('chatDeleted')}</div>`;
                    }
                } else {
                    alert(`${OrionI18n.t('errorPrefix')}: ${result?.error || OrionI18n.t('unknownError')}`);
                }
            });
            listEl.appendChild(item);
        });
    }
};
window.saveSetting = (key) => UI.saveSetting(key);
// Force all links in chat / markdown to open in a new tab without leaving the chat interface
if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
    document.addEventListener('click', (e) => {
        const link = e.target.closest('#chat-area a, .markdown-content a, .message a');
        if (link && link.href && !link.href.startsWith('javascript:')) {
            e.preventDefault();
            e.stopPropagation();
            window.open(link.href, '_blank', 'noopener,noreferrer');
        }
    }, true);
}
