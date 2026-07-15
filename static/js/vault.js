document.addEventListener("DOMContentLoaded", () => {
  let timer = null;
  const panel = document.querySelector("#reveal-panel");
  const form = document.querySelector("#reveal-form");
  if (!form || !panel) return;
  document.querySelectorAll(".reveal-btn").forEach((button) => button.addEventListener("click", () => {
    document.querySelector("#field-input").value = button.dataset.field;
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth" });
  }));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
    const response = await fetch(form.action, { method: "POST", body: data, headers: { "X-CSRFToken": csrf }, cache: "no-store" });
    const payload = await response.json();
    if (response.status === 428 && payload.reauth_url) { window.location.assign(payload.reauth_url); return; }
    if (!response.ok) { window.alert("No fue posible confirmar. Verifique el motivo y los permisos."); return; }
    const field = payload.field;
    const target = document.querySelector(`#value-${field}`);
    target.textContent = payload.value;
    target.classList.add("revealed");
    const copy = document.createElement("button");
    copy.type = "button"; copy.className = "btn secondary"; copy.textContent = "Copiar";
    copy.addEventListener("click", async () => {
      let result = "failed";
      try { await navigator.clipboard.writeText(target.textContent); result = "success"; } finally {
        const meta = document.querySelector("#copy-meta");
        const copyData = new FormData(); copyData.append("copy_token", payload.copy_token); copyData.append("result", result);
        const audited = await fetch(meta.dataset.url, { method: "POST", body: copyData, headers: { "X-CSRFToken": csrf }, cache: "no-store" });
        copy.disabled = true; copy.textContent = audited.ok ? "Copiado y auditado" : "Copia no confirmada";
      }
    });
    target.parentElement.appendChild(copy);
    clearTimeout(timer);
    timer = window.setTimeout(() => { target.textContent = field === "pan" ? "•••• •••• ••••" : "••/••"; target.classList.remove("revealed"); copy.remove(); panel.hidden = true; form.reset(); }, payload.expires_in * 1000);
  });
});
