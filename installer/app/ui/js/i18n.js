const translations = window.orionLocales || { tr: {}, en: {} };

let currentLanguage = localStorage.getItem('orion_lang') || window.orionLang || 'en';

window.setLanguage = async function(lang) {
    if (!translations[lang]) return;
    currentLanguage = lang;
    localStorage.setItem('orion_lang', lang);
    window.orionLang = lang;
    
    // Attempt to notify backend
    try {
        await fetch('/api/system/lang', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lang: lang })
        });
    } catch (e) {
        console.warn("Failed to update backend language preference", e);
    }

    updateStaticTexts();
    
    // Trigger a custom event so other components can re-render
    window.dispatchEvent(new Event('languageChanged'));
};

window.t = function(key, ...args) {
    const langDict = translations[currentLanguage] || translations['en'] || {};
    const defaultDict = translations['en'] || {};
    
    let text = key;
    if (langDict[key] !== undefined) {
        text = langDict[key];
    } else if (defaultDict[key] !== undefined) {
        text = defaultDict[key];
    }
    
    // Simple string formatting if arguments are passed
    if (args.length > 0) {
        args.forEach((arg, i) => {
            text = text.replace(`{${i}}`, arg);
        });
    }
    return text;
};

window.t_service_name = function(service) {
    if (!service) return '';
    const nameKey = `name_${service.id.replace(/-/g, '_')}`.toLowerCase();
    const translatedName = window.t(nameKey);
    return (translatedName !== nameKey) ? translatedName : service.name;
};

window.t_service_desc = function(service) {
    if (!service) return '';
    const descKey = `desc_${service.id.replace(/-/g, '_')}`.toLowerCase();
    const translatedDesc = window.t(descKey);
    return (translatedDesc !== descKey) ? translatedDesc : service.description;
};

// Auto-translate static DOM elements
function updateStaticTexts() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        // Handle inner text or placeholders
        if (el.tagName === 'INPUT' && el.hasAttribute('placeholder')) {
            el.placeholder = window.t(key);
        } else {
            // Keep any nested elements (like icons) by only replacing text nodes or specific spans
            const textSpan = el.querySelector('.i18n-text');
            if (textSpan) {
                textSpan.innerText = window.t(key);
            } else {
                el.innerText = window.t(key);
            }
        }
    });
}

// Initial setup
document.addEventListener('DOMContentLoaded', () => {
    // Determine default language from backend global config if passed, else fallback
    if (window.orionLang && !localStorage.getItem('orion_lang')) {
        currentLanguage = window.orionLang;
    }
    updateStaticTexts();
});
