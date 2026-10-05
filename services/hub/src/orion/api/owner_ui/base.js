/* Same management UI works at /panel and on the direct owner listener. */
(() => {
    const source = new URL(document.currentScript.src);
    const base = source.pathname.slice(0, source.pathname.lastIndexOf('/owner-assets/'));
    window.OwnerAPI = {
        base,
        url(path) { return path.startsWith('/') && !path.startsWith('//') ? base + path : path; },
        fetch(path, options) { return window.fetch(this.url(path), options); }
    };
})();
