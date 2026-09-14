(function () {
  "use strict";

  var root = document.getElementById("asistente-zoho-widget");
  if (!root) return;

  var toggle = document.getElementById("asistente-zoho-toggle");
  var panel = document.getElementById("asistente-zoho-panel");
  var closeBtn = document.getElementById("asistente-zoho-close");
  var form = document.getElementById("asistente-zoho-form");
  var input = document.getElementById("asistente-zoho-input");
  var log = document.getElementById("asistente-zoho-mensajes");
  var endpoint = root.dataset.endpoint;
  var csrfField = form.querySelector("[name=csrfmiddlewaretoken]");
  var csrf = csrfField ? csrfField.value : "";

  // El historial vive solo en esta variable de página: no se persiste en
  // localStorage/sessionStorage ni en el servidor. Se pierde al recargar o
  // navegar, a propósito (conversaciones cortas, sin sesión).
  var MAX_HISTORIAL = 8;
  var historial = [];

  function abrir() {
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    input.focus();
  }

  function cerrar() {
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  }

  toggle.addEventListener("click", function () {
    if (panel.hidden) {
      abrir();
    } else {
      cerrar();
    }
  });
  closeBtn.addEventListener("click", cerrar);

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // Únicamente reconoce enlaces Markdown `[texto](/ruta)` hacia rutas propias
  // del portal (deben empezar por "/"); cualquier otro texto queda escapado
  // como texto plano, nunca como HTML.
  function renderContenido(texto) {
    var escapado = escapeHtml(texto);
    return escapado.replace(/\[([^\[\]]+)\]\((\/[^\s()]*)\)/g, function (_m, etiqueta, url) {
      return '<a href="' + url + '" target="_blank" rel="noopener">' + etiqueta + "</a>";
    });
  }

  function agregarMensaje(rol, texto) {
    var burbuja = document.createElement("div");
    burbuja.className = "asistente-zoho-mensaje asistente-zoho-mensaje--" + rol;
    burbuja.innerHTML = renderContenido(texto);
    log.appendChild(burbuja);
    log.scrollTop = log.scrollHeight;
  }

  function agregarAlHistorial(role, content) {
    historial.push({ role: role, content: content });
    if (historial.length > MAX_HISTORIAL) {
      historial = historial.slice(historial.length - MAX_HISTORIAL);
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var texto = input.value.trim();
    if (!texto) return;

    agregarMensaje("user", texto);
    agregarAlHistorial("user", texto);
    input.value = "";
    input.disabled = true;

    fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({ mensaje: texto, historial: historial }),
      cache: "no-store",
    })
      .then(function (response) {
        if (!response.ok) throw new Error("http_" + response.status);
        return response.json();
      })
      .then(function (data) {
        var respuesta = data && data.respuesta ? data.respuesta : "No fue posible obtener una respuesta.";
        agregarMensaje("assistant", respuesta);
        agregarAlHistorial("assistant", respuesta);
      })
      .catch(function () {
        agregarMensaje("assistant", "No fue posible consultar el asistente en este momento. Intenta de nuevo en unos minutos.");
      })
      .finally(function () {
        input.disabled = false;
        input.focus();
      });
  });
})();
