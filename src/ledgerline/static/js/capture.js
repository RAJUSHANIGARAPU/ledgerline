// Progressive enhancement for /capture: thumbnails, upload progress, service worker.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}

const queue = window.captureQueue;
const queueBox = document.getElementById("capture-queue");
let notice = "";

async function renderQueue() {
  if (!queueBox) return;
  const items = await queue.list().catch(() => []);
  const waiting = items.filter((i) => i.state === "waiting");
  const failed = items.filter((i) => i.state === "failed");
  const lines = [];
  if (notice) lines.push(box("flash-success", notice));
  if (waiting.length) lines.push(box("flash-info", `${waiting.length} waiting to upload`));
  for (const item of failed) {
    const row = box("flash-error", `${item.name} (${item.adminName}): ${item.error} `);
    const discard = document.createElement("button");
    discard.type = "button";
    discard.className = "btn btn-secondary";
    discard.textContent = "Discard";
    discard.onclick = () => queue.remove(item.id).then(renderQueue);
    row.append(discard);
    lines.push(row);
  }
  queueBox.replaceChildren(...lines);
  queueBox.hidden = lines.length === 0;
}

function box(kind, text) {
  const el = document.createElement("div");
  el.className = `flash ${kind}`;
  el.setAttribute("role", "status");
  el.textContent = text;
  return el;
}

async function flushQueue() {
  if (!queue || navigator.onLine === false) return;
  try {
    const sent = await queue.flush();
    if (sent) notice = `${sent} queued upload${sent === 1 ? "" : "s"} sent. Check your inbox.`;
  } catch {
    // IndexedDB unavailable or the network dropped again; the next trigger retries
  }
  await renderQueue();
}

if (queue) {
  window.addEventListener("online", flushQueue);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") flushQueue();
  });
  flushQueue();
}

async function enqueue(files, note, administration) {
  for (const file of files) await queue.add(file, note, administration);
  const reg = await navigator.serviceWorker?.ready.catch(() => null);
  await reg?.sync?.register("capture-queue").catch(() => {});
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
    notice = "";
    const files = [...form.querySelectorAll("[data-picker]")].flatMap((input) => [...input.files]);
    const administration = { id: Number(form.dataset.adminId), name: form.dataset.adminName };
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
    xhr.onerror = async () => {
      submit.disabled = false;
      progress.hidden = true;
      try {
        if (!files.length) throw new Error("nothing to queue");
        await enqueue(files, form.elements.note.value.trim(), administration);
      } catch {
        previews.textContent = "Upload failed. Check your connection and try again.";
        return;
      }
      form.reset();
      previews.replaceChildren();
      notice = "No connection. Saved on this phone; it uploads when you are back online.";
      renderQueue();
    };
    progress.hidden = false;
    submit.disabled = true;
    xhr.send(new FormData(form));
  });
}
