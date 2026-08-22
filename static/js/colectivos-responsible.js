(function () {
  "use strict";
  function fold(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  }
  function setup(container, config) {
    var search = container.querySelector(config.search);
    var select = container.querySelector(config.select);
    var selected = container.querySelector(config.selected);
    if (!search || !select) return;
    function announce() {
      var option = select.options[select.selectedIndex];
      if (selected) selected.textContent = option && option.value
        ? "Seleccionado: " + option.textContent
        : (config.emptyMessage || "Seleccione una opción.");
    }
    search.addEventListener("input", function () {
      var query = fold(search.value);
      Array.prototype.forEach.call(select.options, function (option) {
        var text = option.dataset.searchText || option.textContent;
        option.hidden = Boolean(query) && !fold(text).includes(query);
      });
      var current = select.options[select.selectedIndex];
      var visible = Array.prototype.find.call(select.options, function (option) { return !option.hidden && option.value; });
      if (visible && (!current || current.hidden)) select.value = visible.value;
      announce();
    });
    select.addEventListener("change", announce);
    announce();
  }
  document.querySelectorAll("[data-responsible-picker]").forEach(function (container) {
    setup(container, {
      search: "[data-responsible-search]",
      select: "[data-responsible-select]",
      selected: "[data-responsible-selected]",
      emptyMessage: "Seleccione un responsable.",
    });
  });
  document.querySelectorAll("[data-searchable-select]").forEach(function (container) {
    setup(container, {
      search: "[data-searchable-input]",
      select: "[data-searchable-select-control]",
      selected: "[data-searchable-selected]",
      emptyMessage: "Seleccione un afiliado o Nuevo afiliado.",
    });
  });
}());
