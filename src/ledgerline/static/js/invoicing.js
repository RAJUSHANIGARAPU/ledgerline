// Invoice line editor helpers (HTMX does the server round-trips).
document.addEventListener(
  "change",
  (event) => {
    const select = event.target.closest('select[data-role="account"]');
    if (!select) return;
    const vat = select.selectedOptions[0]?.dataset.vat;
    const target = select.closest("tr")?.querySelector('select[data-role="vat"]');
    if (vat && target && [...target.options].some((o) => o.value === vat)) {
      target.value = vat;
    }
  },
  true,
);

document.addEventListener("input", (event) => {
  const input = event.target.closest(".vat-input");
  const flag = input?.closest("tr")?.querySelector('input[name="line_vat_auto"]');
  if (flag) flag.value = "0";
});
