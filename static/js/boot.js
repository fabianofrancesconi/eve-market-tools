// Resolve auth before revealing anything. <body> starts with .pre-auth so the
// app chrome is hidden from first paint — otherwise the shell flashes on screen
// for the duration of the /api/auth/status round-trip before the login landing
// can cover it. On a multi-user deploy an unauthenticated visitor gets the
// landing (and we skip loadSettings entirely, so its /api/settings,
// /api/last-scan and auto-scan calls never fire and 401); everyone else gets
// the app revealed.
(async () => {
  let st = null;
  try { st = await checkAuth(); } catch (_) {}
  if (st && st.needs_login) showLoginLanding();
  else loadSettings();
  document.body.classList.remove("pre-auth");
})();
_corpInput.addEventListener("input", updateMyLpBadge);
