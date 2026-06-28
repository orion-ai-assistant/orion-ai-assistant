document.addEventListener("DOMContentLoaded", () => {
    const adminKeyInput = document.getElementById("admin-key");
    const usersContainer = document.getElementById("users-container");
    const errorMsg = document.getElementById("error-message");
    const historyPanel = document.getElementById("chat-history-panel");
    const historyOverlay = document.getElementById("chat-history-overlay");
    const historyClose = document.getElementById("chat-history-close");
    const connectionStatusEl = document.getElementById("admin-connection-status");
    const pollIntervalMs = 5000;
    let pollTimer = null;
    let adminConnected = false;
    let currentHistoryChat = null;
    let historySignature = "";
    let usersSignature = "";
    let chatsSignature = "";
    let historyModalOpen = false;

    // --- Default Keys (from Python schema) ---
    let defaultKeys = [];
    const fetchDefaultKeys = async () => {
        try {
            const res = await fetch("/api/v1/admin/settings/schema");
            if (res.ok) defaultKeys = await res.json();
        } catch { /* ignore */ }
    };
    fetchDefaultKeys();


    const showError = (msg) => {
        errorMsg.textContent = msg;
        errorMsg.style.display = "block";
    };

    const hideError = () => {
        errorMsg.style.display = "none";
    };

    const setControlsDisabled = (disabled) => {
        const elements = document.querySelectorAll("main button, main input, main select, main textarea");
        elements.forEach((el) => {
            if (el.id === "admin-key" || el.classList.contains("admin-tab-btn")) return;
            if (disabled) {
                el.setAttribute("disabled", "true");
            } else {
                el.removeAttribute("disabled");
            }
        });
    };

    const setAdminConnection = (connected, message = null) => {
        adminConnected = connected;
        if (!connectionStatusEl) return;
        if (connected) {
            connectionStatusEl.textContent = "Bağlı";
            connectionStatusEl.classList.remove("offline");
            setControlsDisabled(false);
        } else {
            connectionStatusEl.textContent = message || "Bağlantı koptu, yeniden bağlanıyor...";
            connectionStatusEl.classList.add("offline");
            setControlsDisabled(true);
        }
    };

    const getHeaders = () => {
        return {
            "Content-Type": "application/json",
            "X-Admin-Key": adminKeyInput.value.trim()
        };
    };

    const showToast = (msg, isError = false) => {
        const toast = document.createElement("div");
        toast.className = "toast" + (isError ? " toast-error" : "");
        toast.textContent = msg;
        document.body.appendChild(toast);
        setTimeout(() => toast.classList.add("show"), 10);
        setTimeout(() => {
            toast.classList.remove("show");
            setTimeout(() => toast.remove(), 300);
        }, 2500);
    };

    setAdminConnection(false, "Admin API Key gerekli.");

    const showHistoryModal = () => {
        if (!historyOverlay) return;
        historyOverlay.style.display = "flex";
        historyModalOpen = true;
    };

    const hideHistoryModal = () => {
        if (!historyOverlay) return;
        historyOverlay.style.display = "none";
        historyModalOpen = false;
        currentHistoryChat = null;
    };

    if (historyClose) {
        historyClose.addEventListener("click", hideHistoryModal);
    }
    if (historyOverlay) {
        historyOverlay.addEventListener("click", (event) => {
            if (event.target === historyOverlay) hideHistoryModal();
        });
    }

    const buildUsersSignature = (usersData) => {
        const keys = Object.keys(usersData || {}).sort();
        return keys.map((key) => `${key}:${JSON.stringify(usersData[key])}`).join("|");
    };

    const buildChatsSignature = (chats) => JSON.stringify(chats || []);

    const loadData = async (silent = false) => {
        hideError();
        const key = adminKeyInput.value.trim();
        if (!key) {
            showError("Please enter the Admin API Key.");
            if (historyPanel) historyPanel.style.display = "none";
            setAdminConnection(false, "Admin API Key gerekli.");
            return;
        }

        if (!silent) {
            usersContainer.innerHTML = '<p class="placeholder">Loading...</p>';
        }

        try {
            const response = await fetch("/api/v1/admin/users/settings", {
                headers: getHeaders()
            });

            if (response.status === 401) {
                setAdminConnection(false, "Yetkilendirme gerekli.");
                throw new Error("Unauthorized: Invalid Admin API Key");
            }

            if (!response.ok) {
                throw new Error(`API Error: ${response.statusText}`);
            }

            const data = await response.json();
            const nextSignature = buildUsersSignature(data);
            if (silent && nextSignature === usersSignature) {
                setAdminConnection(true);
                return;
            }
            usersSignature = nextSignature;
            setAdminConnection(true);
            renderUsers(data);
        } catch (err) {
            if (!silent) {
                usersContainer.innerHTML = '';
            }
            setAdminConnection(false);
            showError(err.message);
        }
    };

    const deleteSetting = async (userId, key) => {
        if (!adminConnected) {
            showToast("Bağlantı yok. Yeniden bağlanılıyor.", true);
            return;
        }
        if (!confirm(`'${userId}' kullanıcısının '${key}' ayarını silmek istediğinize emin misiniz?`)) {
            return;
        }

        try {
            const response = await fetch(`/api/v1/admin/users/${userId}/settings/${key}`, {
                method: "DELETE",
                headers: getHeaders()
            });

            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body.detail || "Ayar silinemedi");
            }

            showToast(`'${key}' başarıyla silindi.`);
            await loadData();
        } catch (err) {
            showToast(err.message, true);
        }
    };

    const saveSetting = async (userId, key, value) => {
        if (!adminConnected) {
            showToast("Bağlantı yok. Yeniden bağlanılıyor.", true);
            return;
        }
        try {
            const payload = {
                user_id: userId,
                values: {}
            };
            payload.values[key] = value;

            const response = await fetch("/api/v1/admin/settings", {
                method: "POST",
                headers: getHeaders(),
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body.detail || "Ayar kaydedilemedi");
            }

            showToast(`'${key}' başarıyla kaydedildi.`);
            await loadData();
        } catch (err) {
            showToast(err.message, true);
        }
    };

    const renderUsers = (usersData) => {
        usersContainer.innerHTML = '';

        const userIds = Object.keys(usersData);
        if (userIds.length === 0) {
            usersContainer.innerHTML = '<p class="placeholder">No user settings found in the database.</p>';
            return;
        }

        const template = document.getElementById("user-card-template");

        // Sort: global first, then alphabetical
        userIds.sort((a, b) => {
            if (a === "global") return -1;
            if (b === "global") return 1;
            return a.localeCompare(b);
        });

        userIds.forEach(userId => {
            const clone = template.content.cloneNode(true);
            const userIdEl = clone.querySelector(".user-id");
            userIdEl.textContent = `User: ${userId}`;
            if (userId === "global") {
                userIdEl.innerHTML = `User: ${userId} <span class="badge-global">GLOBAL DEFAULTS</span>`;
            }

            const tbody = clone.querySelector(".settings-tbody");
            const settings = usersData[userId];
            const isGlobal = userId === "global";

            if (Object.keys(settings).length === 0) {
                const tr = document.createElement("tr");
                tr.innerHTML = `<td colspan="3" class="placeholder" style="padding: 10px;">No specific settings overrides for this user.</td>`;
                tbody.appendChild(tr);
            } else {
                Object.entries(settings).forEach(([key, val]) => {
                    const isProtected = isGlobal && defaultKeys.includes(key);
                    const tr = document.createElement("tr");

                    // Key column
                    const tdKey = document.createElement("td");
                    tdKey.textContent = key;
                    if (!defaultKeys.includes(key)) {
                        const legacyBadge = document.createElement("span");
                        legacyBadge.className = "badge-legacy";
                        legacyBadge.textContent = "LEGACY";
                        tdKey.appendChild(document.createTextNode(" "));
                        tdKey.appendChild(legacyBadge);
                    }

                    // Value column (editable)
                    const tdVal = document.createElement("td");
                    const valInput = document.createElement("input");
                    valInput.type = "text";
                    valInput.className = "edit-input";
                    valInput.value = val;
                    valInput.dataset.originalValue = val;
                    tdVal.appendChild(valInput);

                    // Actions column
                    const tdActions = document.createElement("td");
                    tdActions.className = "actions-cell";

                    // Save button (hidden until value changes)
                    const saveBtn = document.createElement("button");
                    saveBtn.className = "btn-save";
                    saveBtn.textContent = "Kaydet";
                    saveBtn.style.display = "none";
                    saveBtn.onclick = () => saveSetting(userId, key, valInput.value);
                    tdActions.appendChild(saveBtn);

                    // Show save button when value changes
                    valInput.addEventListener("input", () => {
                        saveBtn.style.display = valInput.value !== valInput.dataset.originalValue ? "inline-block" : "none";
                    });

                    // Delete button (disabled for protected global keys)
                    const delBtn = document.createElement("button");
                    delBtn.className = "btn-danger";
                    delBtn.textContent = "Sil";
                    if (isProtected) {
                        delBtn.disabled = true;
                        delBtn.title = "Global varsayılan ayarlar silinemez";
                        delBtn.classList.add("btn-disabled");
                    } else {
                        delBtn.onclick = () => deleteSetting(userId, key);
                    }
                    tdActions.appendChild(delBtn);

                    tr.appendChild(tdKey);
                    tr.appendChild(tdVal);
                    tr.appendChild(tdActions);
                    tbody.appendChild(tr);
                });
            }

            // Add Setting Row
            const addRow = document.createElement("tr");
            addRow.className = "add-row";
            addRow.innerHTML = `
                <td><input type="text" class="edit-input add-key-input" placeholder="Anahtar adı (örn: system_prompt)"></td>
                <td><input type="text" class="edit-input add-val-input" placeholder="Değer"></td>
                <td class="actions-cell">
                    <button class="btn-add">+ Ekle</button>
                </td>
            `;
            tbody.appendChild(addRow);

            const addKeyInput = addRow.querySelector(".add-key-input");
            const addValInput = addRow.querySelector(".add-val-input");
            const addBtn = addRow.querySelector(".btn-add");
            addBtn.onclick = () => {
                const k = addKeyInput.value.trim();
                const v = addValInput.value.trim();
                if (!k) { showToast("Anahtar adı boş olamaz", true); return; }
                if (!v) { showToast("Değer boş olamaz", true); return; }
                saveSetting(userId, k, v);
            };

            usersContainer.appendChild(clone);
        });
    };

    let keyDebounceId = null;
    const loadActiveTab = () => {
        const activeTab = document.querySelector(".admin-tab-btn.active")?.dataset.tab;
        if (activeTab === "tab-chats") {
            loadChats();
        } else {
            loadData();
        }
    };

    const loadActiveTabSilent = () => {
        const activeTab = document.querySelector(".admin-tab-btn.active")?.dataset.tab;
        if (activeTab === "tab-chats") {
            loadChats(true);
        } else {
            loadData(true);
        }
    };

    const startPolling = () => {
        if (pollTimer) return;
        pollTimer = setInterval(() => {
            if (!adminKeyInput.value.trim()) return;
            loadActiveTabSilent();
            if (historyModalOpen && historyPanel && currentHistoryChat) {
                loadChatHistory(currentHistoryChat, true);
            }
        }, pollIntervalMs);
    };

    const stopPolling = () => {
        if (!pollTimer) return;
        clearInterval(pollTimer);
        pollTimer = null;
    };

    const handleKeyInput = () => {
        hideError();
        const key = adminKeyInput.value.trim();
        if (key) {
            setAdminConnection(false, "Bağlanıyor...");
            startPolling();
        } else {
            setAdminConnection(false, "Admin API Key gerekli.");
            stopPolling();
        }

        if (keyDebounceId) clearTimeout(keyDebounceId);
        if (!key) return;
        keyDebounceId = setTimeout(loadActiveTab, 400);
    };

    adminKeyInput.addEventListener("input", handleKeyInput);
    adminKeyInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            loadActiveTab();
        }
    });


    // --- Tab switching ---
    document.querySelectorAll(".admin-tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".admin-tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".admin-tab-panel").forEach(p => p.classList.remove("active"));
            btn.classList.add("active");
            document.getElementById(btn.dataset.tab).classList.add("active");
            if (btn.dataset.tab === "tab-chats") loadChats();
        });
    });

    // --- Chat search filter ---
    let allChatsData = [];
    let chatSearchQuery = "";
    const chatSearch = document.getElementById("chat-search");
    if (chatSearch) {
        chatSearch.addEventListener("input", () => {
            chatSearchQuery = chatSearch.value.trim().toLowerCase();
            applyChatFilter();
        });
    }

    const applyChatFilter = () => {
        const q = chatSearchQuery;
        if (!q) {
            renderChats(allChatsData);
            return;
        }
        const filtered = allChatsData.filter(c =>
            (c.chat_id || "").toLowerCase().includes(q) ||
            (c.user_id || "").toLowerCase().includes(q) ||
            (c.name || "").toLowerCase().includes(q)
        );
        renderChats(filtered);
    };

    async function loadChats(silent = false) {
        const key = adminKeyInput.value.trim();
        if (!key) { showError("Please enter the Admin API Key."); return; }
        const chatsContainer = document.getElementById("chats-container");
        if (!silent) {
            chatsContainer.innerHTML = '<p class="placeholder">Yükleniyor...</p>';
        }
        if (!silent && historyPanel) historyPanel.style.display = "none";

        try {
            const res = await fetch("/api/v1/admin/chats", { headers: getHeaders() });
            if (res.status === 401) {
                setAdminConnection(false, "Yetkilendirme gerekli.");
                throw new Error("Unauthorized: Invalid Admin API Key");
            }
            if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
            allChatsData = await res.json();
            const nextSignature = buildChatsSignature(allChatsData);
            if (silent && nextSignature === chatsSignature) {
                setAdminConnection(true);
                return;
            }
            chatsSignature = nextSignature;
            setAdminConnection(true);
            applyChatFilter();
        } catch (err) {
            if (!silent) {
                chatsContainer.innerHTML = '';
            }
            setAdminConnection(false);
            showError(err.message);
        }
    }

    const formatContent = (content) => {
        if (typeof content === "string") return content;
        if (Array.isArray(content)) {
            return content.map((item) => item?.text || "[media]").join("");
        }
        if (content && typeof content === "object") {
            try { return JSON.stringify(content); } catch { return "[unreadable content]"; }
        }
        return "";
    };

    async function loadChatHistory(chat, silent = false) {
        if (!historyPanel) return;
        const key = adminKeyInput.value.trim();
        if (!key) { showError("Please enter the Admin API Key."); return; }
        if (!adminConnected && !silent) {
            showToast("Bağlantı yok. Yeniden bağlanılıyor.", true);
            return;
        }

        historyPanel.style.display = "block";
        if (!silent) {
            showHistoryModal();
        }
        if (!silent) {
            historyPanel.innerHTML = '<p class="placeholder">Sohbet gecmisi yukleniyor...</p>';
        }

        const listEl = historyPanel.querySelector(".chat-history-list");
        const previousScroll = silent && listEl ? listEl.scrollTop : 0;

        try {
            const res = await fetch(`/api/v1/admin/chats/${chat.chat_id}/history`, { headers: getHeaders() });
            if (res.status === 401) {
                setAdminConnection(false, "Yetkilendirme gerekli.");
                throw new Error("Unauthorized: Invalid Admin API Key");
            }
            if (!res.ok) throw new Error(`API Error: ${res.statusText}`);
            const history = await res.json();
            const nextSignature = JSON.stringify(history);
            if (silent && nextSignature === historySignature) {
                setAdminConnection(true);
                return;
            }
            historySignature = nextSignature;
            currentHistoryChat = chat;
            setAdminConnection(true);
            renderChatHistory(chat, history);
            if (silent) {
                const nextList = historyPanel.querySelector(".chat-history-list");
                if (nextList) nextList.scrollTop = previousScroll;
            }
        } catch (err) {
            if (!silent) {
                historyPanel.innerHTML = '';
                showError(err.message);
            }
            setAdminConnection(false);
        }
    }

    function renderChatHistory(chat, history) {
        if (!historyPanel) return;
        const title = chat.name || chat.chat_id.substring(0, 12) + "...";
        const count = history.length;

        historyPanel.innerHTML = "";
        const header = document.createElement("div");
        header.className = "chat-history-header";

        const titleEl = document.createElement("h3");
        titleEl.className = "chat-history-title";
        titleEl.textContent = title;

        const metaEl = document.createElement("div");
        metaEl.className = "chat-history-meta";
        metaEl.textContent = `${chat.chat_id} • ${count} mesaj`;

        header.appendChild(titleEl);
        header.appendChild(metaEl);

        const list = document.createElement("div");
        list.className = "chat-history-list";

        if (!count) {
            const empty = document.createElement("div");
            empty.className = "placeholder";
            empty.textContent = "Bu sohbette kayitli mesaj yok.";
            list.appendChild(empty);
        } else {
            history.forEach((item) => {
                const role = item.role || "unknown";
                const content = formatContent(item.content || "");
                const partial = item.partial ? " (partial)" : "";

                const card = document.createElement("div");
                card.className = `chat-history-item ${role}`;

                const roleEl = document.createElement("div");
                roleEl.className = "role";
                roleEl.textContent = `${role}${partial}`;

                card.appendChild(roleEl);

                if (item.thinking) {
                    const thinkText = formatContent(item.thinking);
                    const details = document.createElement("details");
                    details.className = "think-block";
                    details.open = false;

                    const summary = document.createElement("summary");
                    summary.className = "think-summary";
                    const charCount = thinkText.length;
                    summary.textContent = `Dusunce (${charCount} karakter)`;
                    details.appendChild(summary);

                    const body = document.createElement("pre");
                    body.className = "think-body";
                    body.textContent = thinkText || "-";
                    details.appendChild(body);

                    card.appendChild(details);
                }

                const contentEl = document.createElement("div");
                contentEl.textContent = content || "-";
                card.appendChild(contentEl);

                list.appendChild(card);
            });
        }

        historyPanel.appendChild(header);
        historyPanel.appendChild(list);
    }

    async function deleteAdminChat(chatId, rowEl) {
        if (!adminConnected) {
            showToast("Bağlantı yok. Yeniden bağlanılıyor.", true);
            return;
        }
        if (!confirm(`"${chatId}" sohbetini kalıcı olarak silmek istediğinize emin misiniz?`)) return;
        try {
            const res = await fetch(`/api/v1/admin/chats/${chatId}`, { method: "DELETE", headers: getHeaders() });
            if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Silinemedi");
            rowEl.remove();
            allChatsData = allChatsData.filter(c => c.chat_id !== chatId);
            document.getElementById("chats-count").textContent = `${allChatsData.length} sohbet`;
            showToast(`"${chatId}" silindi.`);
        } catch (err) {
            showToast(err.message, true);
        }
    }

    async function renameAdminChat(chatId, newName, nameCell) {
        if (!adminConnected) {
            showToast("Bağlantı yok. Yeniden bağlanılıyor.", true);
            return;
        }
        try {
            const res = await fetch(`/api/v1/admin/chats/${chatId}/rename`, {
                method: "PATCH",
                headers: getHeaders(),
                body: JSON.stringify({ name: newName })
            });
            if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Yeniden adlandırma başarısız.");
            nameCell.querySelector(".chat-display-name").textContent = newName || chatId.substring(0, 8) + "...";
            const chat = allChatsData.find(c => c.chat_id === chatId);
            if (chat) chat.name = newName;
            showToast("Yeniden adlandırıldı.");
        } catch (err) {
            showToast(err.message, true);
        }
    }

    function renderChats(chats) {
        const container = document.getElementById("chats-container");
        const countEl = document.getElementById("chats-count");
        if (countEl) countEl.textContent = `${chats.length} sohbet`;

        if (!chats.length) {
            container.innerHTML = '<p class="placeholder">Sohbet bulunamadı.</p>';
            return;
        }

        const table = document.createElement("table");
        table.className = "chats-table";
        table.innerHTML = `<thead><tr>
            <th>Sohbet Adı / ID</th>
            <th>Kullanıcı</th>
            <th>Durum</th>
            <th>Son Güncelleme</th>
            <th>İşlemler</th>
        </tr></thead>`;

        const tbody = document.createElement("tbody");
        chats.forEach(chat => {
            const tr = document.createElement("tr");
            const displayName = chat.name || chat.chat_id.substring(0, 12) + "...";

            let dateStr = "";
            try { dateStr = new Date(chat.updated_at).toLocaleString("tr-TR"); } catch (e) { }

            // Name cell
            const tdName = document.createElement("td");
            tdName.innerHTML = `
                <div class="chat-name-cell">
                    <span class="chat-display-name" style="cursor:pointer;" title="${chat.chat_id}">${displayName}</span>
                    <input class="inline-name-input" type="text" value="${chat.name || ''}" placeholder="Yeni ad..." style="display:none;">
                    <button class="btn-sm btn-save-name">💾</button>
                </div>
                <small style="color:#4b5563;font-size:0.72rem;">${chat.chat_id}</small>
            `;
            const nameSpan = tdName.querySelector(".chat-display-name");
            const nameInput = tdName.querySelector(".inline-name-input");
            const saveNameBtn = tdName.querySelector(".btn-save-name");

            nameSpan.addEventListener("click", () => {
                nameSpan.style.display = "none";
                nameInput.style.display = "inline-block";
                saveNameBtn.style.display = "inline-block";
                nameInput.focus();
            });
            saveNameBtn.addEventListener("click", async () => {
                const newName = nameInput.value.trim();
                await renameAdminChat(chat.chat_id, newName, tdName);
                nameInput.style.display = "none";
                saveNameBtn.style.display = "none";
                nameSpan.style.display = "inline";
            });
            nameInput.addEventListener("keydown", async (e) => {
                if (e.key === "Enter") saveNameBtn.click();
                if (e.key === "Escape") {
                    nameInput.style.display = "none";
                    saveNameBtn.style.display = "none";
                    nameSpan.style.display = "inline";
                }
            });

            // Other cells
            const tdUser = document.createElement("td");
            tdUser.innerHTML = `<span class="user-badge">${chat.user_id || "-"}</span>`;

            const tdStatus = document.createElement("td");
            const statusColors = { completed: "#22c55e", processing: "#f59e0b", stopped: "#94a3b8", queued: "#3b82f6", failed: "#ef4444" };
            const sc = statusColors[chat.status] || "#94a3b8";
            tdStatus.innerHTML = `<span style="color:${sc};font-weight:500;">${chat.status || "-"}</span>`;

            const tdDate = document.createElement("td");
            tdDate.textContent = dateStr;
            tdDate.style.color = "#94a3b8";

            // Actions cell
            const tdActions = document.createElement("td");
            tdActions.style.display = "flex";
            tdActions.style.gap = "6px";

            const viewBtn = document.createElement("button");
            viewBtn.className = "btn-sm btn-view";
            viewBtn.textContent = "Goruntule";
            viewBtn.addEventListener("click", () => loadChatHistory(chat));
            tdActions.appendChild(viewBtn);

            const delBtn = document.createElement("button");
            delBtn.className = "btn-sm btn-del";
            delBtn.textContent = "Sil";
            delBtn.addEventListener("click", () => deleteAdminChat(chat.chat_id, tr));
            tdActions.appendChild(delBtn);

            tr.appendChild(tdName);
            tr.appendChild(tdUser);
            tr.appendChild(tdStatus);
            tr.appendChild(tdDate);
            tr.appendChild(tdActions);
            tbody.appendChild(tr);
        });

        table.appendChild(tbody);
        container.innerHTML = "";
        container.appendChild(table);
    }

});
