(() => {
  "use strict";
  const form = document.querySelector("[data-individual-form]");
  const schemaNode = document.getElementById("individual-schema");
  if (!form || !schemaNode) return;
  const schema = JSON.parse(schemaNode.textContent);
  const payload = form.querySelector("#id_items_payload");
  let groups;
  try { groups = JSON.parse(payload.value || "{}"); } catch (_) { groups = schema.initial; }
  // Keep selected File objects keyed by the stable entity key while a
  // repeatable row is edited/re-rendered.  The server still receives the
  // actual multipart input; this only prevents a visual re-render from
  // silently dropping the user's selection.
  const selectedFiles = new Map();
  const restoreFile = (input, key) => {
    const file = selectedFiles.get(key);
    if (!file || typeof DataTransfer === "undefined") return;
    try {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
    } catch (_) { /* browsers that forbid programmatic file assignment */ }
  };
  const dialog = document.querySelector("[data-item-dialog]");
  const fieldsHost = dialog.querySelector("[data-dialog-fields]");
  let activeGroup = null;
  let activeIndex = null;

  const groupSchema = key => schema.repeatables.find(group => group.key === key);
  const escapeLabel = value => String(value || "").trim() || "Sin completar";
  const displayValue = (value, field) => {
    if (field.kind === "checkbox") return value ? "Sí" : "No";
    return String(value || "").trim() || "Sin información";
  };
  const placeholders = {
    first_name: "Ej. Juan Carlos", last_name: "Ej. Pérez Gómez",
    name: "Ej. Juan Carlos Pérez", id_type: "Seleccione...",
    document: "Ej. 1030123456", birth_date: "AAAA-MM-DD",
    email: "Ej. usuario@correo.com", phone: "Ej. 3001234567",
    insured_first_name: "Ej. Juan Carlos", insured_last_name: "Ej. Pérez Gómez",
    insured_document: "Ej. 1030123456", insured_birth_date: "AAAA-MM-DD",
    insured_email: "Ej. usuario@correo.com", insured_phone: "Ej. 3001234567",
    brand: "Ej. Renault", line: "Ej. Duster", displacement: "Ej. 1600",
    model: "Ej. 2026", plate: "Ej. ABC123", city: "Ej. Medellín",
  };
  const newEntityKey = (group, index) => {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return `${group}-${window.crypto.randomUUID()}`;
    return `${group}-${Date.now().toString(36)}-${index}-${Math.random().toString(36).slice(2, 8)}`;
  };
  const sync = () => {
    const serialized = Object.fromEntries(Object.entries(groups).map(([key, rows]) => [key, rows]));
    if (schema.slug === "salud" && Array.isArray(serialized.people)) {
      const requester = {
        is_requester: true,
        first_name: form.querySelector('[name="first_name"]')?.value.trim() || "",
        last_name: form.querySelector('[name="last_name"]')?.value.trim() || "",
        id_type: form.querySelector('[name="requester_id_type"]')?.value.trim() || "",
        document: form.querySelector('[name="requester_document"]')?.value.trim() || "",
        birth_date: form.querySelector('[name="requester_birth_date"]')?.value.trim() || "",
        email: form.querySelector('[name="requester_email"]')?.value.trim() || "",
        phone: form.querySelector('[name="requester_phone"]')?.value.trim() || "",
        employment_relationship: "Empleado",
        relationship: "Titular",
        currently_health_insured: "No",
      };
      serialized.people = [requester, ...serialized.people.filter(row => !row.is_requester)];
    }
    payload.value = JSON.stringify(serialized);
    const count = Object.values(serialized).reduce((total, rows) => total + rows.length, 0);
    form.querySelector("[data-review-summary]").textContent = `${count} elemento${count === 1 ? "" : "s"} listo${count === 1 ? "" : "s"} para enviar.`;
  };
  const affiliateInput = form.querySelector('input[name="affiliate_document"]');
  if (affiliateInput) {
    const clear = document.createElement("button");
    clear.type = "button"; clear.className = "button-link button-link--secondary"; clear.textContent = "Quitar";
    clear.addEventListener("click", () => { affiliateInput.value = ""; });
    affiliateInput.parentElement.appendChild(clear);
  }
  const render = key => {
    const definition = groupSchema(key);
    const host = form.querySelector(`[data-item-list="${key}"]`);
    host.replaceChildren();
    const rows = (groups[key] || []).map((row, index) => ({row, index})).filter(({row}) => !(schema.slug === "salud" && key === "people" && row.is_requester));
    if (schema.slug === "salud" && key === "people") {
      const primary = {
        first_name: form.querySelector('[name="first_name"]')?.value.trim() || "",
        last_name: form.querySelector('[name="last_name"]')?.value.trim() || "",
        id_type: form.querySelector('[name="requester_id_type"]')?.value.trim() || "",
        document: form.querySelector('[name="requester_document"]')?.value.trim() || "",
        birth_date: form.querySelector('[name="requester_birth_date"]')?.value.trim() || "",
        email: form.querySelector('[name="requester_email"]')?.value.trim() || "",
        phone: form.querySelector('[name="requester_phone"]')?.value.trim() || "",
      };
      const hasPrimary = Object.values(primary).some(value => value);
      if (hasPrimary) {
        const card = document.createElement("article"); card.className = "repeatable-card repeatable-card--primary";
        const title = document.createElement("div"); title.className = "repeatable-card__heading";
        title.innerHTML = "<strong>Asegurado principal</strong><span>Mismo afiliado</span>";
        const summary = document.createElement("dl"); summary.className = "repeatable-summary";
        [["Nombres", primary.first_name], ["Apellidos", primary.last_name], ["Identificación", `${primary.id_type} ${primary.document}`.trim()], ["Fecha de nacimiento", primary.birth_date], ["Correo", primary.email], ["Teléfono", primary.phone]].forEach(([labelText, value]) => {
          const item = document.createElement("div"); const label = document.createElement("dt"); label.textContent = labelText;
          const content = document.createElement("dd"); content.textContent = value || "Sin información"; item.append(label, content); summary.appendChild(item);
        });
        card.append(title, summary); host.append(card);
      }
    }
    rows.forEach(({row, index}) => {
      row.entity_key ||= newEntityKey(key, index);
      const card = document.createElement("article"); card.className = "repeatable-card";
      const title = document.createElement("div");
      const first = definition.fields.find(field => row[field.key]);
      title.className = "repeatable-card__heading";
      title.innerHTML = `<strong>${definition.singular} ${index + 1}</strong><span></span>`;
      title.querySelector("span").textContent = first ? escapeLabel(row[first.key]) : "Pendiente de completar";
      const summary = document.createElement("dl");
      summary.className = "repeatable-summary";
      definition.fields.forEach(field => {
        if (field.key === "entity_key") return;
        const item = document.createElement("div");
        const label = document.createElement("dt"); label.textContent = field.label;
        const value = document.createElement("dd"); value.textContent = displayValue(row[field.key], field);
        item.append(label, value); summary.appendChild(item);
      });
      const actions = document.createElement("div"); actions.className = "repeatable-actions";
      const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "Editar"; edit.addEventListener("click", () => openDialog(key, index));
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Eliminar"; remove.disabled = groups[key].length <= definition.minimum; remove.addEventListener("click", () => { groups[key].splice(index, 1); render(key); sync(); });
      if (key === "vehicles" || key === "people") {
        const sameRequester = (key === "vehicles" && Boolean(row.insured_same_as_requester)) || (key === "people" && Boolean(row.is_requester));
        const documentLabel = key === "vehicles" ? "Adjuntar matrícula o tarjeta de propiedad" : "Adjuntar cédula";
        if (key === "vehicles" || !sameRequester) {
          const documentField = document.createElement("div");
          documentField.className = "entity-document-field field";
          const label = document.createElement("label");
          label.textContent = documentLabel;
          const input = document.createElement("input");
          input.type = "file";
          input.name = `entity_attachment_${row.entity_key}`;
          input.accept = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";
          input.setAttribute("data-entity-file", row.entity_key);
          label.appendChild(input);
          documentField.appendChild(label);
          restoreFile(input, row.entity_key);
          const fileName = document.createElement("small");
          fileName.className = "entity-document-name";
          const refreshFileName = () => {
            fileName.textContent = input.files && input.files[0] ? `📎 ${input.files[0].name}` : "";
          };
          refreshFileName();
          input.addEventListener("change", () => {
            if (input.files && input.files[0]) selectedFiles.set(row.entity_key, input.files[0]);
            else selectedFiles.delete(row.entity_key);
            refreshFileName();
          });
          documentField.appendChild(fileName);
          const clear = document.createElement("button");
          clear.type = "button"; clear.className = "button-link button-link--secondary"; clear.textContent = "Quitar";
          clear.addEventListener("click", () => { input.value = ""; selectedFiles.delete(row.entity_key); refreshFileName(); });
          documentField.appendChild(clear);
          card.appendChild(documentField);
          if (key === "vehicles" && !sameRequester) {
            const insuredField = document.createElement("div");
            insuredField.className = "entity-document-field field";
            const insuredLabel = document.createElement("label");
            insuredLabel.textContent = "Adjuntar cédula del asegurado";
            const insuredInput = document.createElement("input");
            insuredInput.type = "file";
            insuredInput.name = `entity_attachment_${row.entity_key}-insured`;
            insuredInput.accept = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";
            insuredInput.setAttribute("data-entity-file", `${row.entity_key}-insured`);
            insuredLabel.appendChild(insuredInput);
            insuredField.appendChild(insuredLabel);
            restoreFile(insuredInput, `${row.entity_key}-insured`);
            const insuredName = document.createElement("small");
            insuredName.className = "entity-document-name";
            insuredName.textContent = insuredInput.files && insuredInput.files[0] ? `📎 ${insuredInput.files[0].name}` : "";
            insuredInput.addEventListener("change", () => {
              if (insuredInput.files && insuredInput.files[0]) selectedFiles.set(`${row.entity_key}-insured`, insuredInput.files[0]);
              else selectedFiles.delete(`${row.entity_key}-insured`);
              insuredName.textContent = insuredInput.files && insuredInput.files[0] ? `📎 ${insuredInput.files[0].name}` : "";
            });
            insuredField.appendChild(insuredName);
            const clearInsured = document.createElement("button");
            clearInsured.type = "button"; clearInsured.className = "button-link button-link--secondary"; clearInsured.textContent = "Quitar";
            clearInsured.addEventListener("click", () => { insuredInput.value = ""; selectedFiles.delete(`${row.entity_key}-insured`); insuredName.textContent = ""; });
            insuredField.appendChild(clearInsured);
            card.appendChild(insuredField);
          }
        }
      }
      actions.append(edit, remove);
      card.prepend(title, summary);
      card.append(actions);
      host.append(card);
    });
    const addButton = form.querySelector(`[data-add-item="${key}"]`);
    if (addButton && key === "vehicles") addButton.textContent = groups[key]?.length ? "+ Agregar otro vehículo" : "+ Agregar vehículo";
    sync();
  };
  const openDialog = (key, index = null) => {
    activeGroup = key; activeIndex = index;
    const definition = groupSchema(key); const row = index === null ? {} : groups[key][index];
    dialog.querySelector("[data-dialog-title]").textContent = `${index === null ? "Agregar" : "Editar"} ${definition.singular}`;
    fieldsHost.replaceChildren();
    const applyConditions = () => {
      definition.fields.forEach(field => {
        if (!field.show_when || !field.show_when.length) return;
        const [dependency, expected] = field.show_when;
        const target = fieldsHost.querySelector(`[name="${field.key}"]`);
        const source = fieldsHost.querySelector(`[name="${dependency}"]`);
        const visible = source && source.value === expected;
        target.closest(".field").hidden = !visible;
        if (!visible) target.value = "";
      });
    };
    const isChecked = name => {
      const input = fieldsHost.querySelector(`[name="${name}"]`);
      return Boolean(input && input.checked);
    };
    const copyRequester = () => {
      const sameVehicle = key === "vehicles" && isChecked("insured_same_as_requester");
      const samePerson = key === "people" && (isChecked("is_requester") || fieldsHost.querySelector('[name="use_requester"]')?.value === "Sí");
      if (!sameVehicle && !samePerson) {
        fieldsHost.querySelectorAll("[data-requester-copy]").forEach(input => { input.readOnly = false; input.disabled = false; });
        return;
      }
      const pairs = {
        first_name: "first_name", last_name: "last_name", id_type: "requester_id_type",
        document: "requester_document", birth_date: "requester_birth_date",
        email: "requester_email", phone: "requester_phone",
        insured_first_name: "first_name", insured_last_name: "last_name",
        insured_id_type: "requester_id_type", insured_document: "requester_document",
        insured_birth_date: "requester_birth_date", insured_email: "requester_email",
        insured_phone: "requester_phone", insured_name: "__full_name__",
      };
      Object.entries(pairs).forEach(([target, source]) => {
        const targetInput = fieldsHost.querySelector(`[name="${target}"]`);
        if (!targetInput) return;
        if (source === "__full_name__") {
          targetInput.value = [form.querySelector('[name="first_name"]')?.value, form.querySelector('[name="last_name"]')?.value].filter(Boolean).join(" ");
        } else {
          const sourceInput = form.querySelector(`[name="${source}"]`);
          if (sourceInput) targetInput.value = sourceInput.value;
        }
        targetInput.dataset.requesterCopy = "true";
        targetInput.readOnly = true;
        // Disabled controls remain part of the JSON row built below; this
        // prevents re-entry while keeping the copied value persisted.
        targetInput.disabled = true;
      });
    };
    definition.fields.forEach(field => {
      const wrapper = document.createElement("div"); wrapper.className = "field";
      const label = document.createElement("label"); label.textContent = `${field.label}${field.required ? " *" : ""}`;
      let input;
      if (field.kind === "choice") { input = document.createElement("select"); input.append(new Option("Seleccione", "")); field.choices.forEach(choice => input.append(new Option(choice, choice))); }
      else { input = document.createElement("input"); input.type = field.kind === "checkbox" ? "checkbox" : ({email:"email",date:"date",tel:"tel"}[field.kind] || "text"); }
      input.name = field.key; input.id = `dialog-${key}-${index === null ? "new" : index}-${field.key}`; label.htmlFor = input.id;
      input.required = field.required; input.maxLength = 180; input.value = row[field.key] || "";
      if (placeholders[field.key] && field.kind !== "choice") input.placeholder = placeholders[field.key];
      if (field.kind === "checkbox") input.checked = Boolean(row[field.key]);
      if (key === "people" && field.key === "is_requester") {
        const alreadyAdded = (groups.people || []).some((item, itemIndex) => itemIndex !== index && Boolean(item.is_requester || item.use_requester));
        if (alreadyAdded) {
          input.disabled = true;
          label.textContent += " (El solicitante ya fue agregado)";
        }
      }
      if (["first_name","last_name","id_type","document","birth_date","email","phone","insured_name","insured_id_type","insured_document","insured_first_name","insured_last_name","insured_birth_date","insured_email","insured_phone"].includes(field.key)) input.dataset.requesterCopy = "true";
      wrapper.append(label, input); fieldsHost.append(wrapper);
    });
    fieldsHost.querySelectorAll("input,select").forEach(input => input.addEventListener("change", () => { copyRequester(); applyConditions(); }));
    const syncVehiclePlate = () => {
      if (key !== "vehicles") return;
      const zeroKm = fieldsHost.querySelector('[name="zero_km"]');
      const plate = fieldsHost.querySelector('[name="plate"]');
      if (!zeroKm || !plate) return;
      let error = fieldsHost.querySelector("[data-plate-error]");
      if (!error) {
        error = document.createElement("small"); error.dataset.plateError = "true";
        error.className = "field-error"; error.hidden = true;
        plate.parentElement.appendChild(error);
      }
      const updateError = () => {
        const required = zeroKm.value === "No";
        plate.required = required;
        plate.placeholder = required ? "Ej. ABC123" : "No obligatorio para vehículo 0 km";
        error.hidden = !(required && !plate.value.trim());
        error.textContent = error.hidden ? "" : "La placa es obligatoria cuando el vehículo no es 0 km.";
      };
      plate.addEventListener("input", updateError);
      zeroKm.addEventListener("change", updateError);
      updateError();
    };
    copyRequester();
    applyConditions();
    syncVehiclePlate();
    dialog.showModal();
  };
  form.querySelectorAll("[data-add-item]").forEach(button => button.addEventListener("click", () => openDialog(button.dataset.addItem)));
  dialog.querySelectorAll("[data-close-dialog]").forEach(button => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("[data-dialog-form]").addEventListener("submit", event => {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const row = Object.fromEntries([...fieldsHost.querySelectorAll("input,select")].map(input => [input.name, input.type === "checkbox" ? input.checked : input.value.trim()]));
    const previous = activeIndex === null ? null : groups[activeGroup][activeIndex];
    row.entity_key = previous?.entity_key || newEntityKey(activeGroup, activeIndex === null ? groups[activeGroup].length : activeIndex);
    if (activeIndex === null) groups[activeGroup].push(row); else groups[activeGroup][activeIndex] = row;
    render(activeGroup); dialog.close();
  });
  schema.repeatables.forEach(definition => { groups[definition.key] ||= []; render(definition.key); });
  if (schema.slug === "salud") {
    ["first_name", "last_name", "requester_id_type", "requester_document", "requester_birth_date", "requester_email", "requester_phone"].forEach(name => {
      const input = form.querySelector(`[name="${name}"]`);
      if (input) input.addEventListener("input", () => render("people"));
    });
  }
  form.addEventListener("submit", sync);
})();
