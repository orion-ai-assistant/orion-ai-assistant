// Admin Panel JavaScript

const API_BASE = '';
let currentConfig = null;
let isLoggedIn = sessionStorage.getItem('admin_logged_in') === '1';

const _nativeFetch = window.fetch;
window.fetch = function (url, options = {}) {
    options.credentials = 'include';
    return _nativeFetch(url, options).then((res) => {
        if (res.status === 401) {
            logout();
        }
        return res;
    });
};

function showLoginOverlay() {
    document.getElementById('login-overlay').style.display = 'flex';
    document.getElementById('main-app').style.display = 'none';
}

function hideLoginOverlay() {
    document.getElementById('login-overlay').style.display = 'none';
    document.getElementById('main-app').style.display = '';
}

async function login() {
    const tokenInput = document.getElementById('token-input');
    const loginError = document.getElementById('login-error');
    const token = tokenInput.value.trim();
    if (!token) return;

    try {
        const res = await _nativeFetch(`${API_BASE}/api/auth/login`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });

        if (res.ok) {
            isLoggedIn = true;
            sessionStorage.setItem('admin_logged_in', '1');
            loginError.style.display = 'none';
            hideLoginOverlay();
            initApp();
            return;
        }

        loginError.textContent = '❌ Geçersiz token';
        loginError.style.display = 'block';
        tokenInput.value = '';
        tokenInput.focus();
    } catch (error) {
        loginError.textContent = '❌ Bağlantı hatası';
        loginError.style.display = 'block';
    }
}

async function logout() {
    await _nativeFetch(`${API_BASE}/api/auth/logout`, { method: 'POST', credentials: 'include' });
    isLoggedIn = false;
    sessionStorage.removeItem('admin_logged_in');
    showLoginOverlay();
    document.getElementById('token-input').value = '';
}

let _appInitialized = false;
function initApp() {
    if (_appInitialized) return;
    _appInitialized = true;

    setupNavigation();
    loadDashboard();
    loadConfig();
    setInterval(refreshStats, 10000);

    const providerSelect = document.getElementById('provider');
    if (providerSelect) {
        providerSelect.addEventListener('change', (e) => _toggleVertexSettings(e.target.value));
    }

    const thinkingToggle = document.getElementById('thinking-toggle');
    if (thinkingToggle) {
        thinkingToggle.addEventListener('change', (e) => toggleSetting('thinking', e.target.checked));
    }

    const realAgentToggle = document.getElementById('real-agent-toggle');
    if (realAgentToggle) {
        realAgentToggle.addEventListener('change', async () => {
            await saveConfig();
        });
    }
}

function _toggleVertexSettings(provider) {
    const vertexProject = document.getElementById('vertex-settings');
    const vertexLocation = document.getElementById('vertex-location');
    if (!vertexProject || !vertexLocation) return;

    const visible = provider === 'vertexai';
    vertexProject.style.display = visible ? 'block' : 'none';
    vertexLocation.style.display = visible ? 'block' : 'none';
}

document.addEventListener('DOMContentLoaded', () => {
    if (!isLoggedIn) {
        showLoginOverlay();
        return;
    }
    initApp();
});

function setupNavigation() {
    document.querySelectorAll('.nav-btn').forEach((btn) => {
        const fresh = btn.cloneNode(true);
        btn.parentNode.replaceChild(fresh, btn);
    });

    document.querySelectorAll('.nav-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            switchTab(tab);
            document.querySelectorAll('.nav-btn').forEach((item) => item.classList.remove('active'));
            btn.classList.add('active');
        });
    });
}

function switchTab(tabName) {
    document.querySelectorAll('.tab-content').forEach((tab) => tab.classList.remove('active'));
    const activeTab = document.getElementById(`${tabName}-tab`);
    if (!activeTab) return;

    activeTab.classList.add('active');
    if (tabName === 'users') loadUsers();
    if (tabName === 'models') loadModels();
    if (tabName === 'tools') loadTools();
    if (tabName === 'agents') loadAgents();
    if (tabName === 'dashboard') loadDashboard();
    if (tabName === 'settings') loadConfig();
}

