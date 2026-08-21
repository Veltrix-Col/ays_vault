(() => {
  "use strict";
  const loadingForms = [
    ["form[action*='/persona/crear/']", "Creando afiliado en Zoho…"],
    ["form[action*='/riesgo/'][action*='/crear/']", "Creando vehículo en Zoho…"],
    ["form[action*='/subriesgo/'][action*='/crear/']", "Agregando a la póliza…"],
    ["form[action*='/task/publicar/']", "Publicando Task en Zoho…"],
    ["form[action*='/responsable/']", "Publicando Task en Zoho…"],
  ];
  loadingForms.forEach(([selector, message]) => {
    document.querySelectorAll(selector).forEach((form) => {
      form.dataset.remoteZoho = "true";
      form.dataset.loadingMessage = message;
    });
  });
  const closeDialog = (dialog) => {
    if (dialog && dialog.open) dialog.close();
  };
  document.querySelectorAll("[data-dialog-open]").forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const dialog = document.getElementById(trigger.dataset.dialogOpen);
      if (dialog && typeof dialog.showModal === "function") dialog.showModal();
    });
  });
  document.querySelectorAll("[data-dialog-close]").forEach((trigger) => {
    trigger.addEventListener("click", () => closeDialog(trigger.closest("dialog")));
  });
  document.querySelectorAll("dialog.individual-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeDialog(dialog);
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeDialog(dialog);
    });
  });
  // A 0 km vehicle may legitimately have no plate yet.  It is not a failed
  // Zoho search: keep the operational card visible as pending plate data.
  document.querySelectorAll(".vehicle-entity-card").forEach((card) => {
    const heading = card.querySelector(".entity-card__heading h3");
    const badge = card.querySelector(".entity-card__heading .status-badge");
    if (!heading || !badge || heading.textContent.trim() !== "Sin placa") return;
    if (badge.textContent.trim() === "No encontrado") badge.textContent = "Pendiente de placa";
    if (!card.querySelector(".risk-plate-hint")) {
      const hint = document.createElement("p");
      hint.className = "risk-plate-hint";
      hint.textContent = "Complete la placa para buscar o crear este vehículo en Zoho.";
      card.insertBefore(hint, card.querySelector(".entity-actions"));
    }
  });

  // The insured dialog belongs to the vehicle candidate, not to the global
  // affiliate correction.  Normalize its target even for legacy templates
  // that rendered an empty parent-loop index.
  const vehicleCards = Array.from(document.querySelectorAll(".vehicle-entity-card"));
  vehicleCards.forEach((card, index) => {
    const insuredTrigger = card.querySelector(".entity-card--nested [data-dialog-open^='insured-edit-']");
    if (insuredTrigger) insuredTrigger.dataset.dialogOpen = `insured-edit-${index}`;
  });

  const effectiveSummary = (dialog, title) => {
    if (!dialog) return null;
    const values = [];
    dialog.querySelectorAll("input[name], select[name], textarea[name]").forEach((field) => {
      if (field.type === "hidden" || field.name === "csrfmiddlewaretoken") return;
      const label = field.closest("label");
      const text = label ? label.firstChild && label.firstChild.textContent.trim() : field.name;
      const value = field.value.trim();
      if (text && value) values.push(`${text}: ${value}`);
    });
    if (!values.length) return null;
    const summary = document.createElement("div");
    summary.className = "entity-effective-summary";
    const heading = document.createElement("strong");
    heading.textContent = title;
    summary.appendChild(heading);
    values.forEach((value) => {
      const line = document.createElement("span");
      line.textContent = value;
      summary.appendChild(line);
    });
    return summary;
  };

  // Each operational card displays the exact effective candidate represented
  // by its own dialog. The original global summary is intentionally removed.
  document.querySelectorAll(".zoho-effective-summary").forEach((node) => node.remove());
  document.querySelectorAll(".entity-association").forEach((association) => {
    const trigger = association.querySelector("[data-dialog-open^='subrisk-edit-']");
    const dialog = trigger ? document.getElementById(trigger.dataset.dialogOpen) : null;
    const summary = effectiveSummary(dialog, "Datos para Zoho");
    if (summary) {
      const summaryTitle = summary.querySelector("strong");
      if (summaryTitle) summaryTitle.remove();
      association.insertBefore(summary, association.querySelector(".entity-actions") || association.lastElementChild);
    }

    const heading = association.querySelector(".eyebrow");
    if (heading) heading.textContent = "Datos de póliza";
    association.querySelectorAll(".eyebrow").forEach((label) => {
      if (label !== heading && label.textContent.trim() === "Datos para Zoho") label.remove();
    });
    const edit = association.querySelector("[data-dialog-open^='subrisk-edit-']");
    if (edit) edit.textContent = "Editar datos de póliza";
    const policy = association.querySelector("p");
    const policyLabel = policy ? policy.textContent.replace(/^\s*Póliza:\s*/i, "").trim() : "";
    association.querySelectorAll("form button").forEach((button) => {
      if (button.textContent.trim() === "Asociar a esta póliza") button.textContent = "Agregar a la póliza";
    });
    association.querySelectorAll(".status-message--success").forEach((message) => {
      message.textContent = policyLabel ? `Agregado a la póliza ${policyLabel}` : "Agregado a la póliza";
    });
    association.querySelectorAll("p").forEach((message) => {
      if (message === policy) return;
      if (message.textContent.includes("todavía no está asociado")) {
        message.textContent = "El vehículo todavía no está agregado a la póliza.";
      } else if (message.textContent.includes("No se puede asociar todavía")) {
        message.textContent = message.textContent.replace("No se puede asociar todavía", "No se puede agregar todavía");
      }
    });
    association.querySelectorAll("[data-dialog-open^='subrisk-edit-']").forEach((trigger) => trigger.remove());
  });
  document.querySelectorAll("dialog[id^='subrisk-edit-']").forEach((dialog) => dialog.remove());
})();
