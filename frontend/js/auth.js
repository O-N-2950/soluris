const API = window.location.hostname === 'localhost' ? 'http://localhost:8000/api' : '/api';

// Check URL params for signup mode
if (new URLSearchParams(window.location.search).get('signup') === 'true') showSignup();

function showLogin() {
  document.getElementById('loginForm').style.display = 'block';
  document.getElementById('signupForm').style.display = 'none';
}
function showSignup() {
  document.getElementById('loginForm').style.display = 'none';
  document.getElementById('signupForm').style.display = 'block';
}

function showError(id, msg) {
  const el = document.getElementById(id);
  el.textContent = msg;
  el.classList.add('visible');
  setTimeout(() => el.classList.remove('visible'), 5000);
}

async function login() {
  const email = document.getElementById('loginEmail').value.trim();
  const password = document.getElementById('loginPassword').value;
  if (!email || !password) return showError('loginError', 'Veuillez remplir tous les champs.');
  try {
    const res = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    const data = await res.json();
    if (!res.ok) return showError('loginError', data.detail || 'Erreur de connexion');
    localStorage.setItem('token', data.token);
    window.location.href = '/app';
  } catch (e) {
    showError('loginError', 'Erreur réseau. Réessayez.');
  }
}

async function signup() {
  const name = document.getElementById('signupName').value.trim();
  const email = document.getElementById('signupEmail').value.trim();
  const password = document.getElementById('signupPassword').value;
  if (!name || !email || !password) return showError('signupError', 'Veuillez remplir tous les champs.');
  if (password.length < 8) return showError('signupError', 'Mot de passe : 8 caractères minimum.');
  try {
    const res = await fetch(`${API}/auth/signup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, email, password })
    });
    const data = await res.json();
    if (!res.ok) return showError('signupError', data.detail || 'Erreur lors de la création');
    localStorage.setItem('token', data.token);
    window.location.href = '/app';
  } catch (e) {
    showError('signupError', 'Erreur réseau. Réessayez.');
  }
}

// Enter key support
document.querySelectorAll('input').forEach(input => {
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      const isSignup = document.getElementById('signupForm').style.display !== 'none';
      isSignup ? signup() : login();
    }
  });
});
