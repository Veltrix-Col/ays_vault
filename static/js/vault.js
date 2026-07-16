document.addEventListener("DOMContentLoaded", () => {
  const sidebar = document.querySelector("#app-sidebar");
  const openButton = document.querySelector("[data-sidebar-open]");
  const closeButtons = document.querySelectorAll("[data-sidebar-close]");
  const backdrop = document.querySelector(".sidebar-backdrop");

  const setSidebar = (open) => {
    if (!sidebar || !openButton || !backdrop) return;
    sidebar.classList.toggle("is-open", open);
    document.body.classList.toggle("sidebar-open", open);
    openButton.setAttribute("aria-expanded", String(open));
    backdrop.hidden = !open;
    if (open) sidebar.querySelector("a, button")?.focus();
    else openButton.focus();
  };

  openButton?.addEventListener("click", () => setSidebar(true));
  closeButtons.forEach((button) => button.addEventListener("click", () => setSidebar(false)));
  sidebar?.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => {
    if (window.matchMedia("(max-width: 991px)").matches) setSidebar(false);
  }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sidebar?.classList.contains("is-open")) setSidebar(false);
  });
  window.matchMedia("(min-width: 992px)").addEventListener("change", (event) => {
    if (event.matches && sidebar?.classList.contains("is-open")) setSidebar(false);
  });

  const timelineForm = document.querySelector("[data-timeline-filters]");
  if (timelineForm) {
    const applyQuickFilter = (field, value) => {
      const input = timelineForm.querySelector(`[name="${field}"]`);
      input.value = input.value === value ? "" : value;
      timelineForm.requestSubmit();
    };
    timelineForm.querySelectorAll("[data-quick-period]").forEach((button) => button.addEventListener("click", () => applyQuickFilter("period", button.dataset.quickPeriod)));
    timelineForm.querySelectorAll("[data-quick-event]").forEach((button) => button.addEventListener("click", () => applyQuickFilter("quick_event", button.dataset.quickEvent)));
    const advanced = timelineForm.querySelector("[data-advanced-filters]");
    advanced?.addEventListener("toggle", () => {
      timelineForm.querySelector("[name=advanced]").value = advanced.open ? "1" : "";
    });
    const syncExportForm = (exportForm) => {
      exportForm.querySelectorAll("input[data-synced-filter]").forEach((input) => input.remove());
      new FormData(timelineForm).forEach((value, key) => {
        const input = document.createElement("input");
        input.type = "hidden"; input.name = key; input.value = value; input.dataset.syncedFilter = "true";
        exportForm.appendChild(input);
      });
    };
    document.querySelectorAll("#timeline-export-xlsx, #timeline-export-pdf").forEach((exportForm) => exportForm.addEventListener("submit", () => syncExportForm(exportForm)));
  }

  let timer = null;
  const panel = document.querySelector("#reveal-panel");
  const form = document.querySelector("#reveal-form");
  if (!form || !panel) return;

  document.querySelectorAll(".reveal-btn").forEach((button) => button.addEventListener("click", () => {
    document.querySelector("#field-input").value = button.dataset.field;
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
    panel.querySelector("input:not([type=hidden])")?.focus();
  }));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
    const response = await fetch(form.action, { method: "POST", body: data, headers: { "X-CSRFToken": csrf }, cache: "no-store" });
    const payload = await response.json();
    if (response.status === 428 && payload.reauth_url) { window.location.assign(payload.reauth_url); return; }
    if (!response.ok) { window.alert("No fue posible confirmar. Verifique el motivo y los permisos."); return; }
    const field = payload.field;
    const target = document.querySelector(`#value-${field}`);
    target.textContent = payload.value;
    target.classList.add("revealed");
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "btn secondary";
    copy.textContent = "Copiar";
    copy.addEventListener("click", async () => {
      let result = "failed";
      try { await navigator.clipboard.writeText(target.textContent); result = "success"; }
      finally {
        const meta = document.querySelector("#copy-meta");
        const copyData = new FormData();
        copyData.append("copy_token", payload.copy_token);
        copyData.append("result", result);
        const audited = await fetch(meta.dataset.url, { method: "POST", body: copyData, headers: { "X-CSRFToken": csrf }, cache: "no-store" });
        copy.disabled = true;
        copy.textContent = audited.ok ? "Copiado y auditado" : "Copia no confirmada";
      }
    });
    target.parentElement.appendChild(copy);
    clearTimeout(timer);
    timer = window.setTimeout(() => {
      target.textContent = field === "pan" ? "•••• •••• ••••" : "••/••";
      target.classList.remove("revealed");
      copy.remove(); panel.hidden = true; form.reset();
    }, payload.expires_in * 1000);
  });
});
