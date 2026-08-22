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
