// Offline upload queue for /capture, shared by the page and the service worker.
(function (root) {
  const DB_NAME = "ledgerline-capture";
  const STORE = "uploads";
  const LOCK = "ledgerline-capture-flush";

  function open() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function run(mode, work) {
    const db = await open();
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, mode);
        const result = work(tx.objectStore(STORE));
        tx.oncomplete = () => resolve(result.result);
        tx.onerror = tx.onabort = () => reject(tx.error);
      });
    } finally {
      db.close();
    }
  }

  const list = () => run("readonly", (s) => s.getAll());
  const put = (item) => run("readwrite", (s) => s.put(item));
  const remove = (id) => run("readwrite", (s) => s.delete(id));

  // Files are stored as bytes: Blob values in IndexedDB are unreliable on older iOS Safari.
  async function add(file, note, administration) {
    await put({
      name: file.name,
      type: file.type,
      bytes: await file.arrayBuffer(),
      note: note || "",
      adminId: administration.id,
      adminName: administration.name,
      state: "waiting",
      error: "",
      createdAt: Date.now(),
    });
  }

  async function freshToken() {
    const response = await fetch("/capture", { credentials: "same-origin", redirect: "manual" });
    if (!response.ok) return null;
    const match = /name="csrf_token" value="([^"]+)"/.exec(await response.text());
    return match ? match[1] : null;
  }

  // "sent" clears the item, "failed" keeps it for the user, "retry" leaves it waiting.
  async function send(item, token) {
    const body = new FormData();
    body.append("csrf_token", token);
    body.append("administration_id", String(item.adminId));
    body.append("note", item.note);
    body.append("files", new File([item.bytes], item.name, { type: item.type }));
    let response;
    try {
      response = await fetch("/capture", {
        method: "POST",
        body,
        credentials: "same-origin",
        redirect: "manual",
        headers: { Accept: "application/json" },
      });
    } catch {
      return { outcome: "retry" };
    }
    if (response.ok) {
      const row = (await response.json().catch(() => null))?.results?.[0];
      if (!row) return { outcome: "retry" };
      return row.error ? { outcome: "failed", error: row.error } : { outcome: "sent" };
    }
    if (response.status === 409 || response.status === 413 || response.status === 415) {
      return { outcome: "failed", error: "Could not be uploaded. Send it again from the inbox." };
    }
    return { outcome: response.status === 403 ? "token" : "retry" };
  }

  async function flushLocked() {
    let sent = 0;
    let token = null;
    for (const item of await list()) {
      if (item.state !== "waiting") continue;
      token = token ?? (await freshToken().catch(() => null));
      if (!token) break;
      let result = await send(item, token);
      if (result.outcome === "token") {
        token = await freshToken().catch(() => null);
        result = token ? await send(item, token) : { outcome: "retry" };
      }
      if (result.outcome === "sent") {
        await remove(item.id);
        sent += 1;
      } else if (result.outcome === "failed") {
        await put({ ...item, state: "failed", error: result.error });
      } else {
        break;
      }
    }
    return sent;
  }

  function flush() {
    const locks = root.navigator && root.navigator.locks;
    return locks ? locks.request(LOCK, flushLocked) : flushLocked();
  }

  root.captureQueue = { add, list, remove, flush };
})(self);
