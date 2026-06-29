document.addEventListener('DOMContentLoaded', () => {

    // --- Scroll Reveal (Intersection Observer) ---
    const revealElements = document.querySelectorAll(".reveal");
    const revealObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add("active");
                observer.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.08,
        rootMargin: "0px 0px -30px 0px"
    });

    revealElements.forEach(el => revealObserver.observe(el));

    // --- Floating Particles ---
    function createParticles() {
        const container = document.getElementById('bg-cosmos-layer');
        if (!container) return;
        
        for (let i = 0; i < 25; i++) {
            const particle = document.createElement('div');
            particle.className = 'particle';
            particle.style.left = `${Math.random() * 100}%`;
            particle.style.top = `${Math.random() * 100}%`;
            particle.style.animationDelay = `${Math.random() * 8}s`;
            particle.style.animationDuration = `${6 + Math.random() * 6}s`;
            
            const hue = Math.random() > 0.5 ? '185' : '260';
            particle.style.background = `hsl(${hue}, 80%, 60%)`;
            particle.style.width = `${1 + Math.random() * 2}px`;
            particle.style.height = particle.style.width;
            
            container.appendChild(particle);
        }
    }
    createParticles();

    // --- OS Tab Switching ---
    const osTabs = document.querySelectorAll('.os-tab');
    const cmdPanels = document.querySelectorAll('.code-line');
    let selectedOS = 'win';

    osTabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            const os = e.currentTarget.getAttribute('data-os');
            
            // Don't switch if disabled
            if (e.currentTarget.classList.contains('disabled')) {
                return;
            }

            selectedOS = os;
            osTabs.forEach(t => t.classList.remove('active'));
            e.currentTarget.classList.add('active');

            cmdPanels.forEach(p => p.classList.remove('active'));
            const target = document.getElementById(`cmd-${os}`);
            if (target) target.classList.add('active');
        });
    });

    // --- Copy to Clipboard ---
    const commandMap = {
        'win': 'powershell -c "iex(irm raw.github.com/orion-ai-assistant/orion-ai-assistant/main/install.ps1)"',
        'mac': 'curl -sSL raw.github.com/orion-ai-assistant/orion-ai-assistant/main/install.sh | bash'
    };

    const copyBtn = document.getElementById('terminal-copy-btn');
    
    const defaultCopyIcon = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px">
            <path stroke-linecap="round" stroke-linejoin="round" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
        </svg>
    `;

    const successCopyIcon = `
        <svg viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2" style="width:14px;height:14px">
            <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
        </svg>
    `;

    if (copyBtn) {
        copyBtn.addEventListener('click', () => {
            const text = commandMap[selectedOS];
            
            navigator.clipboard.writeText(text).then(() => {
                copyBtn.innerHTML = successCopyIcon;
                setTimeout(() => {
                    copyBtn.innerHTML = defaultCopyIcon;
                }, 2200);
            }).catch(err => {
                console.error('Copy failed:', err);
            });
        });
    }

    // --- Language Selector ---
    const langContainer = document.getElementById('orion-lang-selector-container');
    const langTrigger = document.getElementById('orion-lang-selector-trigger');
    const langText = document.getElementById('current-lang-text');
    const langDropdown = document.getElementById('orion-lang-dropdown');

    if (langTrigger && langContainer && langDropdown && window.LOCALES && window.LOCALE_NAMES) {
        langDropdown.innerHTML = '';
        const sortedLocales = [...window.LOCALES].sort((a, b) => {
            const nameA = window.LOCALE_NAMES[a] || '';
            const nameB = window.LOCALE_NAMES[b] || '';
            return nameA.localeCompare(nameB, undefined, { sensitivity: 'base' });
        });

        sortedLocales.forEach(locale => {
            const btn = document.createElement('button');
            btn.className = 'lang-dropdown-item';
            btn.setAttribute('data-lang', locale);
            btn.textContent = `${window.LOCALE_NAMES[locale]} (${locale.toUpperCase()})`;
            btn.addEventListener('click', (e) => {
                const sel = e.currentTarget.getAttribute('data-lang');
                if (window.setLanguage) window.setLanguage(sel);
                langContainer.classList.remove('active');
            });
            langDropdown.appendChild(btn);
        });

        langTrigger.addEventListener('click', (e) => {
            e.stopPropagation();
            langContainer.classList.toggle('active');
        });

        document.addEventListener('click', () => {
            langContainer.classList.remove('active');
        });

        document.addEventListener('orion-lang-changed', (e) => {
            const lang = e.detail.lang;
            if (langText) langText.textContent = lang.toUpperCase();
            const items = langDropdown.querySelectorAll('.lang-dropdown-item');
            items.forEach(item => {
                item.classList.toggle('active', item.getAttribute('data-lang') === lang);
            });
        });

        if (window.ORION_CURRENT_LANG) {
            if (langText) langText.textContent = window.ORION_CURRENT_LANG.toUpperCase();
            const items = langDropdown.querySelectorAll('.lang-dropdown-item');
            items.forEach(item => {
                if (item.getAttribute('data-lang') === window.ORION_CURRENT_LANG) {
                    item.classList.add('active');
                }
            });
        }
    }

    // --- Smooth scroll for nav links ---
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            const href = this.getAttribute('href');
            if (href === '#') return;
            
            e.preventDefault();
            const target = document.querySelector(href);
            if (target) {
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    });
});
