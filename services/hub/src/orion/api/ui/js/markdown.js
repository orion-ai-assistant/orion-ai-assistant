/* Raw Markdown stays separate from rendered text: snapshot offsets use raw lengths. */
window.MarkdownView = {
    create(source = '') {
        const element = document.createElement('div');
        element.className = 'markdown-content';
        element.markdownSource = source;
        this.render(element);
        return element;
    },
    update(element, source) {
        element.markdownSource = source;
        if (element.markdownTimer) return;
        element.markdownTimer = setTimeout(() => {
            this.render(element);
            if (element.isConnected) UI.scrollToBottom();
        }, 100);
    },
    render(element) {
        clearTimeout(element.markdownTimer);
        element.markdownTimer = null;
        const source = element.markdownSource || '';
        if (!window.marked || !window.DOMPurify) {
            element.textContent = source;
            return;
        }
        const html = marked.parse(source, {gfm: true, breaks: true, async: false});
        const fragment = DOMPurify.sanitize(html, {
            RETURN_DOM_FRAGMENT: true,
            ALLOWED_TAGS: ['p','br','hr','h1','h2','h3','h4','h5','h6','strong','b','em','i',
                'del','s','blockquote','ul','ol','li','pre','code','a','table','thead','tbody','tr','th','td'],
            ALLOWED_ATTR: ['href','title','start','colspan','rowspan'],
            ALLOW_DATA_ATTR: false,
            ALLOW_ARIA_ATTR: false
        });
        for (const link of fragment.querySelectorAll('a')) {
            const href = link.getAttribute('href') || '';
            if (!/^(https?:|mailto:)/i.test(href)) link.removeAttribute('href');
            else {
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
            }
        }
        for (const table of fragment.querySelectorAll('table')) {
            const wrapper = document.createElement('div');
            wrapper.className = 'markdown-table-scroll';
            wrapper.tabIndex = 0;
            table.replaceWith(wrapper);
            wrapper.appendChild(table);
        }
        element.replaceChildren(fragment);
    }
};
