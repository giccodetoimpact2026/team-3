// Client-side tab switching for the single-page dashboard. Kept tiny and
// framework-free. Tabs are just sections toggled with a class; the SSE feed and
// the DashboardController are shared across all of them (one connection, one
// data model) rather than one page per tab.
//
// A tab is a nav link `<a data-tab="execution">` and a matching
// `<section id="tab-execution" class="tab-panel">`. The active tab is kept in
// the URL hash so a reload (and the browser back button) restores it. When a
// tab becomes visible it fires a `tab:show` event with `{ tab }` detail so the
// controller can lazily render/redraw content that needs the panel laid out
// (e.g. canvas charts, which need a non-zero size to draw).
const Tabs = {
  _current: null,

  init: function () {
    var self = this;
    document.querySelectorAll("[data-tab]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        e.preventDefault();
        self.show(el.getAttribute("data-tab"));
      });
    });
    window.addEventListener("hashchange", function () {
      self.show((location.hash || "").replace(/^#/, ""), true);
    });
    var initial = (location.hash || "").replace(/^#/, "");
    this.show(this._has(initial) ? initial : "overview", true);
  },

  _has: function (name) {
    return !!(name && document.getElementById("tab-" + name));
  },

  show: function (name, fromHash) {
    if (!this._has(name)) name = "overview";
    if (name === this._current) return;
    this._current = name;

    document.querySelectorAll(".tab-panel").forEach(function (p) {
      p.classList.toggle("active", p.id === "tab-" + name);
    });
    document.querySelectorAll("[data-tab]").forEach(function (a) {
      var on = a.getAttribute("data-tab") === name;
      a.classList.toggle("active", on);
      if (on) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });

    if (!fromHash) {
      try { history.replaceState(null, "", "#" + name); } catch (_) { location.hash = name; }
    }
    window.dispatchEvent(new CustomEvent("tab:show", { detail: { tab: name } }));
  },

  current: function () { return this._current; },
};

if (typeof module !== "undefined" && module.exports) module.exports = Tabs;
