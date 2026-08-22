document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-conc-form]");
  if (!form) return;

  const catalogEl = document.getElementById("conc-slots-catalog");
  let catalog = {};
  try { catalog = JSON.parse(catalogEl?.textContent || "{}"); } catch (_) { catalog = {}; }

  const novedadesCatalogEl = document.getElementById("conc-novedades-catalog");
  let novedadesCatalog = {};
  try { novedadesCatalog = JSON.parse(novedadesCatalogEl?.textContent || "{}"); } catch (_) { novedadesCatalog = {}; }

  const ramoSelect = form.querySelector('select[name="ramo"]');
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
  const cobrosField = document.querySelector("[data-cobros-field]");
  const cobrosSelect = document.querySelector("[data-cobros-select]");
  const btnFacturar = document.querySelector("[data-action-facturar]");
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

  ramoSelect?.addEventListener("change", () => { updateSlots(ramoSelect.value); updateNovedades(); });
  if (ramoSelect) updateSlots(ramoSelect.value);

  // --- Novedades: se oculta el upload solo si el ramo la resuelve por Zoho
  // API (vg_deudores no: su novedad viene del banco, sigue pidiendo el archivo).
  function updateNovedades() {
    const ramo = ramoSelect?.value;
    const ocultarNovedades = !!novedadesCatalog[ramo];
    const novedadesZone = form.querySelector('.tool-slot[data-slot="novedades"]');
    if (novedadesZone) {
      novedadesZone.hidden = ocultarNovedades;
      if (ocultarNovedades) {
        const input = novedadesZone.querySelector('input[type="file"]');
        if (input) input.value = "";
      }
    }
  }

  updateNovedades();

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
    const advertencias = summary.total_advertencias ?? 0;
    meta.replaceChildren(
      metaItem("Ramo", summary.ramo),
      metaItem("Periodo", summary.periodo),
      metaItem("Póliza", summary.poliza),
      metaItem("Incidentes", String(summary.total_incidentes ?? 0)),
      metaItem("Advertencias", String(advertencias)),
    );

    // sin_incidentes solo considera incidentes bloqueantes: las advertencias
    // (p. ej. recibo/PDF sin validar, que por ahora es solo informativo) nunca
    // impiden continuar ni acceder a los cobros/enlace de facturación en Zoho.
    const sinIncidentes = summary.sin_incidentes === true || (summary.total_incidentes ?? 0) === 0;
    banner.className = `tool-banner ${sinIncidentes ? "is-ok" : "is-warn"}`;
    if (sinIncidentes) {
      banner.textContent = advertencias > 0
        ? `Conciliación sin incidentes bloqueantes. Hay ${advertencias} advertencia(s) informativa(s) (ver detalle); puede continuar con la facturación.`
        : "Conciliación sin incidentes. Todo cuadra: puede continuar con la facturación.";
    } else {
      banner.textContent = `Se encontraron ${summary.total_incidentes} incidente(s). Revise y descargue el detalle antes de facturar.`;
    }

    const porTipo = summary.por_tipo || {};
    if (Object.keys(porTipo).length) {
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
    btnDownload.hidden = sinIncidentes && advertencias === 0;
    result.hidden = false;
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function metaItem(label, value) {
    const item = document.createElement("div"); item.className = "tool-meta-item";
    const strong = document.createElement("strong"); strong.textContent = value || "—";
    const span = document.createElement("span"); span.textContent = label;
    item.append(strong, span); return item;
  }

  // --- Cobros: Operaciones de la poliza en Zoho ----------------------------
  // Puede haber varias vigentes a la vez (por ramo, por cuota): vienen del
  // backend ordenadas por vigencia mas reciente primero, asi que ese es el
  // seleccionado por defecto; quien concilia puede elegir otra en el dropdown.
  function renderCobros(summary) {
    if (!cobrosSection) return;
    const cobros = Array.isArray(summary.cobros) ? summary.cobros : null;
    if (cobros === null) {
      cobrosSection.hidden = true;
      return;
    }
    cobrosSection.hidden = false;
    const hayCobros = cobros.length > 0;
    if (cobrosEmpty) cobrosEmpty.hidden = hayCobros;
    if (cobrosField) cobrosField.hidden = !hayCobros;
    if (cobrosSelect) cobrosSelect.replaceChildren(...cobros.map(cobroOption));
    if (btnFacturar) {
      btnFacturar.hidden = !hayCobros;
      btnFacturar.href = hayCobros ? cobros[0].url : "#";
    }
    if (polizaLink) {
      polizaLink.hidden = !summary.poliza_url;
      polizaLink.href = summary.poliza_url || "#";
    }
  }

  function cobroOption(cobro) {
    const option = document.createElement("option");
    option.value = cobro.url;
    const cuota = cobro.numero_cuota ? `Cuota ${cobro.numero_cuota}` : "";
    const vigencia = (cobro.vigencia_inicio || cobro.vigencia_fin)
      ? `${cobro.vigencia_inicio || "s/f"} – ${cobro.vigencia_fin || "s/f"}`
      : "vigencia no disponible";
    option.textContent = [cobro.nombre || "Operación sin nombre", cuota, vigencia].filter(Boolean).join(" - ");
    return option;
  }

  cobrosSelect?.addEventListener("change", () => {
    if (btnFacturar && cobrosSelect.value) btnFacturar.href = cobrosSelect.value;
  });

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
    updateNovedades();
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }
});
