(() => {
  const form = document.getElementById('local-login');
  if (!form) return;
  const result = document.getElementById('login-result');
  const button = form.querySelector('button');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    button.disabled = true;
    result.textContent = '';
    try {
      const fields = new FormData(form);
      const response = await fetch(form.action, {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': fields.get('csrf_token')},
        body: JSON.stringify({email: fields.get('email'), password: fields.get('password')})
      });
      if (response.ok) { window.location.assign('/submissions'); return; }
      result.textContent = response.status === 400
        ? 'Session expirée. Rechargez la page avant de réessayer.'
        : 'Connexion refusée. Vérifiez les identifiants de votre compte de test.';
    } catch (_) {
      result.textContent = 'Connexion indisponible. Réessayez dans un instant.';
    } finally {
      button.disabled = false;
    }
  });
})();
