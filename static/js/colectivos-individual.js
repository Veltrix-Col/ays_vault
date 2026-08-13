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
    definition.fields.forEach(field => {
      const wrapper = document.createElement("div"); wrapper.className = "field";
      const label = document.createElement("label"); label.textContent = `${field.label}${field.required ? " *" : ""}`;
      let input;
      if (field.kind === "choice") { input = document.createElement("select"); input.append(new Option("Seleccione", "")); field.choices.forEach(choice => input.append(new Option(choice, choice))); }
      else { input = document.createElement("input"); input.type = {email:"email",date:"date",tel:"tel"}[field.kind] || "text"; }
      input.name = field.key; input.required = field.required; input.maxLength = 180; input.value = row[field.key] || "";
      wrapper.append(label, input); fieldsHost.append(wrapper);
    });
    dialog.showModal();
  };
  form.querySelectorAll("[data-add-item]").forEach(button => button.addEventListener("click", () => openDialog(button.dataset.addItem)));
  dialog.querySelectorAll("[data-close-dialog]").forEach(button => button.addEventListener("click", () => dialog.close()));
  dialog.querySelector("[data-dialog-form]").addEventListener("submit", event => {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const row = Object.fromEntries([...fieldsHost.querySelectorAll("input,select")].map(input => [input.name, input.value.trim()]));
    if (activeIndex === null) groups[activeGroup].push(row); else groups[activeGroup][activeIndex] = row;
    render(activeGroup); dialog.close();
  });
  schema.repeatables.forEach(definition => { groups[definition.key] ||= [{}]; render(definition.key); });
  form.addEventListener("submit", sync);
})();
