"use strict";

document.addEventListener("click", function (event) {
  const button = event.target.closest("[data-copy-target]");
  if (!button) return;
  const field = document.getElementById(button.dataset.copyTarget);
  if (!field) return;
  navigator.clipboard.writeText(field.value).then(function () {
    button.textContent = "Enlace copiado";
  });
});

document.querySelectorAll("[data-individual-otp-toggle]").forEach(function (toggle) {
  var form = toggle.closest("form");
  var fields = form && form.querySelector("[data-individual-otp-fields]");
  var recipient = fields && fields.querySelector("[name='recipient']");
  if (!fields || !recipient) return;
  function sync() {
    var enabled = toggle.checked;
    fields.hidden = !enabled;
    toggle.setAttribute("aria-expanded", enabled ? "true" : "false");
    recipient.required = enabled;
    if (!enabled) recipient.setCustomValidity("");
  }
  toggle.addEventListener("change", sync);
  sync();
});
