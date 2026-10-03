document.addEventListener('DOMContentLoaded', () => {
    const media = window.matchMedia('(max-width: 760px)');
    const sidebar = document.getElementById('app-sidebar');
    const main = document.querySelector('.main-content');
    const toggle = document.getElementById('mobile-menu-toggle');
    const closeButton = document.getElementById('mobile-sidebar-close');
    const backdrop = document.getElementById('sidebar-backdrop');
    const setOpen = (open, restoreFocus = true) => {
        open = open && media.matches;
        document.body.classList.toggle('sidebar-open', open);
        toggle.setAttribute('aria-expanded', String(open));
        sidebar.inert = media.matches && !open;
        main.inert = open;
        if (open) closeButton.focus();
        else if (restoreFocus && media.matches) toggle.focus();
    };
    toggle.addEventListener('click', () => setOpen(!document.body.classList.contains('sidebar-open')));
    closeButton.addEventListener('click', () => setOpen(false));
    backdrop.addEventListener('click', () => setOpen(false));
    sidebar.addEventListener('click', event => {
        if (event.target.closest('.tab-btn, .chat-item, #new-chat-btn, #logout-btn')) setOpen(false);
    });
    document.addEventListener('keydown', event => {
        if (!media.matches || !document.body.classList.contains('sidebar-open')) return;
        if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
        if (event.key === 'Tab') {
            const focusable = [...sidebar.querySelectorAll('button, a[href], input, [tabindex="0"]')]
                .filter(element => !element.disabled && element.getClientRects().length);
            const first = focusable[0], last = focusable.at(-1);
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
    });
    const resize = () => {
        if (!media.matches) {
            document.documentElement.style.removeProperty('--mobile-height');
            document.documentElement.style.removeProperty('--mobile-top');
            return;
        }
        const viewport = window.visualViewport;
        if (viewport && viewport.scale !== 1) return;
        document.documentElement.style.setProperty('--mobile-height', `${viewport?.height || window.innerHeight}px`);
        document.documentElement.style.setProperty('--mobile-top', `${viewport?.offsetTop || 0}px`);
    };
    media.addEventListener('change', () => { setOpen(false, false); resize(); });
    window.addEventListener('resize', resize);
    window.visualViewport?.addEventListener('resize', resize);
    window.visualViewport?.addEventListener('scroll', resize);
    setOpen(false, false);
    resize();
});
