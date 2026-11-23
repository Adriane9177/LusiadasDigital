// Tela de revisão: zoom e arraste das imagens, e campos de correção.
(function () {
  "use strict";
  var zoom = 1.15; // 1 = a largura da página ocupa o visor
  try { zoom = parseFloat(localStorage.getItem("revisaoZoom")) || zoom; } catch (e) {}

  var visores = [].map.call(document.querySelectorAll(".visor"), function (caixa) {
    var img = caixa.querySelector("img");
    if (!img) return null;
    var v = { x: 0, y: 0, escala: 1 };

    function desenhar() {
      img.style.transform = "translate(" + v.x + "px," + v.y + "px) scale(" + v.escala + ")";
    }
    v.recentrar = function () {
      if (!img.naturalWidth) return;
      v.escala = caixa.clientWidth / img.naturalWidth * zoom;
      v.x = (caixa.clientWidth - img.naturalWidth * v.escala) / 2;
      v.y = caixa.clientHeight / 2 - img.naturalHeight * v.escala * parseFloat(caixa.dataset.altura);
      desenhar();
    };
    v.ampliar = function (fator, px, py) {
      if (!img.naturalWidth) return;
      if (px === undefined) { px = caixa.clientWidth / 2; py = caixa.clientHeight / 2; }
      v.x = px - (px - v.x) * fator;
      v.y = py - (py - v.y) * fator;
      v.escala *= fator;
      desenhar();
    };

    function falhou() { img.hidden = true; caixa.querySelector(".sem-imagem").hidden = false; }
    img.addEventListener("load", v.recentrar);
    img.addEventListener("error", falhou);
    if (img.complete) { if (img.naturalWidth) v.recentrar(); else falhou(); }

    caixa.addEventListener("wheel", function (e) {
      e.preventDefault();
      var r = caixa.getBoundingClientRect();
      v.ampliar(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });

    var inicio = null;
    caixa.addEventListener("pointerdown", function (e) {
      inicio = { px: e.clientX - v.x, py: e.clientY - v.y };
      caixa.setPointerCapture(e.pointerId);
      caixa.classList.add("arrastando");
    });
    caixa.addEventListener("pointermove", function (e) {
      if (!inicio) return;
      v.x = e.clientX - inicio.px;
      v.y = e.clientY - inicio.py;
      desenhar();
    });
    ["pointerup", "pointercancel"].forEach(function (t) {
      caixa.addEventListener(t, function () { inicio = null; caixa.classList.remove("arrastando"); });
    });
    caixa.addEventListener("keydown", function (e) {
      var passo = { ArrowLeft: [40, 0], ArrowRight: [-40, 0], ArrowUp: [0, 40], ArrowDown: [0, -40] }[e.key];
      if (!passo) return;
      e.preventDefault();
      v.x += passo[0];
      v.y += passo[1];
      desenhar();
    });
    return v;
  }).filter(Boolean);

  document.querySelectorAll("[data-zoom]").forEach(function (b) {
    b.addEventListener("click", function () {
      var fator = parseFloat(b.dataset.zoom);
      zoom = Math.min(8, Math.max(0.3, zoom * fator));
      try { localStorage.setItem("revisaoZoom", zoom); } catch (e) {}
      visores.forEach(function (v) { v.ampliar(fator); });
    });
  });
  function recentrar() { visores.forEach(function (v) { v.recentrar(); }); }
  document.querySelector("[data-recentrar]").addEventListener("click", recentrar);
  window.addEventListener("resize", recentrar);

  function marcar(campo) { campo.classList.toggle("alterado", campo.value !== campo.dataset.original); }
  document.querySelectorAll("textarea[data-original]").forEach(function (c) {
    marcar(c);
    c.addEventListener("input", function () { marcar(c); });
  });
  document.querySelectorAll("[data-inserir]").forEach(function (b) {
    b.addEventListener("click", function () {
      var c = document.getElementById(b.dataset.inserir);
      c.setRangeText('<lb break="no"/>', c.selectionStart, c.selectionEnd, "end");
      c.focus();
      marcar(c);
    });
  });
  document.querySelectorAll("[data-restaurar]").forEach(function (b) {
    b.addEventListener("click", function () {
      var c = document.getElementById(b.dataset.restaurar);
      c.value = c.dataset.original;
      marcar(c);
    });
  });
})();
