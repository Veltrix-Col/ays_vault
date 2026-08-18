(function () {
  "use strict";
  function fold(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  }
  document.querySelectorAll("[data-responsible-picker]").forEach(function (picker) {
    var search = picker.querySelector("[data-responsible-search]");
    var select = picker.querySelector("[data-responsible-select]");
    var selected = picker.querySelector("[data-responsible-selected]");
    if (!search || !select) return;
    function announce() {
      var option = select.options[select.selectedIndex];
      if (selected) selected.textContent = option && option.value ? "Seleccionado: " + option.textContent : "Seleccione un responsable.";
    }
    search.addEventListener("input", function () {
      var query = fold(search.value);
      Array.prototype.forEach.call(select.options, function (option) {
        option.hidden = Boolean(query) && !fold(option.textContent).includes(query);
      });
      var visible = Array.prototype.find.call(select.options, function (option) { return !option.hidden && option.value; });
      if (visible && (!select.options[select.selectedIndex] || select.options[select.selectedIndex].hidden)) select.value = visible.value;
      announce();
    });
    select.addEventListener("change", announce);
    announce();
  });
}());
