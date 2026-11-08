// Inbox: show the new-supplier fields only when that option is chosen; drag-and-drop onto the file input.
function syncSupplier() {
  const select = document.getElementById("contact-select");
  const box = document.getElementById("new-supplier");
  if (select && box) box.hidden = select.value !== "new";
}

document.addEventListener("change", (event) => {
  if (event.target.id === "contact-select") syncSupplier();
});
document.addEventListener("DOMContentLoaded", syncSupplier);

const zone = document.getElementById("drop-zone");
if (zone) {
  const input = zone.querySelector('input[type="file"]');
  zone.addEventListener("dragover", (event) => event.preventDefault());
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    if (event.dataTransfer?.files.length && input) {
      input.files = event.dataTransfer.files;
    }
  });
}
