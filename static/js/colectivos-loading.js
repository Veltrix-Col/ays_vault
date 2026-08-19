"use strict";

document.addEventListener("submit", function (event) {
  const form = event.target.closest("form[data-remote-zoho], form[data-request-builder], form[data-remote-action], form[action*='/responsable/']");
  if (!form || !form.checkValidity()) return;
  form.querySelectorAll("button[type='submit']").forEach(function (button) {
    button.disabled = true;
  });
  const status = document.createElement("p");
  status.className = "remote-loading-status";
  status.setAttribute("role", "status");
  status.textContent = form.dataset.loadingMessage || (form.action.includes("/responsable/")
    ? "Publicando Task…" : form.matches("[data-request-builder]")
    ? "Preparando la información de la póliza…"
    : "Consultando información en Zoho…");
  form.appendChild(status);
});
