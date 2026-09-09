document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-conc-form]");
  if (!form) return;

  const companiasCatalogEl = document.getElementById("conc-companias-catalog");
  let companiasCatalog = {};
  try { companiasCatalog = JSON.parse(companiasCatalogEl?.textContent || "{}"); } catch (_) { companiasCatalog = {}; }

  // Nombre visible por codigo de compañía, aplanado del catalogo por ramo
  // (el mismo codigo de compañía siempre tiene el mismo nombre en todos los
  // ramos que la soportan): usado para mostrar "Compañía" en el resultado.
  const nombresCompania = {};
  Object.values(companiasCatalog).forEach((companias) => {
    (companias || []).forEach(([codigo, nombre]) => { nombresCompania[codigo] = nombre; });
  });

  const catalogEl = document.getElementById("conc-slots-catalog");
  let catalog = {};
  try { catalog = JSON.parse(catalogEl?.textContent || "{}"); } catch (_) { catalog = {}; }

  const novedadesCatalogEl = document.getElementById("conc-novedades-catalog");
  let novedadesCatalog = {};
  try { novedadesCatalog = JSON.parse(novedadesCatalogEl?.textContent || "{}"); } catch (_) { novedadesCatalog = {}; }

  const ramoSelect = form.querySelector('select[name="ramo"]');
  const companiaSelect = form.querySelector('select[name="compania"]');
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
  let lastSummary = {};

  // --- Compañía dinámica por ramo ------------------------------------------
  // El formato del archivo de cobro lo define la aseguradora, no el ramo: al
  // cambiar de ramo se repueblan las compañías que ese ramo tiene configuradas
  // (hoy siempre Sura) antes de refrescar los slots, que dependen de ambos.
  function updateCompanias(ramo) {
    if (!companiaSelect) return;
    const seleccionPrevia = companiaSelect.value;
    const companias = companiasCatalog[ramo] || [];
    companiaSelect.replaceChildren(...companias.map(([codigo, nombre]) => {
      const option = document.createElement("option");
      option.value = codigo; option.textContent = nombre;
      return option;
    }));
    const sigueValida = companias.some(([codigo]) => codigo === seleccionPrevia);
    if (sigueValida) companiaSelect.value = seleccionPrevia;
  }

  // --- Slots dinámicos por ramo + compañía ---------------------------------
  function updateSlots(ramo, compania) {
    const slots = (catalog[ramo] || {})[compania] || [];
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

  ramoSelect?.addEventListener("change", () => {
    updateCompanias(ramoSelect.value);
    updateSlots(ramoSelect.value, companiaSelect?.value);
    updateNovedades();
  });
  companiaSelect?.addEventListener("change", () => {
    updateSlots(ramoSelect?.value, companiaSelect.value);
    updateNovedades();
  });
  if (ramoSelect) {
    updateCompanias(ramoSelect.value);
    updateSlots(ramoSelect.value, companiaSelect?.value);
  }

  // --- Novedades: se oculta el upload solo si el (ramo, compañía) la resuelve
  // por Zoho API (vg_deudores/Sura no: su novedad viene del banco, sigue
  // pidiendo el archivo).
  function updateNovedades() {
    const ramo = ramoSelect?.value;
    const compania = companiaSelect?.value;
    const ocultarNovedades = !!(novedadesCatalog[ramo] || {})[compania];
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
      metaItem("Compañía", nombresCompania[summary.compania] || summary.compania),
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
    lastSummary = summary || {};
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
    option.dataset.id = cobro.id;
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

  // --- Prellenado del Cobro y asignación de Número crédito antes de facturar
  // Dos conveniencias independientes que corren justo al hacer clic, antes de
  // abrir el enlace: (1) si el recibo (PDF) se extrajo con éxito, prellena
  // Certificado/Fecha expedición/Pago total cuota en Zoho Producción; (2) si
  // el cobro (VG Deudores, export de Riesgos vigentes) trajo 'Código de
  // Crédito' para algún riesgo, asigna el "Número crédito" en Zoho para los
  // que aún no lo tengan. Ninguna bloquea a la otra ni es requisito para
  // facturar: si ambas están deshabilitadas o no aplican, el enlace se
  // comporta como antes (navega directo).
  btnFacturar?.addEventListener("click", (event) => {
    const recibo = lastSummary.recibo_cobro;
    const poliza = lastSummary.poliza;
    const cobroId = cobrosSelect?.selectedOptions?.[0]?.dataset?.id;
    const prellenarUrl = btnFacturar.dataset.prellenarUrl;
    const puedePrellenar = !!(lastSummary.cobro_prefill_enabled && recibo && poliza && cobroId && prellenarUrl);

    const pendientes = Array.isArray(lastSummary.creditos_pendientes) ? lastSummary.creditos_pendientes : [];
    const actualizarCreditoUrl = btnFacturar.dataset.actualizarCreditoUrl;
    const puedeActualizarCredito = !!(
      lastSummary.credito_update_enabled && poliza && pendientes.length && actualizarCreditoUrl
    );

    if (!puedePrellenar && !puedeActualizarCredito) {
      return; // deja el enlace normal (target="_blank") seguir su curso
    }

    // La pestaña se abre en blanco de forma sincrona (dentro del gesto del
    // clic) para no chocar con el bloqueador de pop-ups del navegador; si el
    // navegador la bloquea igual, se deja el enlace normal seguir su curso.
    const ventana = window.open("", "_blank");
    if (!ventana) return;
    event.preventDefault();
    const destino = btnFacturar.href;

    const tareas = [];
    if (puedePrellenar) {
      tareas.push(
        fetch(prellenarUrl, {
          method: "POST", cache: "no-store",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
          body: JSON.stringify({
            poliza, cobro_id: cobroId,
            certificado: recibo.certificado,
            fecha_expedicion: recibo.fecha_expedicion,
            pago_total_cuota: recibo.pago_total_cuota,
          }),
        })
          .then((response) => { if (!response.ok) console.warn("No fue posible prellenar el cobro en Zoho."); })
          .catch(() => { console.warn("No fue posible prellenar el cobro en Zoho."); })
      );
    }
    if (puedeActualizarCredito) {
      tareas.push(
        fetch(actualizarCreditoUrl, {
          method: "POST", cache: "no-store",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
          body: JSON.stringify({ poliza, pendientes }),
        })
          .then((response) => { if (!response.ok) console.warn("No fue posible actualizar el Número crédito en Zoho."); })
          .catch(() => { console.warn("No fue posible actualizar el Número crédito en Zoho."); })
      );
    }

    Promise.allSettled(tareas).finally(() => { ventana.location = destino; });
  });

  function csrfToken() {
    return form.querySelector('[name="csrfmiddlewaretoken"]')?.value || "";
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
    updateNovedades();
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }
});