async function loadDashboard() {
    try {
        const response = await fetch(`${API_BASE}/api/stats/`);
        const data = await response.json();

        const statusBadge = document.getElementById('system-status');
        const statusDot = document.querySelector('.status-dot');
        if (data.status === 'healthy') {
            statusBadge.textContent = 'Healthy';
            statusDot.style.background = 'var(--success)';
        } else {
            statusBadge.textContent = 'Degraded';
            statusDot.style.background = 'var(--warning)';
        }

        const users = data.users || {};
        document.getElementById('total-users').textContent = users.total_users || 0;
        document.getElementById('active-users').textContent = users.active_users || 0;
        document.getElementById('total-sessions').textContent = users.total_sessions || 0;
        document.getElementById('messages-today').textContent = users.total_messages_today || 0;

        const agentReady = data.backend?.agent_ready;
        const agentStatusEl = document.getElementById('agent-status');
        agentStatusEl.textContent = agentReady ? '✅ Ready' : '⚠️ Not Ready';
        agentStatusEl.style.color = agentReady ? 'var(--success)' : 'var(--warning)';
    } catch (error) {
        console.error('Error loading dashboard:', error);
        showToast('Failed to load dashboard', 'error');
    }
}

async function refreshStats() {
    await loadDashboard();
}

function _getAgentProfile(config, agentName) {
    return config?.agents?.[agentName] || null;
}

function _createCheckbox(id, labelText, checked, changeHandler, disabled = false) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.id = id;
    input.checked = Boolean(checked);
    input.disabled = Boolean(disabled);
    input.onchange = changeHandler;
    label.appendChild(input);
    label.append(' ' + labelText);
    return label;
}

function setLoggingControlsState(enabled) {
    const levelsContainer = document.getElementById('log-level-checkboxes');
    const componentsContainer = document.getElementById('log-component-checkboxes');

    [levelsContainer, componentsContainer].forEach((container) => {
        if (!container) return;
        container.classList.toggle('logging-disabled', !enabled);
        container.querySelectorAll('input[type=checkbox]').forEach((el) => {
            el.disabled = !enabled;
        });
    });
}

function renderLogLevelControls(levels) {
    const container = document.getElementById('log-level-checkboxes');
    if (!container) return;

    const defaultLevels = Object.keys(levels || {}).length ? levels : { DEBUG: true, INFO: true, WARNING: true, ERROR: true };
    const logEnabled = Boolean(document.getElementById('log-enabled')?.checked);

    container.innerHTML = '';
    Object.keys(defaultLevels).forEach((level) => {
        const id = `log-level-${level.toLowerCase()}`;
        container.appendChild(_createCheckbox(id, level.toUpperCase(), defaultLevels[level], saveLogSettings, !logEnabled));
    });
}

