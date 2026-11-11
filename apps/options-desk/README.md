# Single-Leg Options Desk

One web page for buying one call or one put on SPY, QQQ, NVDA, TSLA, AMD and META.
It is a standalone prototype, separate from the stock analysis system in this repository: it shares no code, config or build with `apps/dsa-web`.

Source: `index.html` in this folder. It runs as a published Claude artifact with the `mcp` capability, calling the viewer's own Robinhood Trading, RH TV HFT (TradingView) and FMP connectors. Opened as a plain file it shows the layout with a "live data is off" notice.

## Where each number comes from

| Panel | Connector call |
| --- | --- |
| Underlying price, day change | Robinhood Trading `get_equity_quotes` (`last_trade_price`, `adjusted_previous_close`) |
| VWAP, RSI 14 daily, RSI 14 5-min | RH TV HFT (TradingView) `mcp-tv-get-symbol-data-batch` (`VWAP`, `RSI`, `RSI\|5`) |
| Liquidity rank and top pick | Robinhood Trading `get_option_instruments` (at-the-money strike, two nearest expirations) and `get_option_quotes`; rank = volume rank + spread rank; a ticker with earnings inside three sessions cannot be top pick |
| Dealer levels | Robinhood Trading `get_option_chains`, `get_option_instruments`, `get_option_quotes`, run through a JavaScript port of `gex-vex-heatseeker/scripts/gex_map.py` |
| Contract picker | Robinhood Trading `get_option_instruments`, `get_option_quotes` |
| Trade plan | The selected contract's ask, plus `get_portfolio` `total_value` for the 5% cap |
| Open positions | Robinhood Trading `get_accounts`, `get_option_positions`, `get_option_quotes` |
| Earnings | FMP `calendar` (`earnings-company`, limit 3; higher limits are refused by the current FMP plan) |
| Economic releases | RH TV HFT `mcp-tv-get-economic-calendar` (US, high importance, today) |

The page can call only those ten read tools. No order, preview, cancel or exercise tool is in its permission list.

## Refresh

- Open the page in claude.ai while signed in and allow the three connectors when asked.
- Prices, VWAP, RSI, contract quotes, account value and positions refresh about every 30 seconds while the page is visible.
- The liquidity rank refreshes every two minutes. The page opens on the top pick until you tap a ticker.
- **Refresh** re-pulls every panel at once. Each panel shows the time of its last pull.
- Dealer levels are computed once per trading day and kept in the browser. **Recompute levels** rebuilds them. The first build of the day can take up to a minute.
- Earnings and the economic calendar load once per page open.
- Choose the account once in the Open positions panel. The page remembers it in that browser; no account number is stored in the source.

## Not covered

- Orders of any kind.
- Spreads, short options and assigned stock. Sizing and stops assume one long call or put.
- Market holidays and half days. The 3:45 PM ET countdown uses the clock only.
- Intraday rebuilds of the dealer map, air pockets, vanna and charm tables, and the heatseeker entry gates.
- Dividends, early exercise and fees in break-even (strike plus or minus the ask).
- Direction. The top pick is the most liquid ticker, not a call-or-put signal.
- Slippage. Stops and scale-outs are premium levels, not resting orders.
- Other tickers.

## Checked and not checked

- The dealer-level port returns the same flip, walls, net GEX and flow drift as `gex_map.py` on two test chains.
- A Sonnet checker compared 69 displayed values against fresh connector calls: no mismatches. Earnings dates were re-checked separately and matched.
- That check ran on a test render fed with captured connector responses. The published page was not observed running under a signed-in viewer.
- The positions table was tested with sample positions only; the account used for testing held none. It assumes Robinhood reports `average_price` per contract (premium times 100).
- The liquidity rank was tested on a render fed with the 1:35 PM ET scan of 2026-11-11 (SPY first, then QQQ, NVDA, TSLA, AMD, META).
- The full six-ticker dealer build (several hundred SPY and QQQ contracts) has not been run end to end.
