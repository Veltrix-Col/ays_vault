document.addEventListener("DOMContentLoaded", () => {
  const sidebar = document.querySelector("#app-sidebar");
  const openButton = document.querySelector("[data-sidebar-open]");
  const closeButtons = document.querySelectorAll("[data-sidebar-close]");
  const backdrop = document.querySelector(".sidebar-backdrop");

  const setSidebar = (open) => {
    if (!sidebar || !openButton || !backdrop) return;
    sidebar.classList.toggle("is-open", open);
    document.body.classList.toggle("sidebar-open", open);
    openButton.setAttribute("aria-expanded", String(open));
    backdrop.hidden = !open;
    if (open) sidebar.querySelector("a, button")?.focus();
    else openButton.focus();
  };

  openButton?.addEventListener("click", () => setSidebar(true));
  closeButtons.forEach((button) => button.addEventListener("click", () => setSidebar(false)));
  sidebar?.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => {
    if (window.matchMedia("(max-width: 991px)").matches) setSidebar(false);
  }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sidebar?.classList.contains("is-open")) setSidebar(false);
  });
  window.matchMedia("(min-width: 992px)").addEventListener("change", (event) => {
    if (event.matches && sidebar?.classList.contains("is-open")) setSidebar(false);
  });

  const timelineForm = document.querySelector("[data-timeline-filters]");
  if (timelineForm) {
    const applyQuickFilter = (field, value) => {
      const input = timelineForm.querySelector(`[name="${field}"]`);
      input.value = input.value === value ? "" : value;
      timelineForm.requestSubmit();
    };
    timelineForm.querySelectorAll("[data-quick-period]").forEach((button) => button.addEventListener("click", () => applyQuickFilter("period", button.dataset.quickPeriod)));
    timelineForm.querySelectorAll("[data-quick-event]").forEach((button) => button.addEventListener("click", () => applyQuickFilter("quick_event", button.dataset.quickEvent)));
    const advanced = timelineForm.querySelector("[data-advanced-filters]");
    advanced?.addEventListener("toggle", () => {
      timelineForm.querySelector("[name=advanced]").value = advanced.open ? "1" : "";
    });
    const syncExportForm = (exportForm) => {
      exportForm.querySelectorAll("input[data-synced-filter]").forEach((input) => input.remove());
      new FormData(timelineForm).forEach((value, key) => {
        const input = document.createElement("input");
        input.type = "hidden"; input.name = key; input.value = value; input.dataset.syncedFilter = "true";
        exportForm.appendChild(input);
      });
    };
    document.querySelectorAll("#timeline-export-xlsx, #timeline-export-pdf").forEach((exportForm) => exportForm.addEventListener("submit", () => syncExportForm(exportForm)));
  }

  const searchForm = document.querySelector("[data-vault-search]");
  if (searchForm) {
    const input = searchForm.querySelector("[name=q]");
    const clear = searchForm.querySelector("[data-search-clear]");
    const spinner = searchForm.querySelector("[data-search-spinner]");
    const results = document.querySelector("[data-vault-results]");
    const status = document.querySelector("#vault-search-status");
    let debounce = null;
    let controller = null;

    const loadResults = async (url) => {
      controller?.abort();
      controller = new AbortController();
      spinner.hidden = false; results.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" }, signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("SEARCH_FAILED");
        results.innerHTML = await response.text();
        history.replaceState({}, "", url);
        status.textContent = "Resultados de la Bóveda actualizados.";
      } catch (error) {
        if (error.name !== "AbortError") status.textContent = "No fue posible actualizar la búsqueda. Intente nuevamente.";
      } finally {
        if (!controller.signal.aborted) { spinner.hidden = true; results.setAttribute("aria-busy", "false"); }
      }
    };
    const search = () => {
      clear.hidden = !input.value;
      const url = new URL(searchForm.action, window.location.origin);
      if (input.value.trim()) url.searchParams.set("q", input.value.trim());
      loadResults(`${url.pathname}${url.search}`);
    };
    input.addEventListener("input", () => { window.clearTimeout(debounce); debounce = window.setTimeout(search, 300); });
    searchForm.addEventListener("submit", (event) => { event.preventDefault(); window.clearTimeout(debounce); search(); });
    clear.addEventListener("click", () => { input.value = ""; input.focus(); search(); });
    results.addEventListener("click", (event) => {
      const link = event.target.closest("[data-search-page]");
      if (!link) return;
      event.preventDefault(); loadResults(link.href);
    });
  }

  const protectedMeta = document.querySelector("#protected-meta");
  if (protectedMeta) {
    const csrf = protectedMeta.querySelector("[name=csrfmiddlewaretoken]").value;
    const modalBackdrop = document.querySelector("#protected-modal");
    const modal = modalBackdrop.querySelector("[role=dialog]");
    const modalContent = document.querySelector("#protected-modal-content");
    const modalTitle = document.querySelector("#protected-modal-title");
    const actionStatus = document.querySelector("#protected-status");
    const timers = new Map();
    let pendingAction = null;
    let opener = null;

    const showStatus = (message, kind = "success") => {
      actionStatus.textContent = message; actionStatus.className = `toast inline-action-status ${kind}`; actionStatus.hidden = false;
      window.setTimeout(() => { actionStatus.hidden = true; actionStatus.textContent = ""; }, 3500);
    };
    const hideField = (field) => {
      const target = document.querySelector(`#value-${field}`);
      if (!target) return;
      window.clearTimeout(timers.get(field)); timers.delete(field);
      target.textContent = target.dataset.masked; target.classList.remove("revealed");
      target.closest("[data-protected-row]")?.querySelector("[data-hide-protected]")?.remove();
    };
    const closeModal = () => {
      modalBackdrop.hidden = true; document.body.classList.remove("modal-open"); modalContent.replaceChildren();
      opener?.focus(); opener = null;
    };
    const openModal = (html, button, stage = "identity") => {
      opener = button || opener; modalContent.innerHTML = html; modalBackdrop.hidden = false; document.body.classList.add("modal-open");
      modalTitle.textContent = stage === "context" ? "Contexto de la operación" : "Confirme su identidad";
      modalContent.querySelector("input:not([type=hidden]), button")?.focus();
    };
    const confirmCopy = async (token, result) => {
      const data = new FormData(); data.append("copy_token", token); data.append("result", result);
      return fetch(protectedMeta.dataset.copyUrl, { method: "POST", body: data, headers: { "X-CSRFToken": csrf }, cache: "no-store" });
    };
    const executeProtected = async (field, action, button) => {
      button?.setAttribute("aria-busy", "true"); if (button) button.disabled = true;
      const data = new FormData(); data.append("field", field); data.append("action", action);
      try {
        const response = await fetch(protectedMeta.dataset.actionUrl, { method: "POST", body: data, headers: { "X-CSRFToken": csrf }, cache: "no-store" });
        if (response.status === 428) {
          const payload = await response.json(); pendingAction = { field, action, button }; openModal(payload.form_html, button, payload.stage); return;
        }
        if (!response.ok) { showStatus("No fue posible completar la operación protegida.", "error"); return; }
        let protectedValue = await response.text();
        const token = response.headers.get("X-Vault-Copy-Token");
        if (action === "copy") {
          let result = "failed";
          try { await navigator.clipboard.writeText(protectedValue); result = "success"; }
          finally {
            protectedValue = "";
            const audited = await confirmCopy(token, result);
            showStatus(audited.ok && result === "success" ? "Dato copiado correctamente." : "No fue posible confirmar la copia.", audited.ok ? "success" : "error");
          }
          return;
        }
        const target = document.querySelector(`#value-${field}`);
        target.textContent = protectedValue; protectedValue = ""; target.classList.add("revealed");
        const actions = target.closest("[data-protected-row]").querySelector(".protected-actions");
        actions.querySelector("[data-hide-protected]")?.remove();
        const hide = document.createElement("button"); hide.type = "button"; hide.className = "btn ghost"; hide.dataset.hideProtected = field; hide.textContent = "Ocultar";
        hide.addEventListener("click", () => hideField(field)); actions.appendChild(hide);
        timers.set(field, window.setTimeout(() => hideField(field), Number(response.headers.get("X-Vault-Expires-In") || 20) * 1000));
      } catch (error) { showStatus("No fue posible completar la operación. Verifique la conexión.", "error"); }
      finally { button?.removeAttribute("aria-busy"); if (button) button.disabled = false; }
    };

    document.querySelectorAll(".protected-action").forEach((button) => button.addEventListener("click", () => executeProtected(button.dataset.field, button.dataset.action, button)));
    modalBackdrop.addEventListener("click", (event) => { if (event.target === modalBackdrop || event.target.closest("[data-protected-close]")) closeModal(); });
    modalContent.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target; const submit = form.querySelector("[type=submit]"); submit.disabled = true; submit.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(form.action, { method: "POST", body: new FormData(form), headers: { "X-CSRFToken": csrf }, cache: "no-store" });
        const contentType = response.headers.get("content-type") || "";
        if (form.matches("[data-protected-identity]")) {
          modalContent.innerHTML = await response.text();
          if (response.ok) { modalTitle.textContent = "Contexto de la operación"; modalContent.querySelector("input:not([type=hidden])")?.focus(); }
          return;
        }
        if (!response.ok || !contentType.includes("application/json")) { modalContent.innerHTML = await response.text(); modalContent.querySelector("input:not([type=hidden])")?.focus(); return; }
        const payload = await response.json();
        if (payload.ok && pendingAction) { const action = pendingAction; closeModal(); pendingAction = null; await executeProtected(action.field, action.action, action.button); }
      } catch (error) {
        modalContent.innerHTML = '<p class="form-error" role="alert">No fue posible validar la operación. Verifique la conexión e intente nuevamente.</p>';
      } finally { submit.disabled = false; submit.removeAttribute("aria-busy"); }
    });
    document.addEventListener("keydown", (event) => {
      if (modalBackdrop.hidden) return;
      if (event.key === "Escape") { closeModal(); return; }
      if (event.key !== "Tab") return;
      const focusable = [...modal.querySelectorAll("button, input, select, textarea, a[href]")].filter((item) => !item.disabled && !item.hidden);
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    document.addEventListener("visibilitychange", () => { if (document.hidden) timers.forEach((_, field) => hideField(field)); });
  }

  const reportDialog = document.querySelector("#report-dialog");
  if (reportDialog) {
    const reportForm = reportDialog.querySelector("[data-report-form]");
    const reportTitle = reportDialog.querySelector("#report-dialog-title");
    const reportError = reportDialog.querySelector("[data-report-error]");
    let reportUrls = null;
    let reportOpener = null;
    let reportBusy = false;

    const closeReportDialog = () => {
      if (reportBusy) return;
      reportDialog.close(); reportForm.reset(); reportError.hidden = true; reportError.textContent = ""; reportOpener?.focus();
    };
    document.querySelectorAll("[data-report-open]").forEach((button) => button.addEventListener("click", () => {
      reportOpener = button;
      reportUrls = { XLSX: button.dataset.xlsxUrl, PDF: button.dataset.pdfUrl };
      reportTitle.textContent = `Configurar ${button.dataset.reportName}`;
      reportDialog.showModal(); reportDialog.querySelector("input, select, button")?.focus();
    }));
    reportDialog.querySelectorAll("[data-report-close]").forEach((button) => button.addEventListener("click", closeReportDialog));
    reportDialog.addEventListener("cancel", (event) => { if (reportBusy) event.preventDefault(); });
    reportForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (reportBusy) return;
      const submitter = event.submitter;
      const format = submitter?.dataset.reportFormat;
      if (!format || !reportUrls?.[format]) return;
      reportBusy = true; reportError.hidden = true;
      reportForm.querySelectorAll("button").forEach((button) => { button.disabled = true; });
      submitter.setAttribute("aria-busy", "true"); submitter.dataset.originalText = submitter.textContent;
      submitter.textContent = format === "PDF" ? "Generando PDF…" : "Preparando Excel…";
      try {
        const response = await fetch(reportUrls[format], {
          method: "POST",
          body: new FormData(reportForm),
          cache: "no-store",
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        if (!response.ok) {
          reportError.textContent = (await response.text()).replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim() || "No fue posible generar el informe.";
          reportError.hidden = false; return;
        }
        const blob = await response.blob();
        const disposition = response.headers.get("Content-Disposition") || "";
        const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        const filename = match ? decodeURIComponent(match[1]) : `A&S Vault - Informe.${format === "PDF" ? "pdf" : "xlsx"}`;
        const downloadUrl = URL.createObjectURL(blob); const link = document.createElement("a");
        link.href = downloadUrl; link.download = filename; document.body.appendChild(link); link.click(); link.remove();
        window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000); reportBusy = false; closeReportDialog();
      } catch (error) {
        reportError.textContent = "No fue posible generar el informe. Verifique la conexión e intente nuevamente."; reportError.hidden = false;
      } finally {
        reportBusy = false; reportForm.querySelectorAll("button").forEach((button) => { button.disabled = false; });
        submitter.removeAttribute("aria-busy"); submitter.textContent = submitter.dataset.originalText;
      }
    });
  }
});
