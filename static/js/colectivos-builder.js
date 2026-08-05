(() => {
  "use strict";
  const form = document.querySelector("[data-request-builder]");
  if (!form) return;
  const policies = [...form.querySelectorAll('input[type="checkbox"][name^="policy_"]')];
  const counter = form.querySelector("[data-selected-count]");
  const adjustmentCounter = form.querySelector("[data-adjustment-count]");

  function refresh() {
    let selected = 0;
    let selectedAdjustments = 0;
    policies.forEach((policy) => {
      const index = policy.name.slice("policy_".length);
      const adjustmentInputs = form.querySelectorAll(`input[name="adjustments_${index}"]`);
      if (policy.checked) selected += 1;
      adjustmentInputs.forEach((input) => {
        input.disabled = !policy.checked;
        if (policy.checked && input.checked) selectedAdjustments += 1;
      });
      const card = policy.closest(".policy-selector-card");
      if (card) card.classList.toggle("policy-selector-card--selected", policy.checked);
    });
    if (counter) counter.textContent = String(selected);
    if (adjustmentCounter) adjustmentCounter.textContent = String(selectedAdjustments);
  }

  policies.forEach((policy) => policy.addEventListener("change", refresh));
  form.querySelectorAll('input[name^="adjustments_"]').forEach((input) => input.addEventListener("change", refresh));
  refresh();
})();
