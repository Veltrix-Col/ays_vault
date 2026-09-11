(function () {
  function initRenewalScheduleDirtyState() {
    document.querySelectorAll(".renewal-schedule-form").forEach(function (form) {
      var input = form.querySelector("input[name='scheduled_for']");
      if (!input || input.dataset.dirtyStateInitialized === "true") return;

      input.dataset.dirtyStateInitialized = "true";
      var originalValue = input.dataset.originalValue || "";
      var updateDirtyState = function () {
        var dirty = input.value !== originalValue;
        form.classList.toggle("is-dirty", dirty);
      };

      input.addEventListener("input", updateDirtyState);
      input.addEventListener("change", updateDirtyState);
      updateDirtyState();
    });
  }

  initRenewalScheduleDirtyState();
}());