function renderLogComponentControls(components) {
    const container = document.getElementById('log-component-checkboxes');
    if (!container) return;

    container.innerHTML = '';
    const logEnabled = Boolean(document.getElementById('log-enabled')?.checked);
    const tree = { children: {} };

    Object.keys(components || {})
        .filter((c) => c.toLowerCase() !== 'all')
        .sort()
        .forEach((key) => {
            const parts = key.toLowerCase().split('/');
            let current = tree;
            parts.forEach((part, idx) => {
                if (!current.children[part]) {
                    current.children[part] = { name: part, children: {}, isComponent: false, path: parts.slice(0, idx + 1).join('/') };
                }
                current = current.children[part];
                if (idx === parts.length - 1) {
                    current.isComponent = true;
                    current.originalKey = key;
                    current.checked = components[key];
                }
            });
        });

    const root = document.createElement('div');
    root.className = 'log-tree-root';

    function renderNode(node, parent) {
        const entries = Object.values(node.children).sort((a, b) => {
            const aHas = Object.keys(a.children).length > 0;
            const bHas = Object.keys(b.children).length > 0;
            if (aHas && !bHas) return -1;
            if (!aHas && bHas) return 1;
            return a.name.localeCompare(b.name);
        });

        entries.forEach((child) => {
            const item = document.createElement('div');
            item.className = 'log-tree-item';
            const row = document.createElement('div');
            row.className = 'log-tree-row';
            const icon = document.createElement('span');
            icon.className = 'log-tree-icon';
            icon.innerHTML = Object.keys(child.children).length > 0 ? '📂' : (child.isComponent ? '🐍' : '📄');
            row.appendChild(icon);

            if (child.isComponent) {
                const id = `log-comp-${child.originalKey.replace(/[^a-z0-9]/g, '-')}`;
                const label = _createCheckbox(id, child.name, child.checked, saveLogSettings, !logEnabled);
                label.classList.add('log-tree-label');
                label.dataset.component = child.originalKey;
                row.appendChild(label);
            } else {
                const text = document.createElement('span');
                text.className = 'log-tree-folder-text';
                text.textContent = child.name;
                row.appendChild(text);
            }

            item.appendChild(row);
            if (Object.keys(child.children).length > 0) {
                const childContainer = document.createElement('div');
                childContainer.className = 'log-tree-children';
                renderNode(child, childContainer);
                item.appendChild(childContainer);
            }
            parent.appendChild(item);
        });
    }

    renderNode(tree, root);
    container.appendChild(root);
}

async function loadConfig() {
    try {
        const response = await fetch(`${API_BASE}/api/config/`);
        currentConfig = await response.json();

        const thinkingToggle = document.getElementById('thinking-toggle');
        const realAgentToggle = document.getElementById('real-agent-toggle');
        const maxHistoryInput = document.getElementById('max-history');
        const rateLimitInput = document.getElementById('rate-limit');
        const providerSelect = document.getElementById('provider');
        const vertexProjectInput = document.getElementById('vertex-project');
        const vertexLocationInput = document.getElementById('vertex-location-input');
        const googleApiKeyInput = document.getElementById('google-api-key');

        if (thinkingToggle) {
            thinkingToggle.checked = Boolean(currentConfig.thinking_enabled);
        }
        if (realAgentToggle) {
            realAgentToggle.checked = Boolean(currentConfig.ws_enable_real_agent);
        }
        if (maxHistoryInput) {
            maxHistoryInput.value = currentConfig.max_conversation_history;
        }
        if (rateLimitInput) {
            rateLimitInput.value = currentConfig.rate_limit_per_user;
        }

        const provider = currentConfig.provider || 'google_genai';
        if (providerSelect) {
            providerSelect.value = provider;
        }
        if (vertexProjectInput) {
            vertexProjectInput.value = currentConfig.vertex_project || '';
        }
        if (vertexLocationInput) {
            vertexLocationInput.value = currentConfig.vertex_location || '';
        }
        if (googleApiKeyInput) {
            googleApiKeyInput.value = currentConfig.google_api_key || '';
        }
        _toggleVertexSettings(provider);

        const logSettings = currentConfig.log_settings || { enabled: true, levels: {}, components: {} };
        const logEnabledEl = document.getElementById('log-enabled');
        if (logEnabledEl) {
            logEnabledEl.checked = Boolean(logSettings.enabled);
            logEnabledEl.onchange = async () => {
                setLoggingControlsState(logEnabledEl.checked);
                await saveLogSettings();
            };
        }

        renderLogLevelControls(logSettings.levels || {});
        renderLogComponentControls(logSettings.components || {});
        setLoggingControlsState(Boolean(logSettings.enabled));

        if (document.getElementById('agent-selector')) {
            _populateAgentEditorSelect(currentConfig);
        }

        document.getElementById('last-updated').textContent = new Date(currentConfig.last_updated).toLocaleString();
        document.getElementById('updated-by').textContent = currentConfig.updated_by || 'system';
    } catch (error) {
        console.error('Error loading config:', error);
        showToast('Failed to load configuration', 'error');
    }
}

