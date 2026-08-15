(() => {
  "use strict";
  const input = document.querySelector("[data-invitation-filter]");
  if (!input) return;
  const rows = [...document.querySelectorAll("[data-invitation-row]")];
  const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es");
  const filter = () => {
    const terms = normalize(input.value).trim().split(/\s+/).filter(Boolean);
    rows.forEach(row => { row.hidden = !terms.every(term => normalize(row.dataset.invitationSearchText).includes(term)); });
  };
  input.addEventListener("input", filter);
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") { input.value = ""; filter(); }
  });
})();
