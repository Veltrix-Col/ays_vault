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
      if (action && action.value !== "SIN_CAMBIOS") changedRows += 1;
    });
    changedRows += form.querySelectorAll("[data-include-action]:checked").length;
    if (visible) visible.textContent = String(visibleRows);
    if (progress) progress.textContent = String(changedRows);
  }

  if (filter) filter.addEventListener("input", refresh);
  form.addEventListener("change", refresh);
  form.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const opened = event.target.closest("details[open]");
    if (!opened) return;
    opened.removeAttribute("open");
    opened.querySelector("summary")?.focus();
  });
  refresh();
})();
