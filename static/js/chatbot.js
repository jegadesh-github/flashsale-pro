(() => {
  const config = window.dropDealsChatConfig || {};
  const shell = document.querySelector('[data-chatbot]');
  if (!shell) return;

  const panel = shell.querySelector('[data-chatbot-panel]');
  const toggle = shell.querySelector('[data-chatbot-toggle]');
  const closeBtn = shell.querySelector('[data-chatbot-close]');
  const form = shell.querySelector('[data-chatbot-form]');
  const input = shell.querySelector('[data-chatbot-input]');
  const body = shell.querySelector('[data-chatbot-body]');
  const starters = shell.querySelectorAll('[data-chatbot-starter]');

  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    if (open) input.focus();
  }

  function appendMessage(text, role) {
    const msg = document.createElement('div');
    msg.className = `chat-msg ${role}`;
    const p = document.createElement('p');
    p.textContent = text;
    msg.appendChild(p);
    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
    return msg;
  }

  function appendDeals(deals) {
    if (!deals || !deals.length) return;
    const wrap = document.createElement('div');
    wrap.className = 'chat-deal-list';
    deals.forEach((deal) => {
      const card = document.createElement('a');
      card.className = 'chat-deal-item';
      card.href = deal.outbound_url || deal.affiliate_url || '#';
      card.target = '_blank';
      card.rel = 'noopener noreferrer';
      card.innerHTML = `
        <img src="${deal.image_url || ''}" alt="${deal.product_name || 'Deal'}">
        <div>
          <strong>${deal.product_name || 'Deal'}</strong>
          <span>${deal.category || 'Category'} | ${deal.sale_price || ''}</span>
        </div>
      `;
      wrap.appendChild(card);
    });
    body.appendChild(wrap);
    body.scrollTop = body.scrollHeight;
  }

  async function sendMessage(text) {
    if (!text || !config.endpoint) return;
    appendMessage(text, 'user');
    const pending = appendMessage('Searching for matching deals...', 'bot');

    try {
      const res = await fetch(config.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: 'dropdeals-web' })
      });
      const data = await res.json();
      pending.remove();
      appendMessage(data.reply || 'I found a few options for you.', 'bot');
      appendDeals(data.deals || []);
    } catch (error) {
      pending.remove();
      appendMessage('I could not reach the live assistant right now. Try again in a moment.', 'bot');
    }
  }

  toggle.addEventListener('click', () => setOpen(panel.hidden));
  closeBtn.addEventListener('click', () => setOpen(false));
  starters.forEach((starter) => {
    starter.addEventListener('click', () => {
      setOpen(true);
      sendMessage(starter.getAttribute('data-chatbot-starter'));
    });
  });

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    setOpen(true);
    sendMessage(text);
  });

  if (config.suggestionEndpoint) {
    fetch(config.suggestionEndpoint)
      .then((res) => res.json())
      .then((data) => {
        const suggestions = data.suggestions || [];
        if (!suggestions.length) return;
        const first = suggestions[0];
        const msg = document.createElement('div');
        msg.className = 'chat-msg bot';
        msg.innerHTML = `<p><strong>Suggestion:</strong> ${first.title}. ${first.detail}</p>`;
        body.appendChild(msg);
      })
      .catch(() => {});
  }
})();
