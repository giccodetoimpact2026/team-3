// Thin exchange REST wrapper (jQuery). Auth is X-API-Key on every call.
// Read-only: the dashboard displays state, it never places orders.
const ExchangeAPI = {
  // GET /portfolio -> cash, positions, P&L (integer cents). Unwraps the
  // {status, data} envelope.
  getPortfolio: function () {
    return $.ajax({
      url: CONFIG.API_BASE_URL + "/portfolio",
      method: "GET",
      dataType: "json",
      headers: { "X-API-Key": CONFIG.API_KEY },
    }).then(function (resp) {
      return resp && resp.data ? resp.data : resp;
    });
  },
};
