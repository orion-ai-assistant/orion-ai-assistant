/* Raw Markdown stays separate from rendered text: snapshot offsets use raw lengths. */

function parseYouTubeUrl(url) {
    if (!url || typeof url !== 'string') return null;
    let videoId = null;
    try {
        let cleanUrl = url.trim();
        if (!/^https?:\/\//i.test(cleanUrl) && (cleanUrl.includes('youtube.com') || cleanUrl.includes('youtu.be'))) {
            cleanUrl = 'https://' + cleanUrl;
        }
        const u = new URL(cleanUrl, window.location.origin || 'http://localhost');
        const host = u.hostname.toLowerCase();
        const isYtHost = host === 'youtu.be' || host.endsWith('.youtu.be') ||
                         host === 'youtube.com' || host.endsWith('.youtube.com') ||
                         host === 'youtube-nocookie.com' || host.endsWith('.youtube-nocookie.com');
        if (!isYtHost) return null;

        if (host === 'youtu.be' || host.endsWith('.youtu.be')) {
            videoId = u.pathname.slice(1).split('/')[0];
        } else if (u.pathname === '/watch') {
            videoId = u.searchParams.get('v');
        } else if (u.pathname.startsWith('/embed/')) {
            videoId = u.pathname.split('/')[2];
        } else if (u.pathname.startsWith('/v/')) {
            videoId = u.pathname.split('/')[2];
        } else if (u.pathname.startsWith('/shorts/')) {
            videoId = u.pathname.split('/')[2];
        }
    } catch {
        const match = url.match(/(?:youtu\.be\/|youtube(?:-nocookie)?\.com\/(?:watch\?.*v=|embed\/|shorts\/|v\/))([a-zA-Z0-9_-]{11})/i);
        if (match) videoId = match[1];
    }

    if (!videoId || !/^[a-zA-Z0-9_-]{11}$/.test(videoId)) return null;

    return { videoId, originalUrl: url };
}

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
            if (element.isConnected && window.UI?.scrollToBottom) UI.scrollToBottom();
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
                'del','s','blockquote','ul','ol','li','pre','code','a','table','thead','tbody','tr','th','td',
                'img','audio','video','source'],
            ALLOWED_ATTR: ['href','title','start','colspan','rowspan','src','alt','loading','decoding','width','height','controls','preload'],
            ALLOW_DATA_ATTR: false,
            ALLOW_ARIA_ATTR: false
        });

        // Replace YouTube links in bot messages with a clean, embedded player
        const links = Array.from(fragment.querySelectorAll('a'));
        for (const link of links) {
            if (!link.parentNode) continue;
            const href = link.getAttribute('href') || '';
            if (!/^(https?:|mailto:)/i.test(href)) {
                link.removeAttribute('href');
                continue;
            }

            link.target = '_blank';
            link.rel = 'noopener noreferrer';

            const ytData = parseYouTubeUrl(href);
            if (ytData) {
                const wrapper = document.createElement('div');
                wrapper.className = 'yt-video-embed';

                const iframe = document.createElement('iframe');
                iframe.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(ytData.videoId)}`;
                iframe.title = 'YouTube video player';
                iframe.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
                iframe.referrerPolicy = 'strict-origin-when-cross-origin';
                iframe.allowFullscreen = true;
                wrapper.appendChild(iframe);

                let targetToReplace = link;
                const parent = link.parentElement;
                if (parent && parent.tagName === 'P') {
                    const textWithoutLink = parent.textContent.replace(link.textContent, '').trim();
                    if (!textWithoutLink || /^["'“”«»]+$/.test(textWithoutLink)) {
                        targetToReplace = parent;
                    }
                }
                if (targetToReplace.parentNode) {
                    targetToReplace.parentNode.replaceChild(wrapper, targetToReplace);
                } else {
                    targetToReplace.replaceWith(wrapper);
                }
                continue;
            }

            // Direct audio file links
            if (/\.(mp3|wav|ogg|m4a|aac|flac)(\?.*)?$/i.test(href)) {
                const isStandalone = !link.parentElement || 
                    (link.parentElement.tagName === 'P' && link.parentElement.childNodes.length === 1);
                if (isStandalone) {
                    const audioWrapper = document.createElement('div');
                    audioWrapper.className = 'media-audio-card';
                    const audio = document.createElement('audio');
                    audio.controls = true;
                    audio.preload = 'metadata';
                    audio.src = href;
                    const title = (link.textContent.trim() !== href ? link.textContent.trim() : 'Ses Kaydı');
                    const label = document.createElement('span');
                    label.className = 'media-audio-title';
                    label.textContent = title;
                    audioWrapper.appendChild(label);
                    audioWrapper.appendChild(audio);

                    let targetToReplace = link;
                    if (link.parentElement && link.parentElement.tagName === 'P') {
                        targetToReplace = link.parentElement;
                    }
                    if (targetToReplace.parentNode) {
                        targetToReplace.parentNode.replaceChild(audioWrapper, targetToReplace);
                    } else {
                        targetToReplace.replaceWith(audioWrapper);
                    }
                    continue;
                }
            }

            link.target = '_blank';
            link.rel = 'noopener noreferrer';
        }

        // Sanitize image sources — only allow http/https URLs, block data URIs and JS
        for (const img of fragment.querySelectorAll('img')) {
            const src = img.getAttribute('src') || '';
            if (!/^https?:\/\//i.test(src)) {
                img.remove();
                continue;
            }
            img.loading = 'lazy';
            img.decoding = 'async';
            img.style.maxWidth = '100%';
            img.style.borderRadius = '10px';
            img.style.marginTop = '6px';
            img.alt = img.alt || '';
        }

        for (const table of fragment.querySelectorAll('table')) {
            const wrapper = document.createElement('div');
            wrapper.className = 'markdown-table-scroll';
            wrapper.tabIndex = 0;
            table.replaceWith(wrapper);
            wrapper.appendChild(table);
        }

        // Make ordered list numbers selectable as real text in the DOM
        for (const ol of fragment.querySelectorAll('ol')) {
            const startAttr = ol.getAttribute('start');
            let count = startAttr ? parseInt(startAttr, 10) : 1;
            if (isNaN(count)) count = 1;

            for (const child of ol.children) {
                if (child.tagName === 'LI') {
                    const numSpan = document.createElement('span');
                    numSpan.className = 'markdown-ol-number';
                    numSpan.textContent = `${count}. `;
                    const target = (child.firstElementChild && child.firstElementChild.tagName === 'P')
                        ? child.firstElementChild
                        : child;
                    target.prepend(numSpan);
                    count++;
                }
            }
        }

        element.replaceChildren(fragment);
    }
};
