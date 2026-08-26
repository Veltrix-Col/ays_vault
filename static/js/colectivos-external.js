(() => {
  "use strict";

  const form = document.querySelector("[data-external-response]");
  if (!form) return;

  const filter = form.querySelector("[data-row-filter]");
  const progress = form.querySelector("[data-progress-count]");
  const visible = form.querySelector("[data-visible-count]");
  const backdrop = form.querySelector("[data-drawer-backdrop]");
  const saveButton = form.querySelector(".external-sticky-save button[type='submit']");
  const tables = [...form.querySelectorAll("[data-functional-table]")];
  let activeDrawer = null;
  let activeTrigger = null;
  let drawerSnapshot = [];

  function normalize(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es");
  }

  function recordChanged(record) {
    const action = record.querySelector("[data-row-action]");
    return Boolean(action && action.value !== "SIN_CAMBIOS");
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
    changedRows += form.querySelectorAll("[data-include-action]:checked").length;
    if (visible) visible.textContent = String(visibleRows);
    if (progress) progress.textContent = String(changedRows);
    if (saveButton) saveButton.textContent = changedRows ? `Guardar mis cambios (${changedRows})` : "Guardar mis cambios";
  }

  function markModified(control) {
    const record = control.closest("[data-functional-entity]");
    if (!record || control.matches("[data-row-action]")) return;
    const hasValue = control.type === "checkbox" ? control.checked : Boolean(control.value);
    if (!hasValue) return;
    const action = record.querySelector("[data-row-action]");
    if (!action || action.value !== "SIN_CAMBIOS") return;
    const modify = [...action.options].find((option) => option.value === "MODIFICAR");
    if (modify) action.value = "MODIFICAR";
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
      const action = activeDrawer?.closest("[data-functional-entity]")?.querySelector("[data-row-action]");
      if (action) action.value = "RETIRAR";
      closeDrawer();
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
