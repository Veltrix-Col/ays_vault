(() => {
  "use strict";
  const form = document.querySelector("[data-external-response]");
  if (!form) return;

  const rows = [...form.querySelectorAll("[data-response-row], [data-functional-entity]")];
  const filter = form.querySelector("[data-row-filter]");
  const progress = form.querySelector("[data-progress-count]");
  const visible = form.querySelector("[data-visible-count]");

  function normalize(value) {
    return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es");
  }

  function refresh() {
    const query = normalize(filter ? filter.value.trim() : "");
    let visibleRows = 0;
    let changedRows = 0;
    rows.forEach((row) => {
      const action = row.querySelector("[data-row-action]");
      const matches = !query || normalize(row.textContent || "").includes(query);
      row.hidden = !matches;
      if (matches) visibleRows += 1;
      const changed = Boolean(action && action.value !== "SIN_CAMBIOS");
      if (changed) changedRows += 1;
      const state = row.querySelector("[data-change-state]");
      if (state) state.hidden = !changed;
    });
    changedRows += form.querySelectorAll("[data-include-action]:checked").length;
    if (visible) visible.textContent = String(visibleRows);
    if (progress) progress.textContent = String(changedRows);
  }

  function markModified(control) {
    const row = control.closest("[data-functional-entity]");
    if (!row || control.matches("[data-row-action]") || !control.value) return;
    const action = row.querySelector("[data-row-action]");
    if (!action || action.value !== "SIN_CAMBIOS") return;
    const modify = [...action.options].find((option) => option.value === "MODIFICAR");
    if (modify) action.value = "MODIFICAR";
  }

  if (filter) filter.addEventListener("input", refresh);
  form.querySelectorAll("[data-edit-disclosure]").forEach((disclosure) => {
    const toggle = disclosure.querySelector("[data-edit-toggle]");
    disclosure.addEventListener("toggle", () => {
      if (!toggle) return;
      toggle.setAttribute("aria-expanded", disclosure.open ? "true" : "false");
      if (disclosure.open) {
        const firstControl = disclosure.querySelector("[data-edit-panel] input, [data-edit-panel] select, [data-edit-panel] textarea");
        if (firstControl) firstControl.focus();
      }
    });
  });
  form.addEventListener("click", (event) => {
    const close = event.target.closest("[data-edit-close], [data-edit-done]");
    if (!close) return;
    const disclosure = close.closest("[data-edit-disclosure]");
    if (!disclosure) return;
    disclosure.open = false;
    const toggle = disclosure.querySelector("[data-edit-toggle]");
    if (toggle) toggle.focus();
  });
  form.addEventListener("input", (event) => {
    markModified(event.target);
    refresh();
  });
  form.addEventListener("change", (event) => {
    markModified(event.target);
    refresh();
  });
  form.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const disclosure = event.target.closest("[data-edit-disclosure]");
    if (disclosure && disclosure.open) {
      disclosure.open = false;
      const toggle = disclosure.querySelector("[data-edit-toggle]");
      if (toggle) toggle.focus();
      return;
    }
    const opened = event.target.closest("details[open]");
    if (opened) {
      opened.removeAttribute("open");
      opened.querySelector("summary")?.focus();
    }
  });
  refresh();
})();
