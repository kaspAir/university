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

  /* Reserved decision (Art. 11): four equal options, none preselected, reasons mandatory, conditions for limited acceptance.
     Generic: the card names the endpoint (mandate approval or result approval) and the binding (expected_version / expected_sha256). */
  const backdrop = document.getElementById("decision-dialog");
  if (backdrop) {
    const dlg = backdrop.querySelector(".dialog");
    let current = null;
    function open(card) {
      current = card.dataset;
      dlg.querySelector("[data-f=kind]").textContent = current.kind || "Entscheid";
      dlg.querySelector("[data-f=title]").textContent = current.title;
      dlg.querySelector("[data-f=meta]").textContent = current.meta || "";
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
      const extra = current.extra ? JSON.parse(current.extra) : {};
      try {
        await command(current.endpoint, Object.assign({ option, rationale, alternatives_presented: alternatives, remaining_objections: objections, consequences, conditions }, extra));
        close();
        location.href = current.after || location.pathname;
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    }));
    dlg.querySelector("[data-option=accept_limited]").addEventListener("mouseenter", () => dlg.querySelector("[data-f=conditions]").classList.remove("hidden"));
  }

  /* Research order journey: each step is one or two existing commands; the page reloads and shows the next step from server state. */
  function val(form, name) { const el = form.querySelector("[name=" + name + "]"); return el ? el.value.trim() : ""; }
  function lines(text) { return text.split("\n").map(s => s.trim()).filter(Boolean); }
  function fail(form, e) { const err = form.querySelector("[data-f=err]"); err.textContent = e.message; err.classList.remove("hidden"); }
  const mandateForm = document.querySelector("[data-journey=mandate]");
  if (mandateForm) mandateForm.querySelector("[data-submit]").addEventListener("click", async () => {
    const document_ = {
      title: val(mandateForm, "title"), question_space: val(mandateForm, "question_space"), knowledge_goal: val(mandateForm, "knowledge_goal"),
      method_frame: val(mandateForm, "method_frame"), data_labels: ["public"], access_rules: val(mandateForm, "access_rules"), risk_rules: val(mandateForm, "risk_rules"),
      review_requirements: val(mandateForm, "review_requirements"), duration_days: Number(val(mandateForm, "duration_days")),
      report_points: lines(val(mandateForm, "report_points")), responsible_user_id: document.body.dataset.userId };
    try {
      const res = await command("/mandates", { document: document_, budget: val(mandateForm, "budget"), currency: "CHF" });
      location.href = "/campus/auftrag/" + res.mandate_version_id;
    } catch (e) { fail(mandateForm, e); }
  });
  const projectForm = document.querySelector("[data-journey=project]");
  if (projectForm) projectForm.querySelector("[data-submit]").addEventListener("click", async () => {
    const sel = projectForm.querySelector("[name=profile]"); const profile = JSON.parse(sel.options[sel.selectedIndex].dataset.profile);
    try {
      const project = await command("/projects", { mandate_version_id: projectForm.dataset.mandate, institute_id: val(projectForm, "institute_id"),
                                                   title: val(projectForm, "title"), budget: val(projectForm, "budget") });
      const need = Object.assign({}, window.CAMPUS_TASK_DEFAULTS, {
        agent_profile_version_ref: profile.ref, skill_version_refs: profile.skill_version_refs || [],
        qualification_mode: (profile.qualification_status === "task_qualified" && (profile.qualified_task_classes || []).includes(window.CAMPUS_TASK_DEFAULTS.task_class)) ? "qualified" : "sandbox_experiment",
        routing_policy_ref: projectForm.dataset.policy });
      await command("/projects/" + project.project_id + "/tasks", { task_need: need, budget: "1.00" });
      location.reload();
    } catch (e) { fail(projectForm, e); }
  });
  const workflowForm = document.querySelector("[data-journey=workflow]");
  /* A new flow is offered after a version was returned for rework; if the previous flow is still running, show its state instead. */
  if (workflowForm && workflowForm.dataset.workflowCheck) (async function check() {
    try {
      const res = await fetch("/api/v1/workflows/" + workflowForm.dataset.workflowCheck, { credentials: "same-origin" });
      const data = await res.json();
      if (data.status === "RUNNING") {
        workflowForm.querySelectorAll("textarea, label, button, ul").forEach(el => el.classList.add("hidden"));
        const box = workflowForm.querySelector("[data-f=running]"); box.classList.remove("hidden");
        box.querySelector("[data-f=status]").textContent = data.status; box.querySelector("[data-f=stage]").textContent = data.stage ? "· Stufe " + data.stage : "";
        setTimeout(check, 5000);
      } else if (workflowForm.querySelector("[data-f=running]:not(.hidden)")) { location.reload(); }
    } catch (e) { /* the service is unreachable: the form stays usable; the API refuses a second concurrent flow anyway */ }
  })();
  if (workflowForm) workflowForm.querySelector("[data-submit]").addEventListener("click", async () => {
    try {
      await command("/tasks/" + workflowForm.dataset.task + "/workflow", { source_urls: lines(val(workflowForm, "source_urls")), selection_mode: "llm" });
      location.reload();
    } catch (e) { fail(workflowForm, e); }
  });
  document.querySelectorAll("[data-action=freeze-dossier]").forEach(btn => btn.addEventListener("click", async () => {
    try { await command("/projects/" + btn.dataset.project + "/dossier", {}); location.reload(); }
    catch (e) { const err = btn.parentElement.querySelector("[data-f=err]"); if (err) { err.textContent = e.message; err.classList.remove("hidden"); } else showError(e); }
  }));
  /* Workflow status is read from the durable workflow service; the page reloads once the run has ended. */
  document.querySelectorAll("[data-workflow-id]").forEach(el => {
    const id = el.dataset.workflowId; let ended = false;
    async function tick() {
      try {
        const res = await fetch("/api/v1/workflows/" + id, { credentials: "same-origin" });
        const data = await res.json();
        el.querySelector("[data-f=status]").textContent = data.status || "unbekannt";
        el.querySelector("[data-f=stage]").textContent = data.stage ? "· Stufe " + data.stage : "";
        if (data.status && data.status !== "RUNNING" && !ended) {
          ended = true;
          if (data.status === "COMPLETED") { setTimeout(() => location.reload(), 1500); return; }
          /* failed, terminated, timed out: no result exists; the reason stays in the workflow service and the audit; a new flow is a new route */
          el.querySelector("[data-f=status]").textContent = data.status + " – Ablauf ohne Ergebnis beendet (Grund im Ablaufdienst und im Audit unter Betrieb). Ein neuer Ablauf ist eine neue begründete Route.";
          document.querySelectorAll("[data-restart]").forEach(f => f.classList.remove("hidden"));
          return;
        }
      } catch (e) { el.querySelector("[data-f=status]").textContent = "Ablaufdienst nicht erreichbar"; }
      if (!ended) setTimeout(tick, 5000);
    }
    tick();
  });
})();
