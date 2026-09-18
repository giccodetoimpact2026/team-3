// Pure formatting helpers, kept in their own file so they can be unit-tested in
// Node (see tests/) and loaded as browser globals. No DOM, no jQuery.
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api; // Node (tests)
  } else {
    root.formatCurrency = api.formatCurrency; // browser global
  }
})(typeof self !== "undefined" ? self : this, function () {
  // Money is INTEGER CENTS on the exchange wire; render as dollars ($1,510.00).
  function formatCurrency(cents) {
    const dollars = (cents || 0) / 100;
    const sign = dollars < 0 ? "-" : "";
    const abs = Math.abs(dollars).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    return sign + "$" + abs;
  }

  return { formatCurrency };
});
