(function () {
  "use strict";

  const overlay = document.querySelector("[data-novelties-loading]");
  const link = document.querySelector("[data-novelties-entry]");
  if (!overlay || !link) return;

  link.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target.toLowerCase() !== "_self") return;
    overlay.hidden = false;
    document.documentElement.classList.add("portal-is-loading");
  });

  window.addEventListener("pageshow", function () {
    overlay.hidden = true;
    document.documentElement.classList.remove("portal-is-loading");
  });
}());
