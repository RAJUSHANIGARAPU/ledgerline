// Offline upload queue for /capture, shared by the page and the service worker.
// Metadata and file bytes live in separate stores so listing the queue never loads photos.
(function (root) {
  const DB_NAME = "ledgerline-capture";
  const DB_VERSION = 2;
  const META = "uploads";
  const BYTES = "bytes";
  const LOCK = "ledgerline-capture-flush";
  const MAX_ATTEMPTS = 6;
  const BASE_DELAY = 30 * 1000;
  const MAX_DELAY = 60 * 60 * 1000;

  function open() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        for (const name of [...db.objectStoreNames]) db.deleteObjectStore(name);
        db.createObjectStore(META, { keyPath: "id", autoIncrement: true });
        db.createObjectStore(BYTES);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function run(mode, work) {
    const db = await open();
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction([META, BYTES], mode);
        let out;
        try {
          out = work(tx.objectStore(META), tx.objectStore(BYTES));
        } catch (error) {
          tx.abort();
          reject(error);
          return;
        }
        tx.oncomplete = () => resolve(out instanceof IDBRequest ? out.result : out);
        tx.onerror = tx.onabort = () => reject(tx.error || new DOMException("Aborted", "AbortError"));
      });
    } finally {
      db.close();
    }
  }

  const list = () => run("readonly", (meta) => meta.getAll());
  const getBytes = (id) => run("readonly", (meta, bytes) => bytes.get(id));
  const remove = (id) => run("readwrite", (meta, bytes) => { meta.delete(id); bytes.delete(id); });
  const purge = () => run("readwrite", (meta, bytes) => { meta.clear(); bytes.clear(); });

  function update(id, patch, dropBytes = false) {
    return run("readwrite", (meta, bytes) => {
      meta.get(id).onsuccess = (e) => {
        if (e.target.result) meta.put({ ...e.target.result, ...patch });
      };
      if (dropBytes) bytes.delete(id);
    });
  }

  const sameOwner = (item, who) => item.userId === who.userId && item.tenantId === who.tenantId;

  // Items captured by another user or in another tenant are removed, never shown or sent.
  function purgeForeign(who) {
    return run("readwrite", (meta, bytes) => {
      meta.getAll().onsuccess = (e) => {
        for (const item of e.target.result) {
          if (!sameOwner(item, who)) { meta.delete(item.id); bytes.delete(item.id); }
        }
      };
    });
  }

  function retry(id) {
    return update(id, { state: "waiting", attempts: 0, nextAt: 0, error: "" });
  }

  // One transaction: either every file of a submission is queued or none is.
  // Files are stored as bytes: Blob values in IndexedDB are unreliable on older iOS Safari.
  async function addMany(files, note, administration, who) {
    const buffers = await Promise.all(files.map((file) => file.arrayBuffer()));
    await run("readwrite", (meta, bytes) => {
      files.forEach((file, i) => {
        meta.add({
          name: file.name,
          type: file.type,
          note: note || "",
          adminId: administration.id,
          adminName: administration.name,
          userId: who.userId,
          tenantId: who.tenantId,
          state: "waiting",
          error: "",
          attempts: 0,
          nextAt: 0,
          createdAt: Date.now(),
        }).onsuccess = (e) => bytes.put(buffers[i], e.target.result);
      });
    });
  }

  // The signed-in identity and a CSRF token; also tells an expired session from a dead network.
  async function readContext() {
    let response;
    try {
      response = await fetch("/capture/context", { credentials: "same-origin", redirect: "manual" });
    } catch {
      return { status: "offline" };
    }
    if (response.type === "opaqueredirect" || response.status === 401) return { status: "auth" };
    if (!response.ok) return { status: "retry" };
    const data = await response.json().catch(() => null);
    return data ? { status: "ok", ...data } : { status: "retry" };
  }

  const FAILED = {
    409: "The administration is no longer available. Send it again from the inbox.",
    413: "The file is too large.",
    415: "This file type is not supported. Send a PDF, JPEG or PNG.",
  };

  // sent / failed (permanent, shown to the user) / forbidden / auth / network / transient.
  async function send(item, csrf) {
    const content = await getBytes(item.id);
    if (!content) return { outcome: "failed", error: "The saved file is missing. Choose it again." };
    const body = new FormData();
    body.append("csrf_token", csrf);
    body.append("administration_id", String(item.adminId));
    body.append("note", item.note);
    body.append("files", new File([content], item.name, { type: item.type }));
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
      return { outcome: "network" };
    }
    if (response.type === "opaqueredirect" || response.status === 401) return { outcome: "auth" };
    if (response.ok) {
      const row = (await response.json().catch(() => null))?.results?.[0];
      if (!row) return { outcome: "transient" };
      return row.error ? { outcome: "failed", error: row.error } : { outcome: "sent" };
    }
    if (response.status === 403) return { outcome: "forbidden" };
    if (FAILED[response.status]) return { outcome: "failed", error: FAILED[response.status] };
    if (response.status >= 500 || response.status === 408 || response.status === 429) {
      return { outcome: "transient" };
    }
    return { outcome: "failed", error: `The server refused this upload (error ${response.status}).` };
  }

  async function backOff(item) {
    const attempts = (item.attempts || 0) + 1;
    if (attempts >= MAX_ATTEMPTS) {
      await update(item.id, { state: "failed", retryable: true, attempts, error: "The server kept failing. Try again later." });
    } else {
      const delay = Math.min(BASE_DELAY * 2 ** (attempts - 1), MAX_DELAY);
      await update(item.id, { attempts, nextAt: Date.now() + delay });
    }
  }

  const waitingCount = async () => (await list()).filter((i) => i.state === "waiting").length;

  async function flushLocked() {
    const summary = { sent: 0, remaining: 0, auth: false };
    let context = await readContext();
    if (context.status !== "ok") {
      summary.auth = context.status === "auth";
      summary.remaining = await waitingCount();
      return summary;
    }
    await purgeForeign(context);
    for (const item of await list()) {
      if (item.state !== "waiting" || (item.nextAt || 0) > Date.now()) continue;
      let result = await send(item, context.csrf);
      if (result.outcome === "forbidden") {
        // a stale token also gives 403; only a second refusal with a fresh token is final
        context = await readContext();
        result = context.status === "ok" ? await send(item, context.csrf) : { outcome: context.status };
        if (result.outcome === "forbidden") {
          result = { outcome: "failed", error: "Your account is read-only here and cannot upload documents." };
        }
      }
      if (result.outcome === "sent") {
        await remove(item.id);
        summary.sent += 1;
      } else if (result.outcome === "failed") {
        await update(item.id, { state: "failed", error: result.error }, true);
      } else if (result.outcome === "transient") {
        await backOff(item);
      } else {
        summary.auth = result.outcome === "auth";
        break;
      }
    }
    summary.remaining = await waitingCount();
    return summary;
  }

  function flush() {
    const locks = root.navigator && root.navigator.locks;
    return locks ? locks.request(LOCK, flushLocked) : flushLocked();
  }

  root.captureQueue = { addMany, list, remove, retry, purge, purgeForeign, flush, sameOwner };
})(self);