async function saveLogSettings() {
    try {
        const levels = {};
        document.querySelectorAll('#log-level-checkboxes input[type=checkbox]').forEach((el) => {
            const key = el.id.replace(/^log-level-/, '').toUpperCase();
            levels[key] = el.checked;
        });

        const components = {};
        document.querySelectorAll('#log-component-checkboxes input[type=checkbox]').forEach((el) => {
            const key = (el.closest('label')?.dataset.component || el.id.replace(/^log-comp-/, '')).toLowerCase();
            components[key] = el.checked;
        });

        const logEnabledEl = document.getElementById('log-enabled');
        const updatedConfig = {
            ...currentConfig,
            log_settings: {
                enabled: logEnabledEl ? logEnabledEl.checked : true,
                levels,
                components,
            },
            updated_by: 'admin',
            last_updated: new Date().toISOString(),
        };

        const response = await fetch(`${API_BASE}/api/config/`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updatedConfig),
        });

        if (response.ok) {
            await loadConfig();
            showToast('Logging settings saved', 'success');
        } else {
            throw new Error('Failed to save logging settings');
        }
    } catch (error) {
        console.error('Error saving log settings:', error);
        showToast('Failed to save logging settings', 'error');
    }
}

async function saveConfig() {
    try {
        const providerEl = document.getElementById('provider');
        const vertexProjectEl = document.getElementById('vertex-project');
        const vertexLocationEl = document.getElementById('vertex-location-input');
        const googleApiKeyEl = document.getElementById('google-api-key');
        const thinkingToggleEl = document.getElementById('thinking-toggle');
        const realAgentToggleEl = document.getElementById('real-agent-toggle');
        const maxHistoryEl = document.getElementById('max-history');
        const rateLimitEl = document.getElementById('rate-limit');

        const updatedConfig = {
            ...currentConfig,
            provider: providerEl ? providerEl.value : currentConfig.provider,
            vertex_project: vertexProjectEl ? vertexProjectEl.value : currentConfig.vertex_project,
            vertex_location: vertexLocationEl ? vertexLocationEl.value : currentConfig.vertex_location,
            google_api_key: googleApiKeyEl ? googleApiKeyEl.value : currentConfig.google_api_key,
            thinking_enabled: thinkingToggleEl ? thinkingToggleEl.checked : Boolean(currentConfig.thinking_enabled),
            ws_enable_real_agent: realAgentToggleEl ? realAgentToggleEl.checked : Boolean(currentConfig.ws_enable_real_agent),
            max_conversation_history: maxHistoryEl ? Number(maxHistoryEl.value) || 0 : currentConfig.max_conversation_history,
            rate_limit_per_user: rateLimitEl ? Number(rateLimitEl.value) || 0 : currentConfig.rate_limit_per_user,
            updated_by: 'admin',
            last_updated: new Date().toISOString(),
        };

        const response = await fetch(`${API_BASE}/api/config/`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updatedConfig),
        });

        if (response.ok) {
            await loadConfig();
            showToast('AI configuration saved', 'success');
        } else {
            throw new Error('Failed to save configuration');
        }
    } catch (error) {
        console.error('Error saving config:', error);
        showToast('Failed to save configuration', 'error');
    }
}

function _getAgentToolList(agentProfile) {
    return Array.isArray(agentProfile?.tools) ? agentProfile.tools.filter((tool) => typeof tool === 'string' && tool) : [];
}

