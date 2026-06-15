<script lang="ts">
  let { onLogin }: { onLogin: () => void } = $props();

  let email = $state('');
  let password = $state('');
  let error = $state('');
  let loading = $state(false);

  async function submit() {
    if (!email.trim() || !password) return;
    loading = true;
    error = '';
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        error = data.detail ?? 'Ошибка входа';
        return;
      }
      // Session is set as an HttpOnly cookie by the server; nothing to store here.
      onLogin();
    } catch {
      error = 'Нет связи с сервером';
    } finally {
      loading = false;
    }
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter') submit();
  }
</script>

<main class="welcome-root">
  <header class="welcome-header">
    <div class="logo-wrap">
      <img src="/brand/shectory-logo.gif" alt="Shectory" class="logo" />
    </div>
    <div class="project-meta">
      <div class="badge">Shectory Trader</div>
      <div class="versions">FORTS ММВБ · Finam Trade API</div>
    </div>
  </header>

  <section class="welcome-content">
    <article class="info-frame">
      <h1>Вход в Shectory Trader</h1>
      <p>Платформа исполнения заявок на FORTS ММВБ через Finam Trade API.</p>
      <p>После входа откроется терминал с котировками, портфелем и управлением роботами.</p>
      <p>Торговые операции производятся от вашего торгового счёта в Finam.</p>
    </article>

    <aside class="login-frame">
      <h2>Вход</h2>
      {#if error}
        <div class="error">{error}</div>
      {/if}
      <div class="bootstrap">
        Используется единый каталог пользователей Shectory.
      </div>
      <div class="form">
        <label for="login-email">E-mail</label>
        <input
          id="login-email"
          type="email"
          bind:value={email}
          onkeydown={onKey}
          disabled={loading}
          autocomplete="username"
          inputmode="email"
          spellcheck="false"
          autocapitalize="none"
          autofocus
        />
        <label for="login-password">Пароль</label>
        <input
          id="login-password"
          type="password"
          bind:value={password}
          onkeydown={onKey}
          disabled={loading}
          autocomplete="current-password"
        />
        <button onclick={submit} disabled={loading || !email.trim() || !password}>
          {loading ? 'Вход...' : 'Войти'}
        </button>
      </div>
      <p class="help"><a href="https://shectory.ru/forgot-password" class="link">Забыл пароль?</a></p>
      <p class="policy">
        Поддержка: <a href="mailto:support@shectory.ru">support@shectory.ru</a>
      </p>
    </aside>
  </section>
</main>

<style>
  .welcome-root {
    min-height: 100vh;
    padding: 20px;
    background: radial-gradient(circle at top, #16223c 0%, #0b1220 55%);
    color: #e8efff;
    font-family: Inter, Arial, sans-serif;
    box-sizing: border-box;
  }
  .welcome-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    margin-bottom: 16px;
  }
  .logo-wrap {
    border: 1px solid #223252;
    background: #0f182a;
    border-radius: 10px;
    padding: 6px 12px;
  }
  .logo { height: 32px; width: auto; display: block; }
  .project-meta { display: grid; justify-items: end; gap: 6px; }
  .badge {
    border: 1px solid #223252;
    background: #0f182a;
    border-radius: 10px;
    padding: 10px 12px;
    font-weight: 700;
    font-size: 14px;
    color: #e8efff;
  }
  .versions { color: #8fa3c6; font-size: 12px; }
  .welcome-content {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
  }
  .info-frame, .login-frame {
    border: 1px solid #223252;
    border-radius: 12px;
    background: #121a2b;
    padding: 18px;
  }
  .info-frame { min-height: 68vh; }
  .info-frame h1 { margin: 0 0 8px; font-size: 1.4rem; color: #e8efff; }
  .info-frame p { color: #8fa3c6; margin: 0.45rem 0; }
  .login-frame h2 { margin: 0 0 8px; font-size: 1.1rem; color: #e8efff; }
  .bootstrap {
    margin: 0.8rem 0 0.3rem;
    padding: 0.6rem 0.7rem;
    border: 1px solid #223252;
    border-radius: 10px;
    color: #8fa3c6;
    font-size: 0.9rem;
  }
  .form { display: grid; gap: 10px; margin-top: 10px; }
  .form label {
    display: block;
    font-size: 0.9rem;
    color: #dbe6ff;
    margin-top: 2px;
  }
  .form input[type="email"],
  .form input[type="password"] {
    width: 100%;
    margin-top: 6px;
    border: 1px solid #223252;
    border-radius: 8px;
    background: #0d1525;
    color: #e8efff;
    padding: 10px;
    font-size: 14px;
    outline: none;
    box-sizing: border-box;
  }
  .form input:focus { border-color: #4f8cff; }
  .form button {
    margin-top: 6px;
    border: 0;
    border-radius: 8px;
    padding: 10px 12px;
    background: #4f8cff;
    color: #fff;
    font-weight: 700;
    cursor: pointer;
    font-size: 14px;
  }
  .form button:disabled { opacity: 0.5; cursor: not-allowed; }
  .form button:not(:disabled):hover { background: #3a7aee; }
  .error {
    margin: 0 0 10px;
    color: #fca5a5;
    background: #31243a;
    border: 1px solid #5b2133;
    padding: 0.6rem 0.7rem;
    border-radius: 10px;
    font-size: 0.9rem;
  }
  .help { color: #8fa3c6; margin-top: 10px; font-size: 0.9rem; }
  .link { color: #9ec1ff; text-decoration: none; }
  .link:hover { text-decoration: underline; }
  .policy { margin-top: 0.9rem; font-size: 0.84rem; color: #8fa3c6; }
  .policy a { color: #9ec1ff; text-decoration: none; }
  .policy a:hover { text-decoration: underline; }
  @media (max-width: 960px) {
    .welcome-content { grid-template-columns: 1fr; }
  }
</style>
