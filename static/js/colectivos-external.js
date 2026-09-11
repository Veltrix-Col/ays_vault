(() => {
  "use strict";

  const form = document.querySelector("[data-external-response]");
  if (!form) return;

  const filter = form.querySelector("[data-row-filter]");
  const progress = form.querySelector("[data-progress-count]");
  const visible = form.querySelector("[data-visible-count]");
  const backdrop = form.querySelector("[data-drawer-backdrop]");
  const tables = [...form.querySelectorAll("[data-functional-table]")];
  let activeDrawer = null;
  let activeTrigger = null;
  let drawerSnapshot = [];

  function normalize(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es");
  }

  function recordChanged(record) {
    if (record.dataset.preparedRetirement === "true") return true;
    const action = record.querySelector("[data-row-action]");
    if (!action || action.value !== "RETIRAR") return false;
    const retirementDate = record.querySelector("input[type='date'][name^='fecha_retiro_entity_']");
    return Boolean(retirementDate && retirementDate.value);
  }

  function matchedRecords(table) {
    const query = normalize(filter ? filter.value.trim() : "");
    return [...table.querySelectorAll("[data-functional-entity]")].filter((record) => {
      const summary = record.querySelector("[data-record-summary]");
      return !query || normalize(summary ? summary.textContent : "").includes(query);
    });
  }

  function refreshTable(table) {
    const records = [...table.querySelectorAll("[data-functional-entity]")];
    const matches = matchedRecords(table);
    const pagination = table.querySelector("[data-pagination]");
    const sizeControl = pagination?.querySelector("[data-page-size]");
    const pageSize = Number(sizeControl?.value || 25);
    const pageCount = Math.max(1, Math.ceil(matches.length / pageSize));
    let page = Number(table.dataset.page || 1);
    if (page > pageCount) page = pageCount;
    if (page < 1) page = 1;
    table.dataset.page = String(page);
    const start = (page - 1) * pageSize;
    const visiblePage = new Set(matches.slice(start, start + pageSize));

    records.forEach((record) => {
      record.hidden = !visiblePage.has(record);
      const state = record.querySelector("[data-change-state]");
      if (state) state.hidden = !recordChanged(record);
      const row = record.querySelector("[data-record-summary]");
      if (row) row.classList.toggle("functional-table__row--retirement-prepared", recordChanged(record));
    });

    const empty = table.querySelector("[data-filter-empty]");
    if (empty) empty.hidden = matches.length !== 0;
    if (pagination) {
      pagination.hidden = matches.length === 0;
      const status = pagination.querySelector("[data-page-status]");
      const previous = pagination.querySelector("[data-page-previous]");
      const next = pagination.querySelector("[data-page-next]");
      if (status) status.textContent = `${matches.length} registros · Página ${page} de ${pageCount}`;
      if (previous) previous.disabled = page <= 1;
      if (next) next.disabled = page >= pageCount;
    }
    return matches.length;
  }

  function refresh() {
    let visibleRows = 0;
    let changedRows = 0;
    tables.forEach((table) => {
      visibleRows += refreshTable(table);
      table.querySelectorAll("[data-functional-entity]").forEach((record) => {
        if (recordChanged(record)) changedRows += 1;
      });
    });
    changedRows += [...form.querySelectorAll("[data-include-action]")].filter((control) => control.value === "INCLUIR").length;
    if (visible) visible.textContent = String(visibleRows);
    if (progress) progress.textContent = String(changedRows);
  }

  function markModified(control) {
    const record = control.closest("[data-functional-entity]");
    // Los campos del modal sólo se convierten en una novedad al pulsar
    // «Preparar retiro». Así se evita contar un borrador incompleto y se
    // conserva la semántica RETIRAR para el POST definitivo.
    if (record || control.matches("[data-row-action]")) return;
  }

  function focusableElements(drawer) {
    return [...drawer.querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex='-1'])")]
      .filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
  }

  function openDrawer(trigger) {
    const drawer = document.getElementById(trigger.dataset.drawerOpen || "");
    if (!drawer) return;
    if (activeDrawer) closeDrawer(false);
    activeDrawer = drawer;
    activeTrigger = trigger;
    drawerSnapshot = [...drawer.querySelectorAll("input, select, textarea")].map((control) => ({
      control,
      value: control.value,
      checked: control.checked,
    }));
    drawer.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    if (backdrop) backdrop.hidden = false;
    document.body.classList.add("functional-drawer-open");
    requestAnimationFrame(() => {
      drawer.classList.add("is-open");
      backdrop?.classList.add("is-open");
      (drawer.querySelector("[data-drawer-title]") || focusableElements(drawer)[0])?.focus();
    });
  }

  function closeDrawer(restoreFocus = true, discardChanges = false) {
    if (!activeDrawer) return;
    const drawer = activeDrawer;
    const trigger = activeTrigger;
    if (discardChanges) {
      drawerSnapshot.forEach(({ control, value, checked }) => {
        control.value = value;
        control.checked = checked;
      });
    }
    drawer.classList.remove("is-open");
    backdrop?.classList.remove("is-open");
    document.body.classList.remove("functional-drawer-open");
    drawer.hidden = true;
    if (backdrop) backdrop.hidden = true;
    trigger?.setAttribute("aria-expanded", "false");
    activeDrawer = null;
    activeTrigger = null;
    drawerSnapshot = [];
    refresh();
    if (restoreFocus) trigger?.focus();
  }

  function addAnotherIncludeDrawer(drawer) {
    if (!drawer || !drawer.querySelector("[data-include-action]")) return;
    const originalId = drawer.id;
    const parent = drawer.parentElement;
    const siblings = [...parent.querySelectorAll("[id^='" + originalId + "']")];
    const index = siblings.length + 1;
    const clone = drawer.cloneNode(true);
    clone.id = `${originalId}__${index}`;
    clone.hidden = true;
    clone.classList.remove("is-open");
    clone.querySelectorAll("input, select, textarea").forEach((control) => {
      if (control.name && control.name.startsWith("include_")) {
        control.name = `${control.name}__${index}`;
      }
      if (control.type === "file") control.value = "";
      else if (control.type === "hidden") control.value = "";
      else control.value = "";
    });
    const title = clone.querySelector("[data-drawer-title]");
    if (title) title.textContent = "Solicitar otro ingreso";
    parent.appendChild(clone);
    const trigger = activeTrigger?.cloneNode(true);
    if (trigger) {
      trigger.dataset.drawerOpen = clone.id;
      trigger.textContent = "+ Agregar otro ingreso";
      trigger.setAttribute("aria-expanded", "false");
      parent.insertBefore(trigger, clone);
    }
  }

  if (filter) filter.addEventListener("input", () => {
    tables.forEach((table) => { table.dataset.page = "1"; });
    refresh();
  });

  form.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-drawer-open]");
    if (opener) {
      openDrawer(opener);
      return;
    }
    if (event.target.closest("[data-drawer-close]")) {
      closeDrawer(true, true);
      return;
    }
    if (event.target.closest("[data-drawer-done]")) {
      const doneButton = event.target.closest("[data-drawer-done]");
      // Retirement is intentionally a button (it only marks the row for the
      // final response submit), so it has no submit event to handle it.
      if (doneButton.matches("[data-edit-done]")) {
        const invalidInput = [...(activeDrawer?.querySelectorAll("[required]") || [])]
          .find((input) => !input.checkValidity());
        if (invalidInput) {
          invalidInput.reportValidity?.();
          invalidInput.focus();
          return;
        }
        const action = activeDrawer?.closest("[data-functional-entity]")?.querySelector("[data-row-action]");
        if (action) action.value = "RETIRAR";
        closeDrawer();
      } else {
        // Income preparation still uses the outer form's formaction.  Mark
        // the operation before the native submit, but only after validating
        // the drawer so an invalid attempt cannot mutate hidden state.
        const invalidInput = [...(activeDrawer?.querySelectorAll("[required]") || [])]
          .find((input) => !input.checkValidity());
        if (invalidInput) {
          event.preventDefault();
          invalidInput.reportValidity?.();
          invalidInput.focus();
          return;
        }
        const includeAction = activeDrawer?.querySelector("[data-include-action]");
        if (includeAction) {
          includeAction.value = "INCLUIR";
          addAnotherIncludeDrawer(activeDrawer);
        }
      }
      // The submit listener below is the final safety net for invalid forms;
      // this click handler prepares the marker only after its own drawer
      // validation has passed.
      return;
    }
    const previous = event.target.closest("[data-page-previous]");
    const next = event.target.closest("[data-page-next]");
    if (previous || next) {
      const table = event.target.closest("[data-functional-table]");
      const direction = next ? 1 : -1;
      table.dataset.page = String(Number(table.dataset.page || 1) + direction);
      refreshTable(table);
    }
  });

  // The drawer action is a submit control in this outer form.  Keep the
  // guard on submit as the authoritative boundary: depending on browser
  // event ordering, a delegated click handler may run without preventing the
  // native submit that follows it.  Only validate fields belonging to the
  // active drawer; the final-response declaration is unrelated to preparing
  // an individual income.
  form.addEventListener("submit", (event) => {
    if (!event.submitter?.matches("[data-drawer-done]")) return;
    const requiredInputs = activeDrawer ? [...activeDrawer.querySelectorAll("[required]")] : [];
    const invalidInput = requiredInputs.find((input) => !input.checkValidity());
    if (invalidInput) {
      event.preventDefault();
      invalidInput.reportValidity?.();
      invalidInput.focus();
      return;
    }
  });

  form.addEventListener("input", (event) => {
    markModified(event.target);
    refresh();
  });
  form.addEventListener("change", (event) => {
    markModified(event.target);
    if (event.target.matches("[data-page-size]")) {
      event.target.closest("[data-functional-table]").dataset.page = "1";
    }
    refresh();
  });

  form.addEventListener("keydown", (event) => {
    if (!activeDrawer) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeDrawer(true, true);
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = focusableElements(activeDrawer);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!focusable.includes(document.activeElement)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  backdrop?.addEventListener("click", () => closeDrawer(true, true));
  refresh();
})();

