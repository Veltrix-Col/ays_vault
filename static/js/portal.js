(() => {
  "use strict";
  const input = document.querySelector("[data-tool-search]");
  if (!input) return;
  const cards = [...document.querySelectorAll("[data-tool-card]")];
  const packages = document.querySelector("[data-area-packages]");
  const results = document.querySelector("[data-tool-results]");
  const status = document.querySelector("[data-tool-search-status]");
  const empty = document.querySelector("[data-tool-search-empty]");
  const visibleLinks = () => cards.filter(card => !card.hidden).map(card => card.querySelector("a")).filter(Boolean);
  const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es").trim();
  const filter = () => {
    const terms = normalize(input.value).split(/\s+/).filter(Boolean);
    const searching = terms.length > 0;
    let visible = 0;
    cards.forEach(card => {
      const haystack = normalize(card.dataset.toolSearchText);
      card.hidden = !searching || !terms.every(term => haystack.includes(term));
      if (!card.hidden) visible += 1;
    });
    packages.hidden = searching;
    results.hidden = !searching || visible === 0;
    status.textContent = searching ? `${visible} resultado${visible === 1 ? "" : "s"}` : `${cards.length} herramientas disponibles en 2 áreas`;
    empty.hidden = !searching || visible !== 0;
  };
  input.addEventListener("input", filter);
  input.addEventListener("keydown", event => {
    const links = visibleLinks();
    if (event.key === "Escape") {
      input.value = "";
      filter();
      return;
    }
    if (event.key === "ArrowDown" && links.length) {
      event.preventDefault();
      links[0].focus();
      return;
    }
    if (event.key === "Enter" && links.length === 1 && normalize(input.value)) {
      event.preventDefault();
      links[0].click();
    }
  });
  cards.forEach(card => {
    const link = card.querySelector("a");
    if (!link) return;
    link.addEventListener("keydown", event => {
      if (event.key === "Escape") {
        event.preventDefault();
        input.value = "";
        filter();
        input.focus();
        return;
      }
      if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
      const links = visibleLinks();
      const position = links.indexOf(link);
      if (position < 0) return;
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      (links[(position + delta + links.length) % links.length] || input).focus();
    });
  });
})();
