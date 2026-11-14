// Small behaviours; no inline scripts are allowed by the CSP.
document.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-toggle]");
  if (toggle) {
    document.getElementById(toggle.dataset.toggle)?.classList.toggle("is-open");
  }
  const confirmButton = event.target.closest("[data-confirm]");
  if (confirmButton && !window.confirm(confirmButton.dataset.confirm)) {
    event.preventDefault();
  }
});

// Signing out or switching tenant wipes the uploads queued on this device.
// The wait is capped so a blocked delete never traps the user.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!form.matches || !form.matches("[data-purge-queue]") || form.dataset.purging) return;
  event.preventDefault();
  form.dataset.purging = "1";
  const deleted = new Promise((resolve) => {
    try {
      const req = indexedDB.deleteDatabase("ledgerline-capture");
      req.onsuccess = req.onerror = req.onblocked = () => resolve();
    } catch {
      resolve();
    }
  });
  Promise.race([deleted, new Promise((resolve) => setTimeout(resolve, 1500))]).then(() => form.submit());
});
