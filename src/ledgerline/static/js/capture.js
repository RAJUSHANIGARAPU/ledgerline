// Progressive enhancement for /capture: thumbnails, upload progress, service worker.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}

const form = document.getElementById("capture-form");
if (form) {
  const previews = document.getElementById("capture-previews");
  const progress = document.getElementById("capture-progress");
  const submit = document.getElementById("capture-submit");

  async function thumbnail(file) {
    const bitmap = await createImageBitmap(file);
    const scale = 96 / Math.max(bitmap.width, bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const img = new Image();
    img.src = canvas.toDataURL("image/jpeg", 0.7);
    img.alt = file.name;
    img.width = canvas.width;
    img.height = canvas.height;
    return img;
  }

  async function renderPreviews() {
    previews.replaceChildren();
    for (const input of form.querySelectorAll("[data-picker]")) {
      for (const file of input.files) {
        const item = document.createElement("div");
        item.className = "pill";
        item.textContent = file.name;
        if (file.type.startsWith("image/")) {
          try {
            item.replaceChildren(await thumbnail(file));
          } catch {
            // e.g. a format the browser cannot decode; keep the file name
          }
        }
        previews.append(item);
      }
    }
  }
  form.addEventListener("change", renderPreviews);

  form.addEventListener("submit", (event) => {
    if (!window.FormData || !window.XMLHttpRequest) return;
    event.preventDefault();
    const xhr = new XMLHttpRequest();
    xhr.open("POST", form.action);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) progress.value = Math.round((e.loaded / e.total) * 100);
    };
    xhr.onload = () => {
      const page = new DOMParser().parseFromString(xhr.responseText, "text/html");
      const next = page.querySelector("main.content");
      if (xhr.status < 400 && next) {
        document.querySelector("main.content").replaceWith(document.adoptNode(next));
      } else {
        form.submit();
      }
    };
    xhr.onerror = () => {
      submit.disabled = false;
      progress.hidden = true;
      previews.textContent = "Upload failed. Check your connection and try again.";
    };
    progress.hidden = false;
    submit.disabled = true;
    xhr.send(new FormData(form));
  });
}
