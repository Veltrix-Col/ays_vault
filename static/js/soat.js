document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-soat-form]");
  if (!form) return;
  const input = form.querySelector('input[type="file"]');
  const fileName = form.querySelector("[data-file-name]");
  const submit = form.querySelector("[data-soat-submit]");
  const progress = form.querySelector("[data-soat-progress]");
  const result = document.querySelector("[data-soat-result]");
  const grid = document.querySelector("[data-result-grid]");
  const resultFile = document.querySelector("[data-result-file]");
  const download = document.querySelector("[data-result-download]");
  const reset = document.querySelector("[data-result-reset]");
  let objectUrl = null;
  let outputName = "Informe_SOAT_A&S.xlsx";

  input?.addEventListener("change", () => {
    const file = input.files?.[0];
    fileName.textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : "Ningún archivo seleccionado";
  });

  const labels = {
    source_records: "Registros fuente", unique_plates: "Placas únicas",
    with_soat: "Placas con SOAT", with_mobility: "Placas con Movilidad",
    mobility_with_reason: "Movilidades con motivo", multiple_candidates: "Múltiples candidatos",
    duration_seconds: "Segundos de procesamiento",
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true; progress.hidden = false; result.hidden = true;
    try {
      const response = await fetch(form.action || window.location.href, {
        method: "POST", body: new FormData(form), cache: "no-store",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!response.ok) {
        const text = (await response.text()).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
        throw new Error(text.includes("No fue posible procesar") ? text.slice(0, 400) : "No fue posible procesar el archivo.");
      }
      const blob = await response.blob();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(blob);
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      outputName = match ? decodeURIComponent(match[1]) : outputName;
      const rawSummary = response.headers.get("X-SOAT-Summary") || "";
      const summary = rawSummary ? JSON.parse(decodeURIComponent(escape(atob(rawSummary.replace(/-/g, "+").replace(/_/g, "/"))))) : {};
      grid.replaceChildren();
      Object.entries(labels).forEach(([key, label]) => {
        const item = document.createElement("div"); item.className = "result-item";
        const value = document.createElement("strong"); value.textContent = String(summary[key] ?? 0);
        const caption = document.createElement("span"); caption.textContent = label;
        item.append(value, caption); grid.append(item);
      });
      resultFile.textContent = outputName; result.hidden = false; result.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      window.alert(error.message || "No fue posible procesar el archivo.");
    } finally {
      submit.disabled = false; progress.hidden = true;
    }
  });
  download?.addEventListener("click", () => {
    if (!objectUrl) return;
    const link = document.createElement("a"); link.href = objectUrl; link.download = outputName;
    document.body.appendChild(link); link.click(); link.remove();
  });
  reset?.addEventListener("click", () => { form.reset(); result.hidden = true; fileName.textContent = "Ningún archivo seleccionado"; input?.focus(); });
});