function _renderAgentToolCheckboxes(agentProfile) {
    const container = document.getElementById('agent-tools-checkboxes');
    if (!container) return;

    const globalTools = currentConfig?.tools || [];
    const configured = _getAgentToolList(agentProfile);
    container.innerHTML = '';

    if (!globalTools.length) {
        container.innerHTML = '<div class="loading">No global tools configured</div>';
        return;
    }

    globalTools.forEach((tool) => {
        const label = document.createElement('label');
        label.style.display = 'inline-flex';
        label.style.alignItems = 'center';
        label.style.gap = '.35rem';
        label.style.marginRight = '.85rem';
        label.style.marginBottom = '.5rem';

        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = tool.name;
        input.checked = configured.includes(tool.name);

        const text = document.createElement('span');
        text.textContent = tool.name;

        label.appendChild(input);
        label.appendChild(text);
        container.appendChild(label);
    });
}

function _populateAgentEditor(agentName) {
    const profile = _getAgentProfile(currentConfig, agentName);
    if (!profile) return;

    const selectedName = document.getElementById('agent-selected-name');
    if (selectedName) selectedName.textContent = agentName;

    document.getElementById('agent-display-name').value = profile.display_name || '';
    document.getElementById('agent-description').value = profile.description || '';
    document.getElementById('agent-system-prompt').value = profile.system_prompt || '';
    document.getElementById('agent-graph').value = profile.graph || '';

    const modelSelect = document.getElementById('agent-model');
    if (modelSelect && currentConfig?.models) {
        modelSelect.innerHTML = '';
        currentConfig.models.forEach((model) => {
            const option = document.createElement('option');
            option.value = model.name;
            option.textContent = model.name;
            modelSelect.appendChild(option);
        });
        modelSelect.value = profile.model || (currentConfig.models[0] && currentConfig.models[0].name) || '';
    }

    _renderAgentToolCheckboxes(profile);
}

function _populateAgentEditorSelect(config) {
    const select = document.getElementById('agent-selector');
    if (!select) return;

    const agents = config?.agents || {};
    const agentKeys = Object.keys(agents);
    select.innerHTML = '';

    if (!agentKeys.length) {
        select.innerHTML = '<option value="">(No agents configured)</option>';
        return;
    }

    agentKeys.forEach((key) => {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = agents[key]?.display_name || key;
        select.appendChild(option);
    });

    const active = config.active_agent || agentKeys[0];
    select.value = active;
    _populateAgentEditor(active);

    const activeName = document.getElementById('agent-active-name');
    if (activeName) activeName.textContent = active;

    select.onchange = () => {
        _populateAgentEditor(select.value);
        const selectedName = document.getElementById('agent-selected-name');
        if (selectedName) selectedName.textContent = select.value;
    };
}

async function loadAgents() {
    try {
        if (!currentConfig) {
            const response = await fetch(`${API_BASE}/api/config/`);
            currentConfig = await response.json();
        }
        _populateAgentEditorSelect(currentConfig);
    } catch (error) {
        console.error('Error loading agents:', error);
        showToast('Failed to load agents', 'error');
    }
}

async function saveAgentConfig() {
    try {
        const agentName = document.getElementById('agent-selector').value;
        const agentProfile = _getAgentProfile(currentConfig, agentName) || {};

        const selectedTools = [];
        document.querySelectorAll('#agent-tools-checkboxes input[type=checkbox]').forEach((el) => {
            if (el.checked) selectedTools.push(el.value);
        });

        const updatedAgents = {
            ...(currentConfig.agents || {}),
            [agentName]: {
                ...agentProfile,
                display_name: document.getElementById('agent-display-name').value || null,
                description: document.getElementById('agent-description').value || null,
                system_prompt: document.getElementById('agent-system-prompt').value || null,
                model: document.getElementById('agent-model').value || null,
                graph: document.getElementById('agent-graph').value || null,
                tools: selectedTools,
            },
        };

        const updatedConfig = {
            ...currentConfig,
            agents: updatedAgents,
            updated_by: 'admin',
            last_updated: new Date().toISOString(),
        };

        const response = await fetch(`${API_BASE}/api/config/`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updatedConfig),
        });

        if (response.ok) {
            await loadConfig();
            await loadAgents();
            showToast('Agent configuration saved', 'success');
        } else {
            throw new Error('Failed to save agent configuration');
        }
    } catch (error) {
        console.error('Error saving agent config:', error);
        showToast('Failed to save agent configuration', 'error');
    }
}

