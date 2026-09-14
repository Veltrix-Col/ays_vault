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

  // Reconoce un subconjunto mínimo de Markdown -- **negrilla**, encabezados
  // "#"/"##", viñetas "-"/"*" y listas numeradas, y enlaces `[texto](url)`
  // hacia una ruta propia del portal ("/...") o hacia Zoho
  // (https://crm.zoho.com/...) -- siempre sobre texto ya escapado; cualquier
  // otra cosa queda como texto plano, nunca como HTML.
  function transformarLinea(contenidoCrudo) {
    var escapado = escapeHtml(contenidoCrudo);
    escapado = escapado.replace(/\[([^\[\]]+)\]\((\/[^\s()]*|https:\/\/crm\.zoho\.[a-z.]+\/[^\s()]*)\)/g, function (_m, etiqueta, url) {
      return '<a href="' + url + '" target="_blank" rel="noopener">' + etiqueta + "</a>";
    });
    return escapado.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }

  function renderContenido(texto) {
    var lineas = String(texto == null ? "" : texto).split(/\r?\n/);
    var html = "";
    for (var i = 0; i < lineas.length; i++) {
      var linea = lineas[i];
      var encabezado = linea.match(/^#{1,6}\s+(.*)$/);
      var numerada = linea.match(/^\s*(\d+\.)\s+(.*)$/);
      var vinieta = linea.match(/^\s*[-*]\s+(.*)$/);
      if (encabezado) {
        html += '<div class="azh-heading">' + transformarLinea(encabezado[1]) + "</div>";
      } else if (numerada) {
        html += '<div class="azh-item">' + numerada[1] + " " + transformarLinea(numerada[2]) + "</div>";
      } else if (vinieta) {
        html += '<div class="azh-item">• ' + transformarLinea(vinieta[1]) + "</div>";
      } else if (linea.trim() === "") {
        html += '<div class="azh-blank"></div>';
      } else {
        html += "<div>" + transformarLinea(linea) + "</div>";
      }
    }
    return html;
  }

  function agregarMensaje(rol, texto) {
    var burbuja = document.createElement("div");
    burbuja.className = "asistente-zoho-mensaje asistente-zoho-mensaje--" + rol;
    burbuja.innerHTML = renderContenido(texto);
    log.appendChild(burbuja);
    log.scrollTop = log.scrollHeight;
  }

  function mostrarIndicadorCarga() {
    var burbuja = document.createElement("div");
    burbuja.className = "asistente-zoho-mensaje asistente-zoho-mensaje--assistant asistente-zoho-mensaje--cargando";
    burbuja.setAttribute("aria-label", "El asistente está pensando");
    burbuja.innerHTML = "<span></span><span></span><span></span>";
    log.appendChild(burbuja);
    log.scrollTop = log.scrollHeight;
    return burbuja;
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
    var indicador = mostrarIndicadorCarga();

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
        indicador.remove();
        input.disabled = false;
        input.focus();
      });
  });
})();
