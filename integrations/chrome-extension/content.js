/**
 * LogpyClaw Browser Skill — Content Script
 * Wird in jede Seite injiziert. Leichtgewichtig — Hauptlogik ist in background.js.
 */

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'ping') {
    sendResponse({ alive: true, url: location.href, title: document.title });
    return true;
  }

  if (msg.type === 'get_text') {
    sendResponse({
      text: (document.body?.innerText || '').slice(0, 8000),
      url: location.href,
      title: document.title,
    });
    return true;
  }

  if (msg.type === 'get_meta') {
    const metas = {};
    document.querySelectorAll('meta[name], meta[property]').forEach(m => {
      const key = m.getAttribute('name') || m.getAttribute('property');
      if (key) metas[key] = m.getAttribute('content');
    });
    sendResponse({ metas, title: document.title, url: location.href });
    return true;
  }
});
