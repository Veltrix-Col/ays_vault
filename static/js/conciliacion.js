document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-conc-form]");
  if (!form) return;

  const catalogEl = document.getElementById("conc-slots-catalog");
  let catalog = {};
  try { catalog = JSON.parse(catalogEl?.textContent || "{}"); } catch (_) { catalog = {}; }

  const ramoSelect = form.querySelector('select[name="ramo"]');
  const submit = form.querySelector("[data-conc-submit]");
  const progress = form.querySelector("[data-conc-progress]");

  const result = document.querySelector("[data-conc-result]");
  const meta = document.querySelector("[data-result-meta]");
  const banner = document.querySelector("[data-result-banner]");
  const incidentsBox = document.querySelector("[data-result-incidents]");
  const incidentsList = document.querySelector("[data-incidents-list]");
  const btnFacturar = document.querySelector("[data-action-facturar]");
  const btnDownload = document.querySelector("[data-action-download]");
  const btnReset = document.querySelector("[data-action-reset]");

  const modal = document.querySelector("[data-facturar-modal]");
  const modalClose = document.querySelector("[data-facturar-close]");

  let objectUrl = null;
  let outputName = "Reporte_Conciliacion.xlsx";

  // --- Slots dinámicos por ramo -------------------------------------------
  function updateSlots(ramo) {
    const slots = catalog[ramo] || [];
    slots.forEach((slot) => {
      const zone = form.querySelector(`.conc-slot[data-slot="${slot.campo}"]`);
      if (!zone) return;
      const input = zone.querySelector('input[type="file"]');
      const label = zone.querySelector("[data-slot-label]");
      const help = zone.querySelector("[data-slot-help]");
      const badge = zone.querySelector("[data-slot-badge]");
      const temporal = zone.querySelector("[data-slot-temporal]");
      const temporalText = zone.querySelector("[data-slot-temporal-text]");
      if (input) input.setAttribute("accept", slot.accept || "");
      if (label) label.innerHTML = slot.required
        ? escapeHtml(slot.label)
        : `${escapeHtml(slot.label)} <span class="conc-optional">(opcional)</span>`;
      if (help) help.textContent = slot.help || "";
      if (badge) badge.textContent = (slot.accept || "").toUpperCase().replace(/\./g, "");
      if (temporal) {
        temporal.hidden = !slot.nota_temporal;
        if (temporalText) temporalText.textContent = slot.nota_temporal || "";
      }
    });
  }

  ramoSelect?.addEventListener("change", () => updateSlots(ramoSelect.value));
  if (ramoSelect) updateSlots(ramoSelect.value);

  // --- Nombre de archivo seleccionado -------------------------------------
  form.querySelectorAll('input[type="file"]').forEach((input) => {
    input.addEventListener("change", () => {
      const display = input.closest(".conc-slot")?.querySelector("[data-file-name]");
      const file = input.files?.[0];
      if (display) {
        display.textContent = file
          ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`
          : "Ningún archivo seleccionado";
      }
    });
  });

  // --- Envío ---------------------------------------------------------------
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true; progress.hidden = false; result.hidden = true;
    try {
      const response = await fetch(form.action || window.location.href, {
        method: "POST", body: new FormData(form), cache: "no-store",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!response.ok) {
        const text = (await response.text()).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
        throw new Error(text.slice(0, 400) || "No fue posible procesar la conciliación.");
      }
      const blob = await response.blob();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(blob);
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      outputName = match ? decodeURIComponent(match[1]) : outputName;
      const raw = response.headers.get("X-Conciliacion-Summary") || "";
      const summary = raw
        ? JSON.parse(decodeURIComponent(escape(atob(raw.replace(/-/g, "+").replace(/_/g, "/")))))
        : {};
      renderResult(summary);
    } catch (error) {
      window.alert(error.message || "No fue posible procesar la conciliación.");
    } finally {
      submit.disabled = false; progress.hidden = true;
    }
  });

  function renderResult(summary) {
    meta.replaceChildren(
      metaItem("Ramo", summary.ramo),
      metaItem("Periodo", summary.periodo),
      metaItem("Póliza", summary.poliza),
      metaItem("Incidentes", String(summary.total_incidentes ?? 0)),
    );

    const sinIncidentes = summary.sin_incidentes === true || (summary.total_incidentes ?? 0) === 0;
    banner.className = `conc-banner ${sinIncidentes ? "is-ok" : "is-warn"}`;
    banner.textContent = sinIncidentes
      ? "Conciliación sin incidentes. Todo cuadra: puede continuar con la facturación."
      : `Se encontraron ${summary.total_incidentes} incidente(s). Revise y descargue el detalle antes de facturar.`;

    const porTipo = summary.por_tipo || {};
    if (!sinIncidentes && Object.keys(porTipo).length) {
      incidentsList.replaceChildren(...Object.entries(porTipo).map(([tipo, n]) => {
        const li = document.createElement("li");
        const label = document.createElement("span"); label.textContent = tipo;
        const count = document.createElement("strong"); count.textContent = String(n);
        li.append(label, count); return li;
      }));
      incidentsBox.hidden = false;
    } else {
      incidentsBox.hidden = true;
    }

    btnFacturar.hidden = !sinIncidentes;
    btnDownload.hidden = sinIncidentes;
    result.hidden = false;
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function metaItem(label, value) {
    const item = document.createElement("div"); item.className = "conc-meta-item";
    const strong = document.createElement("strong"); strong.textContent = value || "—";
    const span = document.createElement("span"); span.textContent = label;
    item.append(strong, span); return item;
  }

  btnDownload?.addEventListener("click", () => {
    if (!objectUrl) return;
    const link = document.createElement("a");
    link.href = objectUrl; link.download = outputName;
    document.body.appendChild(link); link.click(); link.remove();
  });

  btnReset?.addEventListener("click", () => {
    form.reset();
    result.hidden = true;
    form.querySelectorAll("[data-file-name]").forEach((el) => { el.textContent = "Ningún archivo seleccionado"; });
    if (ramoSelect) updateSlots(ramoSelect.value);
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // --- Modal Facturar (placeholder) ---------------------------------------
  function openModal() { if (modal) { modal.hidden = false; modalClose?.focus(); } }
  function closeModal() { if (modal) modal.hidden = true; }
  btnFacturar?.addEventListener("click", openModal);
  modalClose?.addEventListener("click", closeModal);
  modal?.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }
});
