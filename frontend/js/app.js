const API = window.location.hostname === 'localhost' ? 'http://localhost:8000/api' : '/api';

// State
let conversationId = null;
let messages = [];
let isStreaming = false;

// Auth
function getToken() { return localStorage.getItem('token'); }
function headers() { return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getToken()}` }; }

// Filters — persist in localStorage
function saveFilters() {
  const j = document.getElementById('filterJurisdiction');
  const d = document.getElementById('filterDomain');
  if (j) localStorage.setItem('soluris_jurisdiction', j.value);
  if (d) localStorage.setItem('soluris_domain', d.value);
}
function loadFilters() {
  const j = document.getElementById('filterJurisdiction');
  const d = document.getElementById('filterDomain');
  if (j) j.value = localStorage.getItem('soluris_jurisdiction') || '';
  if (d) d.value = localStorage.getItem('soluris_domain') || '';
}
function getFilters() {
  const j = document.getElementById('filterJurisdiction');
  const d = document.getElementById('filterDomain');
  return {
    jurisdiction: j?.value || null,
    legal_domain: d?.value || null,
  };
}

// Init
document.addEventListener('DOMContentLoaded', async () => {
  if (!getToken()) return window.location.href = '/login';
  loadFilters();
  await loadUser();
  await loadConversations();
});

// Load user info + quota display
async function loadUser() {
  try {
    const res = await fetch(`${API}/auth/me`, { headers: headers() });
    if (res.status === 401) return window.location.href = '/login';
    const user = await res.json();
    const el = document.getElementById('userInfo');
    const initials = (user.name || 'U').split(' ').map(w => w[0]).join('').substring(0, 2).toUpperCase();
    
    // Show user + plan info
    let planBadge = '';
    if (user.plan === 'trial') {
      const daysLeft = user.trial_days_left || '?';
      planBadge = `<span style="font-size:0.7rem;color:var(--gold);opacity:0.8">Essai · ${daysLeft}j restants</span>`;
    } else if (user.plan) {
      planBadge = `<span style="font-size:0.7rem;color:var(--text-secondary);opacity:0.8">${user.plan}</span>`;
    }
    
    el.innerHTML = `<div class="avatar">${initials}</div><div style="display:flex;flex-direction:column;gap:2px"><span>${user.name}</span>${planBadge}</div>`;
  } catch (e) { console.error('User load error:', e); }
}

// Conversations
async function loadConversations() {
  try {
    const res = await fetch(`${API}/conversations`, { headers: headers() });
    if (!res.ok) return;
    const convs = await res.json();
    renderConversations(convs);
  } catch (e) { console.error('Conv load error:', e); }
}

function renderConversations(convs) {
  const list = document.getElementById('conversationsList');
  if (!convs.length) { list.innerHTML = '<div style="padding:16px;color:var(--text-muted);font-size:0.82rem;text-align:center">Aucune conversation</div>'; return; }

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today - 86400000);
  const weekAgo = new Date(today - 7 * 86400000);

  const groups = { "Aujourd'hui": [], "Hier": [], "Cette semaine": [], "Plus ancien": [] };
  convs.forEach(c => {
    const d = new Date(c.updated_at);
    if (d >= today) groups["Aujourd'hui"].push(c);
    else if (d >= yesterday) groups["Hier"].push(c);
    else if (d >= weekAgo) groups["Cette semaine"].push(c);
    else groups["Plus ancien"].push(c);
  });

  list.innerHTML = '';
  for (const [label, items] of Object.entries(groups)) {
    if (!items.length) continue;
    list.innerHTML += `<div class="conv-group-label">${label}</div>`;
    items.forEach(c => {
      const active = c.id === conversationId ? 'active' : '';
      list.innerHTML += `<div class="conv-item ${active}" onclick="loadConversation('${c.id}')" title="${c.title}">💬 ${c.title || 'Conversation'}</div>`;
    });
  }
}

async function loadConversation(id) {
  conversationId = id;
  try {
    const res = await fetch(`${API}/conversations/${id}/messages`, { headers: headers() });
    if (!res.ok) return;
    messages = await res.json();
    showMessages();
    loadConversations();
  } catch (e) { console.error('Load conv error:', e); }
}

// Display
function showMessages() {
  document.getElementById('welcomeScreen').style.display = 'none';
  const area = document.getElementById('messagesArea');
  area.classList.add('active');
  area.innerHTML = '';
  messages.forEach(m => appendMessage(m.role, m.content, m.sources));
  area.scrollTop = area.scrollHeight;
}

function appendMessage(role, content, sources) {
  const area = document.getElementById('messagesArea');
  const isUser = role === 'user';
  const avatar = isUser ? (document.querySelector('.avatar')?.textContent || 'U') : 'S';

  let html = `<div class="message ${role}">
    <div class="msg-avatar" ${!isUser ? 'style="background:var(--gradient-brand)"' : ''}>${avatar}</div>
    <div class="msg-content">${isUser ? escapeHtml(content) : renderMarkdown(content)}`;

  if (sources && sources.length) {
    html += '<div class="msg-sources">';
    sources.forEach(s => {
      const url = s.url || '#';
      const label = s.reference || s.title || 'Source';
      html += `<a href="${url}" target="_blank" class="source-tag" title="${s.title || ''}">📄 ${label}</a>`;
    });
    html += '</div>';
  }
  html += '</div></div>';
  area.insertAdjacentHTML('beforeend', html);
  area.scrollTop = area.scrollHeight;
}

// Update quota display after each message
function updateQuotaDisplay(quota) {
  if (!quota) return;
  const el = document.getElementById('quotaDisplay');
  if (!el) return;
  if (quota.limit === -1) {
    el.textContent = `${quota.used} requêtes · ${quota.plan}`;
  } else {
    el.textContent = `${quota.used}/${quota.limit} requêtes · ${quota.plan}`;
  }
}

// Send message
async function sendMessage() {
  const input = document.getElementById('messageInput');
  const text = input.value.trim();
  if (!text || isStreaming) return;

  document.getElementById('welcomeScreen').style.display = 'none';
  document.getElementById('messagesArea').classList.add('active');

  appendMessage('user', text, null);
  messages.push({ role: 'user', content: text });
  input.value = '';
  autoResize(input);

  isStreaming = true;
  document.getElementById('sendBtn').disabled = true;
  document.getElementById('typingIndicator').classList.add('active');

  try {
    const filters = getFilters();
    const res = await fetch(`${API}/chat`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({
        message: text,
        conversation_id: conversationId,
        history: messages.slice(-10),
        jurisdiction: filters.jurisdiction || null,
        legal_domain: filters.legal_domain || null,
      })
    });

    if (res.status === 401) return window.location.href = '/login';
    if (res.status === 402) {
      appendMessage('assistant', '⏰ Votre essai gratuit est terminé. [Passez à un abonnement](/pricing) pour continuer à utiliser Soluris.', null);
      return;
    }
    if (res.status === 429) {
      appendMessage('assistant', '📊 Vous avez atteint votre quota mensuel de requêtes. Passez au plan supérieur pour continuer.', null);
      return;
    }
    if (!res.ok) { const err = await res.json(); throw new Error(err.detail || 'Erreur'); }

    const data = await res.json();
    conversationId = data.conversation_id;
    appendMessage('assistant', data.response, data.sources);
    messages.push({ role: 'assistant', content: data.response, sources: data.sources });
    
    // Update quota display
    if (data.quota) updateQuotaDisplay(data.quota);
    
    // Show confidence indicator
    if (data.confidence === 'none') {
      // No RAG sources were found
    }
    
    loadConversations();
  } catch (e) {
    appendMessage('assistant', `⚠️ Erreur : ${e.message}. Veuillez réessayer.`, null);
  } finally {
    isStreaming = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('typingIndicator').classList.remove('active');
  }
}

function sendPrompt(el) {
  document.getElementById('messageInput').value = el.textContent.replace(el.querySelector('.prompt-title')?.textContent || '', '').trim();
  sendMessage();
}

function newChat() {
  conversationId = null;
  messages = [];
  document.getElementById('welcomeScreen').style.display = '';
  document.getElementById('messagesArea').classList.remove('active');
  document.getElementById('messagesArea').innerHTML = '';
  document.getElementById('messageInput').value = '';
  loadConversations();
}

// Helpers
function handleKeyDown(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}
function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}
function escapeHtml(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function renderMarkdown(text) {
  return text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>')
    .replace(/^\- (.+)/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>')
    .replace(/^(.+)$/, '<p>$1</p>');
}