async function setSelectedAgentAsActive() {
    try {
        const agentName = document.getElementById('agent-selector').value;
        const updatedConfig = {
            ...currentConfig,
            active_agent: agentName,
            updated_by: 'admin',
            last_updated: new Date().toISOString(),
        };

        const response = await fetch(`${API_BASE}/api/config/`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updatedConfig),
        });

        if (response.ok) {
            await loadConfig();
            await loadAgents();
            showToast(`Active agent set to ${agentName}`, 'success');
        } else {
            throw new Error('Failed to set active agent');
        }
    } catch (error) {
        console.error('Error setting active agent:', error);
        showToast('Failed to set active agent', 'error');
    }
}

async function resetConfig() {
    if (!confirm('Are you sure you want to reset to default configuration?')) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/config/reset?admin_name=admin`, { method: 'POST' });
        if (response.ok) {
            await loadConfig();
            showToast('Configuration reset to default', 'success');
        } else {
            throw new Error('Failed to reset configuration');
        }
    } catch (error) {
        console.error('Error resetting config:', error);
        showToast('Failed to reset configuration', 'error');
    }
}

async function toggleSetting(setting, enabled) {
    try {
        if (setting !== 'thinking') return;
        const response = await fetch(`${API_BASE}/api/config/thinking/toggle?enabled=${enabled}&admin_name=admin`, { method: 'POST' });
        if (response.ok) {
            await response.json();
            showToast(`${setting} mode ${enabled ? 'enabled' : 'disabled'}`, 'success');
        } else {
            throw new Error(`Failed to toggle ${setting}`);
        }
    } catch (error) {
        console.error(`Error toggling ${setting}:`, error);
        showToast(`Failed to toggle ${setting}`, 'error');
        const toggleEl = document.getElementById(`${setting}-toggle`);
        if (toggleEl) toggleEl.checked = !enabled;
    }
}

// Users
async function loadUsers() {
    const usersList = document.getElementById('users-list');
    usersList.innerHTML = '<div class="loading">Loading users...</div>';

    try {
        const response = await fetch(`${API_BASE}/api/users/`);
        const users = await response.json();

        if (!users || users.length === 0) {
            usersList.innerHTML = '<div class="loading">No active users</div>';
            return;
        }

        usersList.innerHTML = users.map((user) => `
            <div class="user-card">
                <div class="user-header">
                    <div class="user-id">👤 ${user.user_id}</div>
                    <span class="badge ${user.is_active ? 'badge-success' : 'badge-secondary'}">${user.is_active ? 'Active' : 'Inactive'}</span>
                </div>
                <div>
                    <strong>📱 Devices:</strong> ${user.devices.length}
                    <strong style="margin-left: 2rem;">💬 Messages:</strong> ${user.total_messages}
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Error loading users:', error);
        usersList.innerHTML = '<div class="loading">Failed to load users</div>';
    }
}

