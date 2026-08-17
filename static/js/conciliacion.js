document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-conc-form]");
  if (!form) return;

  const catalogEl = document.getElementById("conc-slots-catalog");
  let catalog = {};
  try { catalog = JSON.parse(catalogEl?.textContent || "{}"); } catch (_) { catalog = {}; }

  const fuentesCatalogEl = document.getElementById("conc-fuentes-catalog");
  let fuentesCatalog = {};
  try { fuentesCatalog = JSON.parse(fuentesCatalogEl?.textContent || "{}"); } catch (_) { fuentesCatalog = {}; }

  const ramoSelect = form.querySelector('select[name="ramo"]');
  const fuenteRadios = form.querySelectorAll('input[name="fuente"]');
  const fuenteApiNota = form.querySelector("[data-fuente-api-nota]");
  const excelSlots = form.querySelector("[data-fuente-excel-slots]");
  const perfilZohoField = form.querySelector("[data-perfil-zoho-field]");
  const submit = form.querySelector("[data-conc-submit]");
  const progress = form.querySelector("[data-conc-progress]");

  const result = document.querySelector("[data-conc-result]");
  const meta = document.querySelector("[data-result-meta]");
  const banner = document.querySelector("[data-result-banner]");
  const incidentsBox = document.querySelector("[data-result-incidents]");
  const incidentsList = document.querySelector("[data-incidents-list]");
  const btnDownload = document.querySelector("[data-action-download]");
  const btnReset = document.querySelector("[data-action-reset]");

  const cobrosSection = document.querySelector("[data-cobros-section]");
  const cobrosEmpty = document.querySelector("[data-cobros-empty]");
  const cobrosList = document.querySelector("[data-cobros-list]");
  const polizaLink = document.querySelector("[data-poliza-link]");

  let objectUrl = null;
  let outputName = "Reporte_Conciliacion.xlsx";

  // --- Slots dinámicos por ramo -------------------------------------------
  function updateSlots(ramo) {
    const slots = catalog[ramo] || [];
    slots.forEach((slot) => {
      const zone = form.querySelector(`.tool-slot[data-slot="${slot.campo}"]`);
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
        : `${escapeHtml(slot.label)} <span class="tool-optional">(opcional)</span>`;
      if (help) help.textContent = slot.help || "";
      if (badge) badge.textContent = (slot.accept || "").toUpperCase().replace(/\./g, "");
      if (temporal) {
        temporal.hidden = !slot.nota_temporal;
        if (temporalText) temporalText.textContent = slot.nota_temporal || "";
      }
    });
  }

  ramoSelect?.addEventListener("change", () => { updateSlots(ramoSelect.value); updateFuente(); });
  if (ramoSelect) updateSlots(ramoSelect.value);

  // --- Fuente de asegurados/personas: Excel (legado) vs Zoho API -----------
  function fuenteSeleccionada() {
    return [...fuenteRadios].find((r) => r.checked)?.value || "excel";
  }

  function updateFuente() {
    const ramo = ramoSelect?.value;
    const soporte = fuentesCatalog[ramo] || {};
    const soportaApi = !!soporte.asegurados;
    fuenteRadios.forEach((radio) => {
      if (radio.value === "api") {
        radio.disabled = !soportaApi;
        if (!soportaApi && radio.checked) {
          radio.checked = false;
          const excelRadio = [...fuenteRadios].find((r) => r.value === "excel");
          if (excelRadio) excelRadio.checked = true;
        }
      }
    });
    if (fuenteApiNota) fuenteApiNota.hidden = soportaApi;

    const fuente = fuenteSeleccionada();
    const esApi = fuente === "api";
    if (excelSlots) {
      excelSlots.hidden = esApi;
      excelSlots.querySelectorAll("input[type=\"file\"]").forEach((input) => {
        if (esApi) input.value = "";
      });
    }
    if (perfilZohoField) perfilZohoField.hidden = !esApi;

    // Novedades: se oculta solo si el ramo tambien resuelve novedades por API
    // (vg_deudores no: su novedad viene del banco, sigue pidiendo el archivo).
    const novedadesZone = form.querySelector('.tool-slot[data-slot="novedades"]');
    if (novedadesZone) {
      const ocultarNovedades = esApi && !!soporte.novedades;
      novedadesZone.hidden = ocultarNovedades;
      if (ocultarNovedades) {
        const input = novedadesZone.querySelector('input[type="file"]');
        if (input) input.value = "";
      }
    }
  }

  fuenteRadios.forEach((radio) => radio.addEventListener("change", updateFuente));
  updateFuente();

  // --- Nombre de archivo seleccionado -------------------------------------
  form.querySelectorAll('input[type="file"]').forEach((input) => {
    input.addEventListener("change", () => {
      const display = input.closest(".tool-slot")?.querySelector("[data-file-name]");
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
      metaItem("Fuente", summary.fuente === "api" ? "Zoho API" : "Excel"),
      metaItem("Incidentes", String(summary.total_incidentes ?? 0)),
    );

    const sinIncidentes = summary.sin_incidentes === true || (summary.total_incidentes ?? 0) === 0;
    banner.className = `tool-banner ${sinIncidentes ? "is-ok" : "is-warn"}`;
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

    renderCobros(summary);
    btnDownload.hidden = sinIncidentes;
    result.hidden = false;
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function metaItem(label, value) {
    const item = document.createElement("div"); item.className = "tool-meta-item";
    const strong = document.createElement("strong"); strong.textContent = value || "—";
    const span = document.createElement("span"); span.textContent = label;
    item.append(strong, span); return item;
  }

  // --- Cobros: lista de Operaciones de la poliza en Zoho (Produccion) ------
  // Puede haber varias vigentes a la vez (por ramo, por cuota): se muestran
  // todas con su vigencia para que quien concilia elija a cual ir, en vez de
  // asumir una sola.
  function renderCobros(summary) {
    if (!cobrosSection) return;
    const cobros = Array.isArray(summary.cobros) ? summary.cobros : null;
    if (cobros === null) {
      cobrosSection.hidden = true;
      return;
    }
    cobrosSection.hidden = false;
    cobrosList.replaceChildren(...cobros.map(cobroItem));
    if (cobrosEmpty) cobrosEmpty.hidden = cobros.length > 0;
    if (polizaLink) {
      polizaLink.hidden = !summary.poliza_url;
      polizaLink.href = summary.poliza_url || "#";
    }
  }

  function cobroItem(cobro) {
    const li = document.createElement("li"); li.className = "conc-cobro-item";
    const link = document.createElement("a");
    link.href = cobro.url; link.target = "_blank"; link.rel = "noopener noreferrer";
    const nombre = document.createElement("strong");
    nombre.textContent = cobro.nombre || "Operación sin nombre";
    const detalle = document.createElement("span");
    detalle.textContent = [cobro.ramo, cobro.numero_cuota ? `Cuota ${cobro.numero_cuota}` : ""]
      .filter(Boolean).join(" · ") || "Sin ramo/cuota registrados";
    const vigencia = document.createElement("span");
    vigencia.textContent = (cobro.vigencia_inicio || cobro.vigencia_fin)
      ? `Vigencia: ${cobro.vigencia_inicio || "s/f"} – ${cobro.vigencia_fin || "s/f"}`
      : "Vigencia no disponible";
    link.append(nombre, detalle, vigencia);
    li.append(link);
    return li;
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
    updateFuente();
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }
});
