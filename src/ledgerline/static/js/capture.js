// Progressive enhancement for /capture: thumbnails, upload progress, service worker.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}

const queue = window.captureQueue;
const strings = JSON.parse(document.getElementById("capture-strings")?.textContent || "{}");
const say = (key, vars = {}) =>
  (strings[key] ?? key).replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
const plural = (key, count) => say(`${key}_${count === 1 ? "one" : "other"}`, { count });
// Queue failures hold a code (this page's language) or text the server already translated.
const failure = (item) => (item.code ? say(`err.${item.code}`, { status: item.arg }) : item.error);
const queueBox = document.getElementById("capture-queue");
const who = queueBox
  ? { userId: Number(queueBox.dataset.userId), tenantId: Number(queueBox.dataset.tenantId) }
  : null;
let notice = "";
let needsSignIn = false;
let retryTimer = null;

async function renderQueue() {
  if (!queueBox) return;
  const all = await queue.list().catch(() => []);
  const items = all.filter((i) => queue.sameOwner(i, who));
  const waiting = items.filter((i) => i.state === "waiting");
  const failed = items.filter((i) => i.state === "failed");
  const lines = [];
  if (notice) lines.push(box("flash-success", notice));
  if (needsSignIn && waiting.length) {
    const row = box("flash-error", `${plural("sign_in_again", waiting.length)} `);
    const link = document.createElement("a");
    link.href = "/login";
    link.textContent = say("sign_in");
    row.append(link);
    lines.push(row);
  } else if (waiting.length) {
    lines.push(box("flash-info", say("waiting", { count: waiting.length })));
  }
  for (const item of failed) {
    const row = box("flash-error", `${item.name} (${item.adminName}): ${failure(item)} `);
    if (item.retryable) row.append(action(say("retry"), () => queue.retry(item.id).then(flushQueue)));
    row.append(action(say("discard"), () => queue.remove(item.id).then(renderQueue)));
    lines.push(row);
  }
  queueBox.replaceChildren(...lines);
  queueBox.hidden = lines.length === 0;
  scheduleRetry(waiting);
}

function action(label, onclick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn btn-secondary";
  button.textContent = label;
  button.onclick = onclick;
  return button;
}

// Items backed off after a server error are picked up again without a reload.
function scheduleRetry(waiting) {
  clearTimeout(retryTimer);
  const due = waiting.map((i) => i.nextAt || 0).filter((t) => t > Date.now());
  if (due.length) retryTimer = setTimeout(flushQueue, Math.min(...due) - Date.now() + 500);
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
    const result = await queue.flush();
    needsSignIn = result.auth;
    if (result.sent) notice = plural("sent", result.sent);
  } catch {
    // IndexedDB unavailable or the network dropped again; the next trigger retries
  }
  await renderQueue();
}

if (queue && who) {
  // Anything left by another user or tenant on this device is removed before it can be shown.
  queue.purgeForeign(who).catch(() => {}).then(renderQueue);
  window.addEventListener("online", flushQueue);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") flushQueue();
  });
  flushQueue();
}

function storageProblem(error) {
  if (error?.name === "QuotaExceededError") {
    return say("storage_full");
  }
  if (!window.indexedDB || ["SecurityError", "InvalidStateError", "UnknownError"].includes(error?.name)) {
    return say("storage_blocked");
  }
  return say("storage_failed");
}

async function enqueue(files, note, administration) {
  await queue.addMany(files, note, administration, who);
  navigator.storage?.persist?.().catch(() => {});
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
      if (!files.length || !queue) {
        previews.textContent = say("upload_failed");
        return;
      }
      try {
        await enqueue(files, form.elements.note.value.trim(), administration);
      } catch (error) {
        previews.textContent = storageProblem(error);
        return;
      }
      form.reset();
      previews.replaceChildren();
      notice = say("saved_offline");
      renderQueue();
    };
    progress.hidden = false;
    submit.disabled = true;
    xhr.send(new FormData(form));
  });
}
