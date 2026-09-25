document.addEventListener("DOMContentLoaded", () => {
    const adminKeyInput = document.getElementById("admin-key");
    const usersContainer = document.getElementById("users-container");
    const errorMsg = document.getElementById("error-message");
    const historyPanel = document.getElementById("chat-history-panel");
    const historyOverlay = document.getElementById("chat-history-overlay");
    const historyClose = document.getElementById("chat-history-close");
    const connectionStatusEl = document.getElementById("admin-connection-status");
    const settingsSearch = document.getElementById("settings-search");
    const pollIntervalMs = 5000;
    let pollTimer = null;
    let adminConnected = false;
    let currentHistoryChat = null;
    let historySignature = "";
    let usersSignature = "";
    let chatsSignature = "";
    let historyModalOpen = false;
    let chatRenameInProgress = false;

    // --- Default Keys (from Python schema) ---
    let defaultKeys = [];
    let defaultValues = {};
    let settingConstraints = {};
    let routerCatalog = null;
    let catalogError = "";
    let catalogCheckedAt = 0;
    let catalogSignature = "";
    const fetchDefaultKeys = async () => {
        try {
            const res = await fetch("/api/v1/admin/settings/schema");
            if (res.ok) defaultKeys = await res.json();
        } catch { /* ignore */ }
    };
    const defaultKeysPromise = fetchDefaultKeys();
    const fetchDefaultValues = async () => {
        const res = await fetch("/api/v1/admin/settings/defaults", { headers: getHeaders() });
        if (res.ok) defaultValues = await res.json();
    };
    const fetchSettingConstraints = async () => {
        const res = await fetch("/api/v1/admin/settings/constraints", { headers: getHeaders() });
        if (res.ok) settingConstraints = await res.json();
    };
    const fetchRouterCatalog = async () => {
        if (Date.now() - catalogCheckedAt < 15000) return false;
        catalogCheckedAt = Date.now();
        try {
            const res = await fetch("/api/v1/admin/models", { headers: getHeaders() });
            if (!res.ok) throw new Error("Router model listesi alınamadı");
            const catalog = await res.json();
            const signature = JSON.stringify(catalog);
            const changed = signature !== catalogSignature;
            catalogSignature = signature;
            routerCatalog = catalog;
            catalogError = "";
            return changed;
        } catch (err) {
            catalogError = err.message;
            return false;
        }
    };


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
                if (!el.disabled) {
                    el.disabled = true;
                    el.dataset.disconnectedDisabled = "true";
                }
            } else if (el.dataset.disconnectedDisabled === "true") {
                el.disabled = false;
                delete el.dataset.disconnectedDisabled;
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
        return keys.map(userId => {
            const settings = usersData[userId] || {};
            return [userId, Object.keys(settings).sort().map(key => [key, settings[key]])];
        }).map(JSON.stringify).join("|");
    };

    const buildChatsSignature = (chats) => JSON.stringify(chats || []);

    const loadData = async (silent = false, force = false) => {
        hideError();
        const key = adminKeyInput.value.trim();
        if (!key) {
            showError("Please enter the Admin API Key.");
            if (historyPanel) historyPanel.style.display = "none";
            setAdminConnection(false, "Admin API Key gerekli.");
            return;
        }

        if (!silent && !usersSignature) {
            usersContainer.innerHTML = '<p class="placeholder">Ayarlar yükleniyor…</p>';
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
            await defaultKeysPromise;
            if (!Object.keys(defaultValues).length) await fetchDefaultValues();
            if (!Object.keys(settingConstraints).length) await fetchSettingConstraints();
            const catalogChanged = await fetchRouterCatalog();
            if (!defaultKeys.length && Object.keys(defaultValues).length) {
                defaultKeys = Object.keys(defaultValues).sort();
            }
            const nextSignature = buildUsersSignature(data);
            if (silent && !force && !catalogChanged && nextSignature === usersSignature) {
                setAdminConnection(true);
                return;
            }
            setAdminConnection(true);
            if (silent && !force && usersContainer.querySelector(".edit-input:focus")) return;
            usersSignature = nextSignature;
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
        if (!confirm(`${userId} kullanıcısının ${key} ayarı kaldırılsın mı? Bundan sonra global değer kullanılacak.`)) {
            return;
        }

        try {
            const response = await fetch(`/api/v1/admin/users/${encodeURIComponent(userId)}/settings/${encodeURIComponent(key)}`, {
                method: "DELETE",
                headers: getHeaders()
            });

            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body.detail || "Ayar silinemedi");
            }

            showToast(`${key} kullanıcı ayarı kaldırıldı.`);
            await loadData(true, true);
        } catch (err) {
            showToast(err.message, true);
        }
    };

    const saveSetting = async (userId, key, value) => {
        if (!adminConnected) {
            showToast("Bağlantı yok. Yeniden bağlanılıyor.", true);
            return;
        }
        const validationError = validateSettingValue(key, value);
        if (validationError) {
            showToast(validationError, true);
            return;
        }
        try {
            const payload = {
                user_id: userId,
                values: {}
            };
            payload.values[key] = value;

            const response = await fetch("/api/v1/admin/users/settings", {
                method: "POST",
                headers: getHeaders(),
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body.detail || "Ayar kaydedilemedi");
            }

            showToast(`${key} kaydedildi.`);
            await loadData(true, true);
        } catch (err) {
            showToast(err.message, true);
        }
    };

    const resetGlobalSetting = async (key) => {
        if (!adminConnected) return;
        if (!confirm(`${key} fabrika varsayılanına sıfırlansın mı?`)) return;
        try {
            const response = await fetch(`/api/v1/admin/settings/global/${encodeURIComponent(key)}/reset`, {
                method: "POST",
                headers: getHeaders()
            });
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body.detail || "Ayar sıfırlanamadı");
            }
            showToast(`${key} varsayılan değerine döndü.`);
            await loadData(true, true);
        } catch (err) {
            showToast(err.message, true);
        }
    };

    const makeAction = (label, className, onClick) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `action-btn ${className}`;
        button.textContent = label;
        button.addEventListener("click", onClick);
        return button;
    };

    const isFactoryValue = (key, value) => {
        const factory = defaultValues[key];
        if (factory === undefined) return false;
        if (factory === "True" || factory === "False") {
            return String(value).toLowerCase() === factory.toLowerCase();
        }
        return String(value) === factory;
    };

    const modelCapabilities = {
        router_model_group: "chat",
        chat_title_model: "chat",
        tts_model: "tts",
        stt_model: "stt"
    };
    const isChoiceSetting = key => key in modelCapabilities || key === "tts_voice";
    const getSettingChoices = (key, effectiveSettings) => {
        if (!routerCatalog) return [];
        if (key === "tts_voice") {
            const provider = routerCatalog.models.find(model => model.name === effectiveSettings.tts_model)?.provider;
            return [...new Set(routerCatalog.voices[provider] || [])].sort();
        }
        return [...new Set(routerCatalog.models
            .filter(model => model.capability === modelCapabilities[key] &&
                (key !== "stt_model" || (model.provider === "local" && model.name === "local-stt")))
            .map(model => model.name))].sort();
    };
    const populateChoiceSelect = (select, key, current, effectiveSettings) => {
        const choices = getSettingChoices(key, effectiveSettings);
        select.replaceChildren();
        if (key === "chat_title_model" || key === "tts_voice") {
            select.add(new Option(key === "chat_title_model" ? "Sohbet modelini kullan" : "Varsayılan ses", ""));
        }
        choices.forEach(choice => select.add(new Option(choice, choice)));
        if (current && !choices.includes(current)) {
            select.add(new Option(`${current} · listede yok`, current));
        }
        if (!select.options.length) select.add(new Option("Router seçenekleri alınamadı", ""));
        select.value = current;
        select.disabled = !choices.length && !["chat_title_model", "tts_voice"].includes(key);
    };
    const getRangeText = key => {
        const constraint = settingConstraints[key];
        if (!constraint || !["integer", "number"].includes(constraint.type)) return "";
        return `${constraint.minimum}-${constraint.maximum}`;
    };
    const validateSettingValue = (key, value) => {
        const constraint = settingConstraints[key];
        if (!constraint || !["integer", "number"].includes(constraint.type)) return "";
        if (constraint.nullable && !String(value).trim()) return "";
        const number = Number(value);
        if (!String(value).trim() || !Number.isFinite(number) ||
            (constraint.type === "integer" && !Number.isInteger(number)) ||
            number < constraint.minimum || number > constraint.maximum) {
            return `${key} için geçerli aralık: ${getRangeText(key)}.`;
        }
        return "";
    };

    const applySettingsFilter = () => {
        const query = settingsSearch?.value.trim().toLocaleLowerCase("tr-TR") || "";
        let visibleCards = 0;
        usersContainer.querySelectorAll(".user-card").forEach(card => {
            const userMatches = card.dataset.userId.toLocaleLowerCase("tr-TR").includes(query);
            let visible = 0;
            card.querySelectorAll(".setting-row").forEach(row => {
                const matches = !query || userMatches || row.dataset.settingKey.toLocaleLowerCase("tr-TR").includes(query);
                row.hidden = !matches;
                if (matches) visible++;
            });
            card.hidden = !!query && !userMatches && visible === 0;
            if (!card.hidden) visibleCards++;
            const addRow = card.querySelector(".add-row");
            if (addRow) addRow.hidden = !!query && !userMatches;
        });
        let empty = usersContainer.querySelector(".filter-empty");
        if (query && !visibleCards) {
            if (!empty) {
                empty = document.createElement("p");
                empty.className = "placeholder filter-empty";
                empty.textContent = "Aramanızla eşleşen ayar bulunamadı.";
                usersContainer.appendChild(empty);
            }
        } else if (empty) {
            empty.remove();
        }
    };
    settingsSearch?.addEventListener("input", applySettingsFilter);

    const renderUsers = (usersData) => {
        usersContainer.innerHTML = "";
        const userIds = Object.keys(usersData).sort((a, b) =>
            a === "global" ? -1 : b === "global" ? 1 : a.localeCompare(b, "tr")
        );
        if (!userIds.length) {
            usersContainer.innerHTML = '<p class="placeholder">Henüz kayıtlı ayar bulunamadı.</p>';
            return;
        }

        const template = document.getElementById("user-card-template");
        userIds.forEach(userId => {
            const clone = template.content.cloneNode(true);
            const card = clone.querySelector(".user-card");
            const isGlobal = userId === "global";
            const settings = usersData[userId] || {};
            const effectiveSettings = { ...(usersData.global || {}), ...settings };
            card.dataset.userId = userId;
            if (isGlobal) card.classList.add("global-card");
            clone.querySelector(".user-id").textContent = isGlobal ? "Global varsayılanlar" : userId;
            clone.querySelector(".user-description").textContent = isGlobal
                ? "Yeni kullanıcılar ve kişisel ayarı olmayanlar için geçerlidir."
                : "Bu kullanıcıya özel ayarlar, global değerlerin üzerine yazılır.";
            clone.querySelector(".setting-count").textContent = `${Object.keys(settings).length} ayar`;
            const tbody = clone.querySelector(".settings-tbody");

            Object.entries(settings)
                .sort(([a], [b]) => a.localeCompare(b, "en"))
                .forEach(([key, val]) => {
                    const known = defaultKeys.includes(key);
                    const tr = document.createElement("tr");
                    tr.className = "setting-row";
                    tr.dataset.settingKey = key;

                    const tdKey = document.createElement("td");
                    tdKey.className = "setting-key";
                    tdKey.textContent = key;
                    if (!known) {
                        const badge = document.createElement("span");
                        badge.className = "badge-legacy";
                        badge.textContent = "ESKİ";
                        tdKey.appendChild(badge);
                    }

                    const tdVal = document.createElement("td");
                    const valueWrap = document.createElement("div");
                    valueWrap.className = "setting-value-wrap";
                    const isBoolean = defaultValues[key] === "True" || defaultValues[key] === "False";
                    const isChoice = known && isChoiceSetting(key);
                    const constraint = settingConstraints[key];
                    const valInput = document.createElement(isBoolean || isChoice ? "select" : "input");
                    if (isBoolean) {
                        ["True", "False"].forEach(value => {
                            const option = document.createElement("option");
                            option.value = value;
                            option.textContent = value === "True" ? "Açık" : "Kapalı";
                            valInput.appendChild(option);
                        });
                    } else if (isChoice) {
                        populateChoiceSelect(valInput, key, String(val), effectiveSettings);
                    } else {
                        valInput.type = key.endsWith("_api_key") ? "password" :
                            ["integer", "number"].includes(constraint?.type) ? "number" : "text";
                        if (valInput.type === "number") {
                            valInput.min = constraint.minimum;
                            valInput.max = constraint.maximum;
                            valInput.step = constraint.type === "integer" ? "1" : "any";
                        }
                    }
                    valInput.className = "edit-input";
                    valInput.value = isBoolean ? (String(val).toLowerCase() === "true" ? "True" : "False") : val;
                    valInput.dataset.originalValue = valInput.value;
                    valInput.setAttribute("aria-label", `${key} değeri`);
                    if (!known) {
                        valInput.readOnly = true;
                        valInput.title = "Bu ayar güncel şemada bulunmuyor";
                    }
                    valueWrap.appendChild(valInput);
                    if (valInput.type === "password") {
                        const visibility = makeAction("Göster", "btn-visibility", () => {
                            valInput.type = valInput.type === "password" ? "text" : "password";
                            visibility.textContent = valInput.type === "password" ? "Göster" : "Gizle";
                        });
                        visibility.setAttribute("aria-label", `${key} değerini göster veya gizle`);
                        valueWrap.appendChild(visibility);
                    }
                    tdVal.appendChild(valueWrap);
                    const rangeText = getRangeText(key);
                    if (rangeText) {
                        valueWrap.classList.add("has-range");
                        const badge = document.createElement("span");
                        badge.className = "range-badge";
                        badge.textContent = rangeText;
                        badge.setAttribute("aria-hidden", "true");
                        valInput.title = `İzin verilen aralık: ${rangeText}`;
                        valueWrap.appendChild(badge);
                    } else if (isChoice && !routerCatalog) {
                        const hint = document.createElement("small");
                        hint.className = "value-hint value-warning";
                        hint.textContent = catalogError || "Router seçenekleri alınamadı";
                        tdVal.appendChild(hint);
                    }

                    const tdActions = document.createElement("td");
                    tdActions.className = "actions-cell";
                    const actions = document.createElement("div");
                    actions.className = "row-actions";
                    if (known) {
                        const saveBtn = makeAction("Kaydet", "btn-save", () => saveSetting(userId, key, valInput.value));
                        const revertBtn = makeAction("Vazgeç", "btn-revert", () => {
                            valInput.value = valInput.dataset.originalValue;
                            updateActions();
                        });
                        saveBtn.hidden = true;
                        revertBtn.hidden = true;
                        actions.append(saveBtn, revertBtn);
                        valInput.addEventListener("input", updateActions);
                        valInput.addEventListener("keydown", event => {
                            if (event.key === "Enter" && !saveBtn.hidden) saveBtn.click();
                            if (event.key === "Escape") revertBtn.click();
                        });
                        function updateActions() {
                            const changed = valInput.value !== valInput.dataset.originalValue;
                            saveBtn.hidden = !changed;
                            revertBtn.hidden = !changed;
                            const secondary = actions.querySelector(".btn-reset, .btn-remove");
                            if (secondary) secondary.hidden = changed;
                        }
                    }
                    if (isGlobal && known && defaultValues[key] !== undefined && !isFactoryValue(key, val)) {
                        const resetBtn = makeAction("Sıfırla", "btn-reset", () => resetGlobalSetting(key));
                        resetBtn.title = "Fabrika varsayılanına dön";
                        actions.appendChild(resetBtn);
                    } else if (!isGlobal) {
                        const removeBtn = makeAction("Kaldır", "btn-remove", () => deleteSetting(userId, key));
                        removeBtn.title = "Kullanıcı ayarını kaldır ve global değeri kullan";
                        actions.appendChild(removeBtn);
                    }
                    tdActions.appendChild(actions);
                    tr.append(tdKey, tdVal, tdActions);
                    tbody.appendChild(tr);
                });

            if (!Object.keys(settings).length) {
                const empty = document.createElement("tr");
                empty.className = "empty-row";
                empty.innerHTML = '<td colspan="3">Bu kullanıcı için özel ayar yok.</td>';
                tbody.appendChild(empty);
            }

            if (!isGlobal) {
                const available = defaultKeys.filter(key => !(key in settings));
                if (available.length) {
                    const addRow = document.createElement("tr");
                    addRow.className = "add-row";
                    const keyCell = document.createElement("td");
                    const keySelect = document.createElement("select");
                    keySelect.className = "edit-input";
                    available.forEach(key => {
                        const option = document.createElement("option");
                        option.value = key;
                        option.textContent = key;
                        keySelect.appendChild(option);
                    });
                    keyCell.appendChild(keySelect);
                    const valueCell = document.createElement("td");
                    const valueInput = document.createElement("input");
                    valueInput.className = "edit-input";
                    valueInput.placeholder = "Yeni değer";
                    valueInput.setAttribute("aria-label", "Yeni ayar değeri");
                    const boolSelect = document.createElement("select");
                    boolSelect.className = "edit-input";
                    boolSelect.setAttribute("aria-label", "Yeni ayar değeri");
                    ["True", "False"].forEach(value => {
                        const option = document.createElement("option");
                        option.value = value;
                        option.textContent = value === "True" ? "Açık" : "Kapalı";
                        boolSelect.appendChild(option);
                    });
                    const choiceSelect = document.createElement("select");
                    choiceSelect.className = "edit-input";
                    choiceSelect.setAttribute("aria-label", "Yeni ayar değeri");
                    const inputWrap = document.createElement("div");
                    inputWrap.className = "setting-value-wrap";
                    const rangeBadge = document.createElement("span");
                    rangeBadge.className = "range-badge";
                    rangeBadge.setAttribute("aria-hidden", "true");
                    inputWrap.append(valueInput, rangeBadge);
                    const choiceHint = document.createElement("small");
                    choiceHint.className = "value-hint value-warning";
                    const actionCell = document.createElement("td");
                    actionCell.className = "actions-cell";
                    const actionWrap = document.createElement("div");
                    actionWrap.className = "row-actions";
                    const addButton = makeAction("+ Ayar ekle", "btn-add", () => {
                        const value = !boolSelect.hidden ? boolSelect.value :
                            !choiceSelect.hidden ? choiceSelect.value : valueInput.value;
                        if (!value.trim() && !["chat_title_model", "tts_voice"].includes(keySelect.value)) {
                            showToast("Bir değer girin.", true);
                            return;
                        }
                        saveSetting(userId, keySelect.value, value);
                    });
                    const updateValueControl = () => {
                        const selectedKey = keySelect.value;
                        const isBoolean = ["True", "False"].includes(defaultValues[selectedKey]);
                        const isChoice = isChoiceSetting(selectedKey);
                        inputWrap.hidden = isBoolean || isChoice;
                        valueInput.hidden = isBoolean || isChoice;
                        boolSelect.hidden = !isBoolean;
                        if (isBoolean) boolSelect.value = defaultValues[keySelect.value];
                        choiceSelect.hidden = !isChoice;
                        if (isChoice) {
                            const choices = getSettingChoices(selectedKey, effectiveSettings);
                            populateChoiceSelect(choiceSelect, selectedKey,
                                ["chat_title_model", "tts_voice"].includes(selectedKey) ? "" : (choices[0] || ""),
                                effectiveSettings);
                        }
                        addButton.disabled = isChoice && choiceSelect.disabled;
                        const constraint = settingConstraints[selectedKey];
                        valueInput.type = ["integer", "number"].includes(constraint?.type) ? "number" : "text";
                        if (valueInput.type === "number") {
                            valueInput.min = constraint.minimum;
                            valueInput.max = constraint.maximum;
                            valueInput.step = constraint.type === "integer" ? "1" : "any";
                        }
                        rangeBadge.textContent = getRangeText(selectedKey);
                        rangeBadge.hidden = !rangeBadge.textContent;
                        inputWrap.classList.toggle("has-range", !rangeBadge.hidden);
                        valueInput.title = rangeBadge.hidden ? "" : `İzin verilen aralık: ${rangeBadge.textContent}`;
                        choiceHint.textContent = isChoice && !routerCatalog ? catalogError || "Router seçenekleri alınamadı" : "";
                        choiceHint.hidden = !choiceHint.textContent;
                    };
                    keySelect.addEventListener("change", updateValueControl);
                    valueCell.append(inputWrap, boolSelect, choiceSelect, choiceHint);
                    updateValueControl();
                    actionWrap.appendChild(addButton);
                    actionCell.appendChild(actionWrap);
                    addRow.append(keyCell, valueCell, actionCell);
                    tbody.appendChild(addRow);
                }
            }
            usersContainer.appendChild(clone);
        });
        applySettingsFilter();
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
            loadActiveTab();
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
        if (silent && (chatRenameInProgress || chatsContainer.querySelector(".inline-name-input:not([hidden])"))) return;
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
            setAdminConnection(true);
            if (silent && (chatRenameInProgress || chatsContainer.querySelector(".inline-name-input:not([hidden])"))) return;
            chatsSignature = nextSignature;
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
            historyPanel.innerHTML = '<p class="placeholder">Sohbet geçmişi yükleniyor…</p>';
        }

        const listEl = historyPanel.querySelector(".chat-history-list");
        const previousScroll = silent && listEl ? listEl.scrollTop : 0;

        try {
            const res = await fetch(`/api/v1/admin/chats/${encodeURIComponent(chat.chat_id)}/history`, { headers: getHeaders() });
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
            const res = await fetch(`/api/v1/admin/chats/${encodeURIComponent(chatId)}`, { method: "DELETE", headers: getHeaders() });
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
        chatRenameInProgress = true;
        try {
            const res = await fetch(`/api/v1/admin/chats/${encodeURIComponent(chatId)}/rename`, {
                method: "PATCH",
                headers: getHeaders(),
                body: JSON.stringify({ name: newName })
            });
            if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Yeniden adlandırma başarısız.");
            nameCell.querySelector(".chat-display-name").textContent = newName || chatId.substring(0, 8) + "...";
            const chat = allChatsData.find(c => c.chat_id === chatId);
            if (chat) chat.name = newName;
            showToast("Yeniden adlandırıldı.");
            return true;
        } catch (err) {
            showToast(err.message, true);
            return false;
        } finally {
            chatRenameInProgress = false;
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
            const nameWrap = document.createElement("div");
            nameWrap.className = "chat-name-cell";
            const nameSpan = document.createElement("span");
            nameSpan.className = "chat-display-name";
            nameSpan.title = chat.chat_id;
            nameSpan.textContent = displayName;
            const editNameBtn = document.createElement("button");
            editNameBtn.type = "button";
            editNameBtn.className = "btn-chat-edit";
            editNameBtn.textContent = "Düzenle";
            editNameBtn.setAttribute("aria-label", `${displayName} sohbet adını düzenle`);
            const nameInput = document.createElement("input");
            nameInput.className = "inline-name-input";
            nameInput.value = chat.name || "";
            nameInput.maxLength = 200;
            nameInput.placeholder = "Yeni ad…";
            nameInput.hidden = true;
            const saveNameBtn = document.createElement("button");
            saveNameBtn.type = "button";
            saveNameBtn.className = "btn-sm btn-save-name";
            saveNameBtn.textContent = "Kaydet";
            saveNameBtn.hidden = true;
            const chatIdLabel = document.createElement("small");
            chatIdLabel.className = "chat-id";
            chatIdLabel.textContent = chat.chat_id;
            nameWrap.append(nameSpan, editNameBtn, nameInput, saveNameBtn);
            tdName.append(nameWrap, chatIdLabel);

            const openNameEditor = () => {
                nameInput.value = chat.name || "";
                nameSpan.hidden = true;
                editNameBtn.hidden = true;
                nameInput.hidden = false;
                saveNameBtn.hidden = false;
                nameInput.focus();
            };
            nameSpan.addEventListener("click", openNameEditor);
            editNameBtn.addEventListener("click", openNameEditor);
            saveNameBtn.addEventListener("click", async () => {
                const newName = nameInput.value.trim();
                if (!newName) { showToast("Sohbet adı boş olamaz.", true); return; }
                if (!await renameAdminChat(chat.chat_id, newName, tdName)) return;
                editNameBtn.setAttribute("aria-label", `${newName} sohbet adını düzenle`);
                nameInput.hidden = true;
                saveNameBtn.hidden = true;
                nameSpan.hidden = false;
                editNameBtn.hidden = false;
            });
            nameInput.addEventListener("keydown", async (e) => {
                if (e.key === "Enter") saveNameBtn.click();
                if (e.key === "Escape") {
                    nameInput.hidden = true;
                    saveNameBtn.hidden = true;
                    nameSpan.hidden = false;
                    editNameBtn.hidden = false;
                }
            });

            // Other cells
            const tdUser = document.createElement("td");
            const userBadge = document.createElement("span");
            userBadge.className = "user-badge";
            userBadge.textContent = chat.user_id || "-";
            tdUser.appendChild(userBadge);

            const tdStatus = document.createElement("td");
            const statusColors = { completed: "#22c55e", processing: "#f59e0b", stopped: "#94a3b8", queued: "#3b82f6", failed: "#ef4444" };
            const sc = statusColors[chat.status] || "#94a3b8";
            const statusBadge = document.createElement("span");
            statusBadge.className = "chat-status";
            statusBadge.style.color = sc;
            statusBadge.textContent = chat.status || "-";
            tdStatus.appendChild(statusBadge);

            const tdDate = document.createElement("td");
            tdDate.textContent = dateStr;
            tdDate.style.color = "#94a3b8";

            // Actions cell
            const tdActions = document.createElement("td");
            tdActions.className = "chat-actions-cell";
            const chatActions = document.createElement("div");
            chatActions.className = "chat-actions";

            const viewBtn = document.createElement("button");
            viewBtn.type = "button";
            viewBtn.className = "btn-chat-view";
            viewBtn.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg><span>Görüntüle</span>';
            viewBtn.addEventListener("click", () => loadChatHistory(chat));
            chatActions.appendChild(viewBtn);

            const delBtn = document.createElement("button");
            delBtn.type = "button";
            delBtn.className = "btn-sm btn-del";
            delBtn.textContent = "Sil";
            delBtn.addEventListener("click", () => deleteAdminChat(chat.chat_id, tr));
            chatActions.appendChild(delBtn);
            tdActions.appendChild(chatActions);

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
