const LOCALES = ["en", "tr", "de", "es", "fr", "it", "pt-BR", "pt-PT", "ru", "ja", "ko", "zh-CN", "zh-TW", "ar", "fa", "he", "hi", "vi", "pl", "nl", "uk", "cs", "sv", "da", "no", "fi", "hu", "ro", "el", "bg", "sr", "hr", "sk", "id", "ms", "th", "tl", "bn", "ur", "ta", "te", "mr", "sw"];
const DEFAULT_LOCALE = "en";
const LOCALE_COOKIE = "locale";

const LOCALE_NAMES = {
  "en": "English",
  "tr": "Türkçe",
  "de": "Deutsch",
  "es": "Español",
  "fr": "Français",
  "it": "Italiano",
  "pt-BR": "Português (Brasil)",
  "pt-PT": "Português (Portugal)",
  "ru": "Русский",
  "ja": "日本語",
  "ko": "한국어",
  "zh-CN": "简体中文",
  "zh-TW": "繁體中文",
  "ar": "العربية",
  "fa": "فارسی",
  "he": "עברית",
  "hi": "हिन्दी",
  "vi": "Tiếng Việt",
  "pl": "Polski",
  "nl": "Nederlands",
  "uk": "Українська",
  "cs": "Čeština",
  "sv": "Svenska",
  "da": "Dansk",
  "no": "Norsk",
  "fi": "Suomi",
  "hu": "Magyar",
  "ro": "Română",
  "el": "Ελληνικά",
  "bg": "Български",
  "sr": "Српски",
  "hr": "Hrvatski",
  "sk": "Slovenčina",
  "id": "Indonesia",
  "ms": "Bahasa Melayu",
  "th": "ไทย",
  "tl": "Tagalog",
  "bn": "বাংলা",
  "ur": "اردو",
  "ta": "தமிழ்",
  "te": "తెలుగు",
  "mr": "मराठी",
  "sw": "Kiswahili"
};

function normalizeLocale(locale) {
  if (!locale) return DEFAULT_LOCALE;
  locale = locale.trim();
  const lowerLocale = locale.toLowerCase();
  
  if (lowerLocale === "zh" || lowerLocale === "zh-cn") return "zh-CN";
  if (lowerLocale === "zh-tw" || lowerLocale === "zh-hk") return "zh-TW";
  
  for (const loc of LOCALES) {
    if (loc.toLowerCase() === lowerLocale) return loc;
  }
  
  const prefix = lowerLocale.split("-")[0];
  for (const loc of LOCALES) {
    if (loc.toLowerCase() === prefix) return loc;
  }
  
  return DEFAULT_LOCALE;
}

function isSupportedLocale(locale) {
  return LOCALES.includes(locale);
}

(function() {
    function getStoredLanguage() {
        try {
            const lang = localStorage.getItem(LOCALE_COOKIE);
            if (lang && isSupportedLocale(lang)) return lang;
        } catch (e) {}
        return null;
    }

    function setStoredLanguage(lang) {
        try { localStorage.setItem(LOCALE_COOKIE, lang); } catch (e) {}
    }

    function loadLanguageFile(lang) {
        return new Promise((resolve) => {
            if (window.ORION_TRANSLATIONS && window.ORION_TRANSLATIONS[lang]) {
                resolve();
                return;
            }
            const script = document.createElement('script');
            script.charset = 'utf-8';
            script.src = `locales/${lang}.js`;
            script.onload = () => resolve();
            script.onerror = () => {
                console.warn(`Could not load locale: ${lang}`);
                resolve();
            };
            document.head.appendChild(script);
        });
    }

    function detectLanguage() {
        const stored = getStoredLanguage();
        if (stored) return stored;
        const browserLang = (navigator.language || navigator.userLanguage || '').toLowerCase();
        return normalizeLocale(browserLang);
    }

    window.t = function(key) {
        if (typeof window.ORION_TRANSLATIONS === 'undefined') return key;
        const dict = window.ORION_TRANSLATIONS[window.ORION_CURRENT_LANG] || window.ORION_TRANSLATIONS['en'];
        const fallback = window.ORION_TRANSLATIONS['en'];
        return dict[key] !== undefined ? dict[key] : (fallback[key] !== undefined ? fallback[key] : key);
    };

    window.setLanguage = async function(lang) {
        const normalized = normalizeLocale(lang);
        await loadLanguageFile(normalized);
        
        window.ORION_CURRENT_LANG = normalized;
        document.documentElement.lang = normalized;
        
        const rtlLocales = ["fa", "ar", "he", "ur"];
        if (rtlLocales.includes(normalized)) {
            document.documentElement.dir = "ltr";
            document.documentElement.classList.add("rtl-active");
        } else {
            document.documentElement.dir = "ltr";
            document.documentElement.classList.remove("rtl-active");
        }
        
        setStoredLanguage(normalized);

        const elements = document.querySelectorAll('[data-i18n]');
        elements.forEach(el => {
            const key = el.getAttribute('data-i18n');
            const value = window.t(key);
            if (value !== key) el.innerHTML = value;
        });

        document.dispatchEvent(new CustomEvent('orion-lang-changed', { detail: { lang: normalized } }));
    };

    window.ORION_CURRENT_LANG = detectLanguage();

    const globals = { LOCALES, DEFAULT_LOCALE, LOCALE_COOKIE, LOCALE_NAMES, normalizeLocale, isSupportedLocale };
    for (const key in globals) window[key] = globals[key];

    if (typeof exports !== 'undefined') {
        for (const key in globals) exports[key] = globals[key];
    }

    document.addEventListener('DOMContentLoaded', async () => {
        await loadLanguageFile('en');
        await window.setLanguage(window.ORION_CURRENT_LANG);
    });
})();
