# Acronyms and trading terms

## Technical terms

| Term | Meaning | In this project |
|---|---|---|
| API | Application Programming Interface | A defined way for software to request data or actions. |
| REST | Representational State Transfer | Here, ordinary HTTP requests such as fetching instruments or submitting an order. |
| SSE | Server-Sent Events | A persistent connection over which the exchange pushes events to your application. |
| MCP | Model Context Protocol | A standard interface for exposing tools/data; the supplied server exposes account-reading tools. |
| LLM | Large Language Model | The AI model used for natural-language questions or announcement analysis. |
| AI | Artificial Intelligence | Broad category that includes LLMs and other models. |
| ML | Machine Learning | Models learned from data; a forecasting model would need suitable data and validation. |
| UI | User Interface | The dashboard people interact with. |
| CSV | Comma-Separated Values | The required downloadable trade-log format. |
| CI | Continuous Integration | Automated checks when code is pushed. |
| ORM | Object-Relational Mapping | A database access layer; not automatically needed for this build. |
| UTC / SGT | Coordinated Universal Time / Singapore Time | SGT is UTC+8; display/export SGT as requested. |
| SSH | Secure Shell | Remote terminal access; users should not need it to stop the bot. |
| SIGTERM | Termination signal | A request for a process to exit cleanly. Ctrl-C normally sends SIGINT instead. |
| Heartbeat | Regular “I am alive” message | Sent by the actual bot, rather than inferred from exchange availability. |
| Backoff | Increasing delay between retries | Avoids hammering an unavailable service. |
| Hysteresis | Resistance to switching states too easily | Prevents regime changes from flip-flopping around a threshold. |

## Trading terms

| Term | Meaning |
|---|---|
| P&L | Profit and Loss: money gained or lost. Distinguish realised results from unrealised value changes. |
| TR / ATR | True Range / Average True Range. Here approximated using absolute changes between consecutive price samples. |
| VWAP | Volume-Weighted Average Price: average trade price weighted by traded volume. Requires volume data. |
| Instrument / ticker | A tradeable item and its identifier. NOVA, ELXR, AXON, CRVX and DRFT are instrument identifiers, not acronyms to expand. |
| Momentum | A strategy that follows an observed price move. |
| Mean reversion | A strategy that expects price to move back toward a recent average. |
| Market-making | Posting buy and sell quotes, while managing accumulated inventory. |
| Bid / ask | The price buyers offer / sellers request. |
| Long / short | Exposure that generally benefits when the price rises / falls. Actual short-selling support must be checked. |
| Limit order | An order with a specified price limit; it may remain unfilled or fill only partly. |
| Liquidity / thin book | Ease of trading without moving the price / few available orders to trade against. |
| Drawdown | A decline from a reference portfolio value, commonly a previous peak. |
| Stop-loss | A rule for limiting losses; its trigger and resulting action must be specified. |
| Exposure | How much the account is affected by price movements. |
| Correlation | How closely instruments' returns move together. |
| Sharpe ratio | Return above a risk-free reference divided by return variability; needs a defined sampling convention and enough observations. |
| Information ratio | Return above a benchmark divided by variability of that excess return; needs a benchmark. |
| Profit factor | Gross profits divided by the absolute value of gross losses; undefined when there are no losses. |

## People and process

| Term | Meaning |
|---|---|
| CIO | Chief Investment Officer in this context. |
| COO | Chief Operating Officer. |
| PM | Portfolio Manager. |
| BA | Business Analyst; recorded some interviews. |
| Quant | Someone who develops quantitative models or trading strategies. |
| Repo | Source-code repository. |
| Sandbox | Test environment, separate from the live trading session. |

