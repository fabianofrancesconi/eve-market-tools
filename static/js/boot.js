// Resolve auth before touching anything else. On a multi-user deploy an
// unauthenticated visitor gets a login landing page and we skip loadSettings()
// entirely — otherwise its /api/settings, /api/last-scan and auto-scan calls
// would all 401 and leave a half-broken UI on screen.
(async () => {
  const st = await checkAuth();
  if (st && st.needs_login) { showLoginLanding(); return; }
  loadSettings();
})();
_corpInput.addEventListener("input", updateMyLpBadge);
