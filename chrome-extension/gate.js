// TubeFilter Gate — block everything except Watch Later, search results, and direct videos

function isAllowed() {
  const path = window.location.pathname;
  const search = window.location.search;

  // Allow Watch Later playlist
  if (path === '/playlist' && search.includes('list=WL')) return true;

  // Allow direct video links
  if (path === '/watch') return true;

  // Allow search results
  if (path === '/results') return true;

  // Allow channel pages (so shared links work)
  if (path.startsWith('/@')) return true;
  if (path.startsWith('/channel/')) return true;
  if (path.startsWith('/c/')) return true;

  // Block everything else (homepage, feed, shorts, trending, subscriptions)
  return false;
}

function showGate() {
  document.documentElement.innerHTML = '';

  const gate = document.createElement('div');
  gate.id = 'tubefilter-gate';
  gate.innerHTML = `
    <div class="tf-container">
      <h1 class="tf-title">TubeFilter</h1>
      <p class="tf-message">Check your email for this week's digest.</p>
      <div class="tf-links">
        <a href="https://www.youtube.com/playlist?list=WL" class="tf-link">Watch Later</a>
        <span class="tf-divider">&middot;</span>
        <a href="https://mail.google.com" class="tf-link">Gmail</a>
      </div>
      <div class="tf-search">
        <input type="text" id="tf-search-input" placeholder="Search YouTube..." autofocus />
      </div>
      <p class="tf-hint">Press Enter to search</p>
    </div>
  `;

  document.body = document.createElement('body');
  document.body.appendChild(gate);

  const style = document.createElement('style');
  style.textContent = `
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { background: #fafafa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
    #tubefilter-gate {
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; padding: 24px;
    }
    .tf-container { text-align: center; max-width: 440px; }
    .tf-title {
      font-size: 28px; font-weight: 800; color: #111; letter-spacing: -0.5px;
      margin-bottom: 12px;
    }
    .tf-message { font-size: 18px; color: #333; margin-bottom: 24px; }
    .tf-links { margin-bottom: 32px; }
    .tf-link {
      font-size: 15px; color: #2563eb; text-decoration: none; font-weight: 500;
    }
    .tf-link:hover { text-decoration: underline; }
    .tf-divider { color: #ccc; margin: 0 12px; }
    .tf-search { margin-bottom: 8px; }
    #tf-search-input {
      width: 100%; padding: 14px 18px; font-size: 16px;
      border: 2px solid #e0e0e0; border-radius: 28px;
      outline: none; background: #fff; color: #111;
      transition: border-color 0.2s;
    }
    #tf-search-input:focus { border-color: #666; }
    .tf-hint { font-size: 12px; color: #bbb; }
  `;
  document.head.appendChild(style);

  document.getElementById('tf-search-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const q = e.target.value.trim();
      if (q) {
        window.location.href = 'https://www.youtube.com/results?search_query=' + encodeURIComponent(q);
      }
    }
  });
}

if (!isAllowed()) {
  showGate();
}

// Watch for SPA navigation
let lastUrl = location.href;
new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    if (!isAllowed()) {
      showGate();
    }
  }
}).observe(document, { subtree: true, childList: true });