// Models
async function loadModels() {
    const modelsList = document.getElementById('models-list');
    modelsList.innerHTML = '<div class="loading">Loading models...</div>';

    try {
        const response = await fetch(`${API_BASE}/api/config/`);
        const config = await response.json();

        if (!config.models || config.models.length === 0) {
            modelsList.innerHTML = '<div class="loading">No models configured</div>';
            return;
        }

        modelsList.innerHTML = config.models.map((model) => `
            <div class="model-card">
                <div class="model-header">
                    <div>
                        <div class="model-name">🤖 ${model.name}</div>
                        <small style="color: var(--text-secondary);">${model.provider || ''}</small>
                    </div>
                    <div>
                        ${model.is_default ? '<span class="badge badge-warning">Default</span>' : ''}
                        <span class="badge ${model.enabled ? 'badge-success' : 'badge-secondary'}">${model.enabled ? 'Enabled' : 'Disabled'}</span>
                    </div>
                </div>
                <div style="margin-top: 1rem;">
                    <button class="btn btn-primary btn-sm" onclick="toggleModel('${model.name}', ${!model.enabled})">${model.enabled ? 'Disable' : 'Enable'}</button>
                    ${!model.is_default ? `<button class="btn btn-secondary btn-sm" onclick="setDefaultModel('${model.name}')">Set as Default</button>` : ''}
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Error loading models:', error);
        modelsList.innerHTML = '<div class="loading">Failed to load models</div>';
    }
}

async function toggleModel(modelName, enabled) {
    try {
        const response = await fetch(`${API_BASE}/api/config/model/${encodeURIComponent(modelName)}/toggle?enabled=${enabled}&admin_name=admin`, { method: 'POST' });
        if (response.ok) {
            showToast(`Model ${modelName} ${enabled ? 'enabled' : 'disabled'}`, 'success');
            await loadModels();
        } else {
            throw new Error('Failed to toggle model');
        }
    } catch (error) {
        console.error('Error toggling model:', error);
        showToast('Failed to toggle model', 'error');
    }
}

async function setDefaultModel(modelName) {
    try {
        const response = await fetch(`${API_BASE}/api/config/model/${encodeURIComponent(modelName)}/default?admin_name=admin`, { method: 'POST' });
        if (response.ok) {
            showToast(`${modelName} set as default`, 'success');
            await loadModels();
        } else {
            throw new Error('Failed to set default model');
        }
    } catch (error) {
        console.error('Error setting default model:', error);
        showToast('Failed to set default model', 'error');
    }
}

// Tools
async function loadTools() {
    const toolsList = document.getElementById('tools-list');
    toolsList.innerHTML = '<div class="loading">Loading tools...</div>';

    try {
        const response = await fetch(`${API_BASE}/api/config/`);
        const config = await response.json();

        if (!config.tools || config.tools.length === 0) {
            toolsList.innerHTML = '<div class="loading">No tools configured</div>';
            return;
        }

        toolsList.innerHTML = config.tools.map((tool) => `
            <div class="model-card">
                <div class="model-header">
                    <div>
                        <div class="model-name">🔧 ${tool.name}</div>
                        <small style="color: var(--text-secondary);">${tool.description || ''}</small>
                    </div>
                    <div>
                        <span class="badge ${tool.enabled ? 'badge-success' : 'badge-secondary'}">${tool.enabled ? 'Enabled' : 'Disabled'}</span>
                    </div>
                </div>
                <div style="margin-top: 1rem;">
                    <button class="btn ${tool.enabled ? 'btn-danger' : 'btn-primary'} btn-sm" onclick="toggleTool('${tool.name}', ${!tool.enabled})">${tool.enabled ? 'Disable' : 'Enable'}</button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Error loading tools:', error);
        toolsList.innerHTML = '<div class="loading">Failed to load tools</div>';
    }
}

async function toggleTool(toolName, enabled) {
    try {
        const response = await fetch(`${API_BASE}/api/config/tool/${encodeURIComponent(toolName)}/toggle?enabled=${enabled}&admin_name=admin`, { method: 'POST' });
        if (response.ok) {
            showToast(`Tool ${toolName} ${enabled ? 'enabled' : 'disabled'}`, 'success');
            await loadTools();
        } else {
            throw new Error('Failed to toggle tool');
        }
    } catch (error) {
        console.error('Error toggling tool:', error);
        showToast('Failed to toggle tool', 'error');
    }
}

function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type} show`;
    setTimeout(() => toast.classList.remove('show'), 3000);
}

function viewLogs() {
    showToast('Logs viewer coming soon...', 'success');
}
