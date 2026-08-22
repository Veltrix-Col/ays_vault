(() => {
  "use strict";
  const input = document.querySelector("[data-invitation-filter]");
  if (!input) return;
  const rows = [...document.querySelectorAll("[data-invitation-row]")];
  const policyOptions = [...document.querySelectorAll("[data-invitation-policy-filter]")];
  const clearPolicy = document.querySelector("[data-invitation-policy-clear]");
  const empty = document.querySelector("[data-invitation-filter-empty]");
  const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es");
  const filter = () => {
    const terms = normalize(input.value).trim().split(/\s+/).filter(Boolean);
    const selected = policyOptions.find(option => option.checked)?.value || "";
    rows.forEach(row => {
      const matchesText = terms.every(term => normalize(row.dataset.invitationSearchText).includes(term));
      const matchesPolicy = !selected || row.dataset.policyKey === selected;
      row.hidden = !(matchesText && matchesPolicy);
    });
    if (empty) empty.hidden = !rows.length || rows.some(row => !row.hidden);
  };
  input.addEventListener("input", filter);
  policyOptions.forEach(option => option.addEventListener("change", filter));
  clearPolicy?.addEventListener("click", () => {
    policyOptions.forEach(option => { option.checked = false; });
    filter();
  });
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") { input.value = ""; filter(); }
  });
})();
