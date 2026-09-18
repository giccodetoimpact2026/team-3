// Persona views (brief §8 decision 5: executive vs trader view). Each view is a
// named set of dashboard sections; everything else is hidden. Pure and
// DOM-free so the layouts are unit-tested in Node (tests/views.test.js).
//
// Executive loads by default: the Deputy COO wants the answer in 30 seconds,
// and a trader switches once (the choice is remembered per browser). The bot
// pill and the kill switch live in the top bar, outside every view.
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api; // Node (tests)
  } else {
    root.Views = api; // browser global
  }
})(typeof self !== "undefined" ? self : this, function () {
  // Sections shown in every view.
  var ALWAYS = ["assistant"];

  var VIEWS = [
    {
      id: "executive",
      label: "Executive",
      audience: "Deputy COO, CIO: did the bot make money, how much, which instruments, was it stable",
      sections: ["cards", "heatmap", "briefing", "pnl"],
    },
    {
      id: "trader",
      label: "Trader",
      audience: "Senior traders: chart with our fills and the forecast, positions, fill rates, order counters",
      sections: ["cards", "ops-strip", "chart", "open-orders", "positions", "fillrate", "pnl"],
    },
    {
      id: "risk",
      label: "Risk",
      audience: "Deputy CIO, risk desk, PM: alerts, exposure against caps, correlation, bot guards",
      sections: ["alerts", "risk-radar", "open-orders", "correlation", "guards"],
    },
    {
      id: "analyst",
      label: "Analyst",
      audience: "Investment analyst: forecast with confidence, trading performance breakdown",
      sections: ["forecast", "performance", "pnl"],
    },
    {
      id: "ops",
      label: "Ops",
      audience: "Trade operations: session summary, counters, order lifecycle and CSV export",
      sections: ["session-summary", "ops-strip", "open-orders", "lifecycle"],
    },
  ];
  var DEFAULT_VIEW = VIEWS[0].id;

  function find(id) {
    for (var i = 0; i < VIEWS.length; i++) if (VIEWS[i].id === id) return VIEWS[i];
    return null;
  }

  function resolveView(id) {
    return find(id) ? id : DEFAULT_VIEW;
  }

  function sectionsFor(id) {
    return find(resolveView(id)).sections.concat(ALWAYS);
  }

  function isVisible(viewId, sectionId) {
    return sectionsFor(viewId).indexOf(sectionId) !== -1;
  }

  return { VIEWS: VIEWS, DEFAULT_VIEW: DEFAULT_VIEW, resolveView: resolveView, sectionsFor: sectionsFor, isVisible: isVisible };
});
