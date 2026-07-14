/*
 * DPDP: reduce on-screen exposure of customer personal data.
 *
 *   <span class="masked-name">RAMESH KUMAR</span>   -> R•••••  K••••
 *   <span class="masked-mobile">9876543210</span>   -> ••••••••10
 *
 * Click any masked value to REVEAL it for a few seconds, then it re-masks
 * automatically. Reveal is by GROUP: clicking one value reveals every masked
 * value in the same row (<tr>) or in the nearest [data-reveal-group] — so a
 * customer's name and mobile reveal (and re-mask) together. It's the CSP's
 * choice to reveal; the real values live only in this local page.
 */
(function () {
  const REVEAL_MS = 4000;

  function maskName(full) {
    return (full || "").trim().split(/\s+/).map(function (w) {
      return w.length <= 1 ? w : w[0] + "•".repeat(Math.min(w.length - 1, 6));
    }).join(" ") || "•••";
  }

  function maskMobile(full) {
    const d = (full || "").replace(/\D/g, "");
    if (!d) return "—";
    if (d.length <= 2) return "•".repeat(d.length);
    return "•".repeat(d.length - 2) + d.slice(-2);
  }

  function maskOf(el) {
    return el.classList.contains("masked-mobile") ? maskMobile(el.dataset.full)
                                                   : maskName(el.dataset.full);
  }

  function init(el) {
    if (el.dataset.mw) return;
    el.dataset.full = (el.dataset.full !== undefined) ? el.dataset.full : el.textContent.trim();
    el.dataset.mw = "1";
    el.textContent = maskOf(el);
    el.style.cursor = "pointer";
    el.title = "Click to reveal for a few seconds";
    el.addEventListener("click", function (e) {
      e.stopPropagation();   // don't trigger row navigation
      e.preventDefault();
      reveal(el);
    });
  }

  function reveal(el) {
    const group = el.closest("[data-reveal-group]") || el.closest("tr") || document.body;
    const items = group.querySelectorAll(".masked-name, .masked-mobile");
    items.forEach(function (it) { it.textContent = it.dataset.full; it.classList.add("revealed"); });
    clearTimeout(group.__maskTimer);
    group.__maskTimer = setTimeout(function () {
      items.forEach(function (it) { it.textContent = maskOf(it); it.classList.remove("revealed"); });
    }, REVEAL_MS);
  }

  function wireAll() {
    document.querySelectorAll(".masked-name, .masked-mobile").forEach(init);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wireAll);
  } else {
    wireAll();
  }
})();
