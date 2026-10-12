/* The two actions of the first surface go through the JSON API with CSRF and Idempotency-Key: one write path, audit, idempotency. */
(function () {
  const csrf = document.body.dataset.csrf;
  function key() { return (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()); }
  async function command(path, body) {
    const res = await fetch("/api/v1" + path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, "Idempotency-Key": key() },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = (data.code ? data.code + ": " : "") + (data.message || res.statusText) + (data.required_resolution ? "\n" + data.required_resolution : "");
      throw new Error(msg);
    }
    return data;
  }
  function showError(err) {
    const box = document.getElementById("action-error");
    if (!box) { alert(err.message); return; }
    box.textContent = err.message;
    box.classList.remove("hidden");
  }
  document.querySelectorAll("[data-stop]").forEach(btn => btn.addEventListener("click", async () => {
    const reason = prompt("Stopp aller Läufe. Grund (wird protokolliert):");
    if (!reason) return;
    try { await command("/workspace/stop", { reason }); location.reload(); } catch (e) { showError(e); }
  }));
  document.querySelectorAll("[data-resume]").forEach(btn => btn.addEventListener("click", async () => {
    const reason = prompt("Wiederaufnahme. Dokumentierter Grund der Behebung (Art. 12):");
    if (!reason) return;
    try { await command("/workspace/resume", { reason }); location.reload(); } catch (e) { showError(e); }
  }));

  /* Reserved decision (Art. 11): four equal options, none preselected, reasons mandatory, conditions for limited acceptance. */
  const backdrop = document.getElementById("decision-dialog");
  if (backdrop) {
    const dlg = backdrop.querySelector(".dialog");
    let current = null;
    function open(card) {
      current = card.dataset;
      dlg.querySelector("[data-f=title]").textContent = current.title;
      dlg.querySelector("[data-f=meta]").textContent = "Mandat " + current.objectId + " · Fassung " + current.version + " · " + current.budget;
      dlg.querySelector("[data-f=question]").textContent = current.question || "";
      dlg.querySelectorAll("textarea, input").forEach(el => { el.value = ""; });
      dlg.querySelector("[data-f=conditions]").classList.add("hidden");
      dlg.querySelector("[data-f=err]").classList.add("hidden");
      backdrop.classList.remove("hidden");
    }
    function close() { backdrop.classList.add("hidden"); current = null; }
    document.querySelectorAll("[data-decide]").forEach(btn => btn.addEventListener("click", () => open(btn.closest("[data-decision]"))));
    backdrop.addEventListener("click", e => { if (e.target === backdrop) close(); });
    dlg.querySelector("[data-f=cancel]").addEventListener("click", close);
    const OPTIONS = { accept: "Annehmen", accept_limited: "Begrenzt annehmen", revise: "Überarbeiten", reject: "Ablehnen" };
    dlg.querySelectorAll("[data-option]").forEach(btn => btn.addEventListener("click", async () => {
      const option = btn.dataset.option;
      const rationale = dlg.querySelector("[name=rationale]").value.trim();
      const consequences = dlg.querySelector("[name=consequences]").value.trim();
      const objections = dlg.querySelector("[name=objections]").value.split("\n").map(s => s.trim()).filter(Boolean);
      const err = dlg.querySelector("[data-f=err]");
      if (!rationale || !consequences) { err.textContent = "Tragende Gründe und Folgen sind Pflicht (Art. 11)."; err.classList.remove("hidden"); return; }
      const conditions = [];
      if (option === "accept_limited") {
        const text = dlg.querySelector("[name=cond_text]").value.trim(), responsible = dlg.querySelector("[name=cond_resp]").value.trim(), due = dlg.querySelector("[name=cond_due]").value;
        if (!text || !responsible || !due) { dlg.querySelector("[data-f=conditions]").classList.remove("hidden"); err.textContent = "Begrenzt annehmen braucht eine Auflage mit Zuständigkeit und Frist."; err.classList.remove("hidden"); return; }
        conditions.push({ text, responsible, due });
      }
      const alternatives = Object.keys(OPTIONS).filter(o => o !== option).map(o => OPTIONS[o]);
      try {
        const res = await command("/mandates/" + current.objectId + "/approve", {
          expected_version: Number(current.version), option, rationale, alternatives_presented: alternatives,
          remaining_objections: objections, consequences, conditions });
        close();
        location.href = "/campus/pruefung?decided=" + encodeURIComponent(res.decision_id || res.mandate_version_id || "");
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    }));
    dlg.querySelector("[data-option=accept_limited]").addEventListener("mouseenter", () => dlg.querySelector("[data-f=conditions]").classList.remove("hidden"));
  }
})();
