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