// Reuse the same drawer visual for editing a prepared ingress.  The table
// stays compact; this form is populated from the server-rendered draft row.
(() => {
  const drawer = document.querySelector("[data-prepared-edit-drawer]");
  const dataNode = document.getElementById("prepared-novelty-edit-data");
  if (!drawer || !dataNode) return;
  let rows = [];
  try { rows = JSON.parse(dataNode.textContent || "[]"); } catch (_) { rows = []; }
  const byId = new Map(rows.map((row) => [String(row.id), row]));
  const form = drawer.querySelector("[data-prepared-edit-form]");
  const uploadEndpoint = document.getElementById("prepared-attachment-endpoint")?.action || "";
  const close = () => { drawer.hidden = true; drawer.classList.remove("is-open"); };
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.checkValidity()) { form.reportValidity(); return; }
    const submit = form.querySelector("button[type=submit]");
    if (submit) submit.disabled = true;
    try {
      const editResponse = await fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" });
      if (!editResponse.ok) throw new Error("edit");
      const file = drawer.querySelector("[data-prepared-edit-attachment-panel] input[type=file]")?.files?.[0];
      if (file && uploadEndpoint) {
        const uploadData = new FormData();
        uploadData.append("change_id", form.querySelector("[data-prepared-edit-id]").value);
        uploadData.append("attachment", file);
        const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
        if (csrfInput?.value) uploadData.append("csrfmiddlewaretoken", csrfInput.value);
        const uploadResponse = await fetch(uploadEndpoint, { method: "POST", body: uploadData, credentials: "same-origin" });
        if (!uploadResponse.ok) throw new Error("attachment");
      }
      window.location.reload();
    } catch (_) {
      if (submit) submit.disabled = false;
    }
  });
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-prepared-edit-open]");
    if (opener) {
      const row = byId.get(String(opener.dataset.changeId)) || {};
      form.querySelector("[data-prepared-edit-id]").value = row.id || "";
      const values = {
        tipo_id: row.tipo_id, documento: row.document, nombres: row.first_name,
        apellidos: row.last_name, fecha_ingreso: row.entry_date,
        correo: row.email, phone: row.phone, parentesco: row.parentesco,
        observaciones: row.observations,
      };
      Object.entries(values).forEach(([name, value]) => {
        const field = form.querySelector(`[data-prepared-edit-field="${name}"]`);
        if (field) field.value = value || "";
      });
      const existing = drawer.querySelector(`[data-prepared-edit-attachment="${row.id}"]`);
      let attachmentPanel = drawer.querySelector("[data-prepared-edit-attachment-panel]");
      if (!attachmentPanel) {
        attachmentPanel = document.createElement("section");
        attachmentPanel.dataset.preparedEditAttachmentPanel = "";
        attachmentPanel.className = "prepared-edit-attachment";
        form.parentElement.insertBefore(attachmentPanel, form.nextSibling);
      }
      attachmentPanel.replaceChildren();
      const heading = document.createElement("h5"); heading.textContent = "Documento adjunto"; attachmentPanel.appendChild(heading);
      if (existing) {
        const current = existing.cloneNode(true); current.hidden = false; attachmentPanel.appendChild(current);
      } else {
        const empty = document.createElement("p"); empty.textContent = "Sin documento adjunto"; attachmentPanel.appendChild(empty);
      }
      const upload = document.createElement("div");
      upload.innerHTML = `<label>Elegir archivo nuevo<input type="file" name="attachment" accept=".pdf,.jpg,.jpeg,.png"></label>`;
      attachmentPanel.appendChild(upload);
      drawer.hidden = false;
      requestAnimationFrame(() => drawer.classList.add("is-open"));
      return;
    }
    if (event.target.closest("[data-prepared-edit-close]")) close();
  });
})();

