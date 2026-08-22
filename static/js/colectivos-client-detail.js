(() => {
  "use strict";
  const input = document.querySelector("[data-policy-filter]");
  if (!input) return;
  const cards = [...document.querySelectorAll("[data-policy-card]")];
  const branches = [...document.querySelectorAll("[data-policy-branch]")];
  const status = document.querySelector("[data-policy-filter-status]");
  const noResults = document.querySelector("[data-policy-no-results]");
  const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es").trim();
  const filter = () => {
    const terms = normalize(input.value).split(/\s+/).filter(Boolean);
    let visible = 0;
    cards.forEach(card => {
      card.hidden = !terms.every(term => normalize(card.dataset.policySearchText).includes(term));
      if (!card.hidden) visible += 1;
    });
    branches.forEach(branch => { branch.hidden = !branch.querySelector("[data-policy-card]:not([hidden])") && terms.length > 0; });
    status.textContent = terms.length ? `${visible} póliza${visible === 1 ? "" : "s"}` : `${cards.length} póliza${cards.length === 1 ? "" : "s"}`;
    noResults.hidden = !terms.length || visible > 0;
  };
  input.addEventListener("input", filter);
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") { input.value = ""; filter(); }
    if (event.key === "Enter") {
      const visible = cards.filter(card => !card.hidden);
      if (visible.length === 1) { event.preventDefault(); visible[0].click(); }
    }
  });
  filter();
})();
