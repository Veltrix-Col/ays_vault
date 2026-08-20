(() => {
  "use strict";
  const form = document.querySelector("[data-individual-form]");
  const schemaNode = document.getElementById("individual-schema");
  if (!form || !schemaNode) return;
  const schema = JSON.parse(schemaNode.textContent);
  const payload = form.querySelector("#id_items_payload");
  let groups;
  try { groups = JSON.parse(payload.value || "{}"); } catch (_) { groups = schema.initial; }
  const dialog = document.querySelector("[data-item-dialog]");
  const fieldsHost = dialog.querySelector("[data-dialog-fields]");
  let activeGroup = null;
  let activeIndex = null;

  const groupSchema = key => schema.repeatables.find(group => group.key === key);
  const escapeLabel = value => String(value || "").trim() || "Sin completar";
  const sync = () => {
    payload.value = JSON.stringify(groups);
    const count = Object.values(groups).reduce((total, rows) => total + rows.length, 0);
    form.querySelector("[data-review-summary]").textContent = `${count} elemento${count === 1 ? "" : "s"} listo${count === 1 ? "" : "s"} para enviar.`;
  };
  const render = key => {
    const definition = groupSchema(key);
    const host = form.querySelector(`[data-item-list="${key}"]`);
    host.replaceChildren();
    (groups[key] || []).forEach((row, index) => {
      const card = document.createElement("article"); card.className = "repeatable-card";
      const title = document.createElement("div");
      const first = definition.fields.find(field => row[field.key]);
      title.innerHTML = `<strong>${definition.singular} ${index + 1}</strong><span></span>`;
      title.querySelector("span").textContent = first ? escapeLabel(row[first.key]) : "Pendiente de completar";
      const actions = document.createElement("div"); actions.className = "repeatable-actions";
      const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "Editar"; edit.addEventListener("click", () => openDialog(key, index));
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Eliminar"; remove.disabled = groups[key].length <= definition.minimum; remove.addEventListener("click", () => { groups[key].splice(index, 1); render(key); sync(); });
      actions.append(edit, remove); card.append(title, actions); host.append(card);
    });
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
    copyRequester();
    applyConditions();
    dialog.showModal();
  };
  form.querySelectorAll("[data-add-item]").forEach(button => button.addEventListener("click", () => openDialog(button.dataset.addItem)));
  dialog.querySelectorAll("[data-close-dialog]").forEach(button => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("[data-dialog-form]").addEventListener("submit", event => {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const row = Object.fromEntries([...fieldsHost.querySelectorAll("input,select")].map(input => [input.name, input.type === "checkbox" ? input.checked : input.value.trim()]));
    if (activeIndex === null) groups[activeGroup].push(row); else groups[activeGroup][activeIndex] = row;
    render(activeGroup); dialog.close();
  });
  schema.repeatables.forEach(definition => { groups[definition.key] ||= [{}]; render(definition.key); });
  form.addEventListener("submit", sync);
})();