// Prepared novelties stay compact until the client explicitly chooses an
// operation.  The server-rendered forms remain ordinary POST forms; this
// layer only controls disclosure and adds the optional fields already present
// in the draft payload.
(() => {
  const relationshipCatalogNode = document.getElementById("relationship-catalog-data");
  let relationshipChoices = [];
  try { relationshipChoices = relationshipCatalogNode ? JSON.parse(relationshipCatalogNode.textContent || "[]") : []; } catch (_) { relationshipChoices = []; }
  const dataNode = document.getElementById("prepared-novelty-edit-data");
  let editRows = [];
  try { editRows = dataNode ? JSON.parse(dataNode.textContent || "[]") : []; } catch (_) { editRows = []; }
  const byId = new Map(editRows.map((row) => [String(row.id), row]));

  document.querySelectorAll(".prepared-novelty").forEach((card) => {
    const changeId = String(card.dataset.functionalKey || "");
    const row = byId.get(changeId) || {};
    const uploadForm = card.querySelector('form[action*="upload_attachment"]');
    if (uploadForm) {
      const disclosure = document.createElement("details");
      disclosure.className = "prepared-novelty__document-editor";
      const summary = document.createElement("summary");
      summary.textContent = uploadForm.querySelector('button[type="submit"]')?.textContent.trim().startsWith("Cambiar")
        ? "Reemplazar documento" : "Adjuntar documento";
      disclosure.appendChild(summary);
      const label = uploadForm.querySelector("label");
      if (label) label.firstChild.textContent = "Elegir archivo";
      const submit = uploadForm.querySelector('button[type="submit"]');
      if (submit) submit.textContent = "Guardar nuevo documento";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "button-link button-link--secondary";
      cancel.textContent = "Cancelar";
      cancel.addEventListener("click", () => { disclosure.open = false; uploadForm.reset(); });
      uploadForm.appendChild(cancel);
      disclosure.appendChild(uploadForm);
      const marker = card.querySelector(".prepared-novelty__heading");
      (marker || card).after(disclosure);
    }

    const edit = card.querySelector(".prepared-novelty__edit");
    if (!edit) return;
    const summary = edit.querySelector("summary");
    if (summary) summary.textContent = "Editar información";
    const form = edit.querySelector("form");
    if (!form) return;
    const fieldPlaceholders = {
      documento: "Ej. 1030123456", document: "Ej. 1030123456",
      nombres: "Ej. Juan Carlos", apellidos: "Ej. Pérez Gómez",
      correo: "Ej. usuario@correo.com", email: "Ej. usuario@correo.com",
      phone: "Ej. 3001234567", telefono: "Ej. 3001234567",
      fecha_nacimiento: "AAAA-MM-DD", fecha_ingreso: "AAAA-MM-DD",
      fecha_retiro: "AAAA-MM-DD", observaciones: "Información adicional (opcional)",
    };
    const addField = (name, labelText, type, value) => {
      if (form.querySelector(`[name="${name}"]`)) return;
      const label = document.createElement("label");
      label.textContent = labelText;
      const input = document.createElement("input");
      input.name = name; input.type = type || "text"; input.value = value || "";
      if (fieldPlaceholders[name]) input.placeholder = fieldPlaceholders[name];
      label.appendChild(input);
      const submit = form.querySelector('button[type="submit"]');
      form.insertBefore(label, submit || null);
    };
    const addSelect = (name, labelText, value, choices) => {
      if (form.querySelector(`[name="${name}"]`)) return;
      const label = document.createElement("label");
      label.textContent = labelText;
      const select = document.createElement("select");
      select.name = name;
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Seleccione";
      select.appendChild(placeholder);
      (choices || []).forEach((choice) => {
        const option = document.createElement("option");
        option.value = Array.isArray(choice) ? choice[0] : choice.value;
        option.textContent = Array.isArray(choice) ? choice[1] : (choice.label || choice.value);
        select.appendChild(option);
      });
      select.value = value || "";
      label.appendChild(select);
      const submit = form.querySelector('button[type="submit"]');
      form.insertBefore(label, submit || null);
    };
    addField("correo", "Correo", "email", row.email);
    addField("phone", "Teléfono", "tel", row.phone);
    if (row.birth_date) addField("fecha_nacimiento", "Fecha de nacimiento", "date", row.birth_date);
    if (row.action === "RETIRAR") addField("fecha_retiro", "Fecha de retiro", "date", row.retirement_date);
    if ((row.contract_fields || []).includes("parentesco")) addSelect("parentesco", "Parentesco", row.parentesco, relationshipChoices);
    if ((row.contract_fields || []).includes("plate")) addField("placa", "Placa", "text", row.plate);
    if ((row.contract_fields || []).includes("model")) addField("modelo", "Modelo", "number", row.model);
    if (row.is_mobility) {
      addField("marca", "Marca / referencia", "text", row.brand);
      addField("clase", "Clase", "text", row.vehicle_class);
      addField("ciudad", "Ciudad", "text", row.city);
      addField("tipo_uso", "Tipo de uso", "text", row.use);
    }
    if (row.rol) addField("rol", "Rol", "text", row.rol);
    if (row.plan) addField("plan", "Plan", "text", row.plan);
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "button-link button-link--secondary";
    cancel.textContent = "Cancelar";
    cancel.addEventListener("click", () => { edit.open = false; });
    form.appendChild(cancel);
  });
})();
