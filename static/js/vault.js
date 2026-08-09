document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form[data-single-submit]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitted === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.submitted = "true";
      window.setTimeout(() => {
        form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach((control) => {
          control.disabled = true;
          control.setAttribute("aria-busy", "true");
        });
      }, 0);
    });
  });

  document.querySelectorAll("[data-protected-edit-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.protectedEditToggle);
      if (!target) return;
      const opening = target.hidden;
      target.hidden = !opening;
      button.setAttribute("aria-expanded", String(opening));
      button.textContent = opening ? "Cancelar cambio" : "Modificar";
      const input = target.querySelector("input");
      if (opening) input?.focus();
      else if (input) input.value = "";
    });
  });

  const visibleCopyButtons = document.querySelectorAll("[data-copy-visible]");
  if (visibleCopyButtons.length) {
    const visibleCopyStatus = document.querySelector("[data-visible-copy-status]");
    const writeVisibleValueToClipboard = async (value) => {
      if (navigator.clipboard?.writeText && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
        return;
      }
      const fallback = document.createElement("textarea");
      fallback.value = value;
      fallback.readOnly = true;
      fallback.style.position = "fixed";
      fallback.style.opacity = "0";
      document.body.appendChild(fallback);
      fallback.select();
      const copied = document.execCommand("copy");
      fallback.remove();
      if (!copied) throw new Error("Clipboard unavailable");
    };
    visibleCopyButtons.forEach((button) => button.addEventListener("click", async () => {
      const valueElement = document.getElementById(button.dataset.copyVisible);
      const value = valueElement?.textContent.trim();
      if (!value) return;
      const label = button.dataset.copyLabel || "Dato";
      const originalText = button.textContent;
      button.disabled = true;
      try {
        await writeVisibleValueToClipboard(value);
        button.textContent = "Copiado";
        if (visibleCopyStatus) visibleCopyStatus.textContent = `${label} copiado`;
      } catch (error) {
        button.textContent = "No fue posible copiar";
        if (visibleCopyStatus) visibleCopyStatus.textContent = `No fue posible copiar ${label.toLowerCase()}`;
      } finally {
        window.setTimeout(() => {
          button.textContent = originalText;
          button.disabled = false;
        }, 1800);
      }
    }));
  }

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
    const protectedHeaders = {
      "X-CSRFToken": csrf,
      "X-Requested-With": "XMLHttpRequest",
      "Accept": "application/json, text/html;q=0.9, text/plain;q=0.8",
    };
    const statusMessages = {
      401: "La sesión segura terminó. Ingrese nuevamente para continuar.",
      403: "No tiene autorización para realizar esta operación.",
      404: "El dato o la tarjeta ya no están disponibles.",
      409: "La autorización expiró o ya fue utilizada. Inicie nuevamente la operación.",
      422: "Revise la información suministrada e intente nuevamente.",
    };

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
    const renderModalError = (message) => {
      const error = document.createElement("p");
      error.className = "form-error";
      error.setAttribute("role", "alert");
      error.textContent = message;
      modalContent.replaceChildren(error);
      error.focus?.();
    };
    const safeErrorMessage = async (response) => {
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        try {
          const payload = await response.json();
          if (typeof payload.message === "string" && payload.message.trim()) return payload.message;
        } catch (_) { /* El cuerpo inválido nunca se presenta al usuario. */ }
      }
      return statusMessages[response.status] || "No fue posible completar la operación protegida.";
    };
    const authenticationRequired = (response) => (
      response.status === 401
      || response.headers.get("X-Vault-Auth-Required") === "1"
      || (response.redirected && new URL(response.url).pathname.startsWith("/login/"))
    );
    const handleAuthenticationRequired = async (response) => {
      if (!authenticationRequired(response)) return false;
      const message = response.status === 401
        ? await safeErrorMessage(response)
        : statusMessages[401];
      closeModal();
      showStatus(message, "error");
      window.setTimeout(() => { window.location.assign("/login/"); }, 1200);
      return true;
    };
    const confirmCopy = async (token, result) => {
      const data = new FormData(); data.append("copy_token", token); data.append("result", result);
      return fetch(protectedMeta.dataset.copyUrl, { method: "POST", body: data, headers: protectedHeaders, cache: "no-store" });
    };
    const executeProtected = async (field, action, button) => {
      button?.setAttribute("aria-busy", "true"); if (button) button.disabled = true;
      const data = new FormData(); data.append("field", field); data.append("action", action);
      try {
        const response = await fetch(protectedMeta.dataset.actionUrl, { method: "POST", body: data, headers: protectedHeaders, cache: "no-store" });
        if (await handleAuthenticationRequired(response)) return;
        if (response.status === 428) {
          const contentType = response.headers.get("content-type") || "";
          if (!contentType.includes("application/json")) {
            showStatus("No fue posible iniciar la autorización protegida.", "error");
            return;
          }
          const payload = await response.json();
          if (!payload.authorization_required || typeof payload.form_html !== "string" || !["identity", "context"].includes(payload.stage)) {
            showStatus("La respuesta de autorización no es válida.", "error");
            return;
          }
          pendingAction = { field, action, button };
          openModal(payload.form_html, button, payload.stage);
          return;
        }
        if (!response.ok) {
          showStatus(await safeErrorMessage(response), "error");
          return;
        }
        const contentType = response.headers.get("content-type") || "";
        if (
          !contentType.includes("text/plain")
          || response.headers.get("X-Vault-Field") !== field
          || response.headers.get("X-Vault-Action") !== action
        ) {
          showStatus("La respuesta protegida no tiene el formato esperado.", "error");
          return;
        }
        let protectedValue = await response.text();
        const token = response.headers.get("X-Vault-Copy-Token");
        if (action === "copy") {
          let result = "failed";
          try { await navigator.clipboard.writeText(protectedValue); result = "success"; }
          finally {
            protectedValue = "";
            const audited = await confirmCopy(token, result);
            if (await handleAuthenticationRequired(audited)) return;
            showStatus(
              audited.ok && result === "success" ? "Dato copiado correctamente." : await safeErrorMessage(audited),
              audited.ok ? "success" : "error",
            );
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
        const response = await fetch(form.action, { method: "POST", body: new FormData(form), headers: protectedHeaders, cache: "no-store" });
        if (await handleAuthenticationRequired(response)) return;
        const contentType = response.headers.get("content-type") || "";
        if (form.matches("[data-protected-identity]")) {
          if (contentType.includes("text/html") && [200, 422].includes(response.status)) {
            modalContent.innerHTML = await response.text();
            if (response.ok) {
              modalTitle.textContent = "Contexto de la operación";
              modalContent.querySelector("input:not([type=hidden])")?.focus();
            }
          } else {
            renderModalError(await safeErrorMessage(response));
          }
          return;
        }
        if (response.status === 422 && contentType.includes("text/html")) {
          modalContent.innerHTML = await response.text();
          modalContent.querySelector("input:not([type=hidden])")?.focus();
          return;
        }
        if (!response.ok || !contentType.includes("application/json")) {
          renderModalError(await safeErrorMessage(response));
          return;
        }
        const payload = await response.json();
        if (payload.ok && pendingAction) { const action = pendingAction; closeModal(); pendingAction = null; await executeProtected(action.field, action.action, action.button); }
      } catch (error) {
        renderModalError("No fue posible validar la operación. Verifique la conexión e intente nuevamente.");
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

  const sensitiveWindow = document.querySelector("[data-sensitive-window]");
  if (sensitiveWindow) {
    const countdown = sensitiveWindow.querySelector("[data-sensitive-countdown]");
    const expiresAt = Date.parse(sensitiveWindow.dataset.expiresAt || "");
    let lastMinute = null;
    const updateWindowCountdown = () => {
      const remaining = Number.isFinite(expiresAt) ? Math.max(0, expiresAt - Date.now()) : 0;
      if (!remaining) {
        sensitiveWindow.classList.add("expired");
        sensitiveWindow.classList.remove("warning");
        sensitiveWindow.firstChild.textContent = "Ventana segura vencida ";
        if (countdown) countdown.textContent = "";
        return false;
      }
      const seconds = Math.floor(remaining / 1000);
      const minutes = Math.floor(seconds / 60);
      const rest = String(seconds % 60).padStart(2, "0");
      sensitiveWindow.classList.toggle("warning", seconds <= 180);
      sensitiveWindow.firstChild.textContent = seconds <= 180
        ? "Ventana segura próxima a vencer "
        : "Ventana segura activa ";
      if (countdown) countdown.textContent = `· ${String(minutes).padStart(2, "0")}:${rest}`;
      if (minutes !== lastMinute) {
        sensitiveWindow.setAttribute("aria-label", `${sensitiveWindow.firstChild.textContent.trim()}. ${minutes} minutos restantes.`);
        lastMinute = minutes;
      }
      return true;
    };
    updateWindowCountdown();
    const timer = window.setInterval(() => {
      if (!updateWindowCountdown()) window.clearInterval(timer);
    }, 1000);
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
        const filename = match ? decodeURIComponent(match[1]) : `CardManager - Informe.${format === "PDF" ? "pdf" : "xlsx"}`;
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
