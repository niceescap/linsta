/* Only editorial same-origin GET pages participate. Forms/auth/admin stay native. */
(() => {
  const compatible = url => url.origin === location.origin && /^\/(?:decouvrir|artistes|a-propos|connexion|mon-espace|espace-auditeur|morceaux\/\d+)?$/.test(url.pathname);
  let controller;
  function highlight() {
    document.querySelectorAll('#site-nav a').forEach(a => {
      if (new URL(a.href).pathname === location.pathname) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
  }
  async function navigate(url, push) {
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    try {
      const response = await fetch(url, {credentials:'same-origin', cache:'no-store', signal});
      if (!response.ok || response.redirected) throw new Error('Native navigation required');
      const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
      const content = parsed.querySelector('#content');
      if (!content || !parsed.querySelector('#editorial-audio')) throw new Error('Not editorial');
      // A changed login context needs a fresh shell; never retain authenticated audio.
      if (parsed.querySelector('#editorial-audio').dataset.enabled !== document.querySelector('#editorial-audio').dataset.enabled) throw new Error('Session changed');
      if (signal.aborted) return;
      document.querySelector('#content').replaceWith(content);
      document.title = parsed.title;
      document.body.dataset.section = content.dataset.section;
      document.querySelector('#hero').hidden = content.dataset.section !== 'radio';
      if (push) history.pushState(null, '', url);
      highlight();
      document.querySelector('#site-nav').classList.remove('open');
      document.querySelector('.menu-toggle').setAttribute('aria-expanded','false');
      window.dispatchEvent(new Event('editorial:render'));
      content.focus({preventScroll:true});
      window.scrollTo(0,0);
    } catch (error) {
      if (error.name !== 'AbortError') location.assign(url);
    }
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-nav], a[data-track-link]');
    if (!link || !link.hasAttribute('href') || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return;
    const url = new URL(link.href);
    if (!compatible(url)) return;
    if (url.pathname === location.pathname && url.hash) return;
    event.preventDefault();
    navigate(url.href, true);
  });
  window.addEventListener('popstate', () => navigate(location.href, false));
  document.querySelector('.menu-toggle').addEventListener('click', event => {
    const open = document.querySelector('#site-nav').classList.toggle('open');
    event.currentTarget.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && document.querySelector('#site-nav').classList.contains('open')) {
      document.querySelector('.menu-toggle').focus();
      document.querySelector('#site-nav').classList.remove('open');
      document.querySelector('.menu-toggle').setAttribute('aria-expanded','false');
    }
  });
  highlight();
})();
