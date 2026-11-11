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
| Dealer levels | Robinhood Trading `get_option_chains`, `get_option_instruments`, `get_option_quotes`, run through a JavaScript port of `gex-vex-heatseeker/scripts/gex_map.py`. One deliberate difference: expiry is taken at 4:00 PM New York time; the source script pins 20:00 UTC all year, which is an hour early in winter |
| Gate check | The same quotes and map, plus TradingView `volume` and `average_volume_10d_calc` for the volume ratio and FMP earnings dates |
| Desk log | The page's own artifact database (`db` capability), collection `ledger`, one document per levels rebuild |
| Contract picker | Robinhood Trading `get_option_instruments`, `get_option_quotes` |
| Trade plan | The selected contract's ask, plus `get_portfolio` `total_value` for the 5% cap |
| Open positions | Robinhood Trading `get_accounts`, `get_option_positions`, `get_option_quotes` |
| Earnings | FMP `calendar` (`earnings-company`, limit 3; higher limits are refused by the current FMP plan) |
| Economic releases | RH TV HFT `mcp-tv-get-economic-calendar` (US, high importance, today) |

The page can call only those ten read connector tools. No order, preview, cancel or exercise tool is in its permission list.

## Refresh

- Open the page in claude.ai while signed in and allow the three connectors when asked.
- Prices, VWAP, RSI, contract quotes, account value and positions refresh about every 30 seconds while the page is visible.
- The liquidity rank refreshes every two minutes. The page opens on the top pick once and never switches ticker on its own after that.
- **Refresh** re-pulls every panel at once. Each panel shows the time of its last pull.
- Dealer levels and the gate check rebuild whenever the map is more than 30 minutes old: on open, on **Refresh**, and every 30 minutes while the market is open. **Rebuild levels now** forces it. A rebuild can take up to a minute; the previous map stays on screen meanwhile.
- Each rebuild saves one entry to the Desk log, no-trade reads included.
- Earnings and the economic calendar load once per page open.
- Choose the account once in the Open positions panel. The page remembers it in that browser; no account number is stored in the source.

## Not covered

- Orders of any kind.
- Spreads, short options and assigned stock. Sizing and stops assume one long call or put.
- Market holidays and half days. The 3:45 PM ET countdown uses the clock only.
- Setup triggers. The gate check covers only the numeric heatseeker gates (entry window, earnings, volume ratio, map age, flip within ±2%, flow drift, regime versus flip, index alignment, vanna). The six setups, their 5-minute-bar triggers, air pockets and charm are not evaluated, so "gates clear" is never a buy signal.
- Strikes beyond ±3% of spot. A flip or wall outside that band shows as "none in loaded strikes".
- The entry window applies the 3:35 PM ET cut-off to every expiration, not only same-day ones, because of the 3:45 PM exit.
- Dividends, early exercise and fees in break-even (strike plus or minus the ask).
- Direction. The top pick is the most liquid ticker, not a call-or-put signal.
- Slippage. Stops and scale-outs are premium levels, not resting orders.
- Other tickers.

## Checked and not checked

- The dealer-level port returns the same flips, walls, net vanna and flow-adjusted flip as `gex_map.py` on three chains: with the script fed a time one hour earlier on November dates (the expiry correction), and unshifted on a July date.
- Rebuild-when-stale, the single log write per rebuild, a fresh-cache reopen and a no-database load were exercised on a test render. One log entry was written to and read back from the published page's store.
- A Sonnet checker compared 69 displayed values against fresh connector calls: no mismatches. Earnings dates were re-checked separately and matched.
- That check ran on a test render fed with captured connector responses. The published page was not observed running under a signed-in viewer.
- The positions table was tested with sample positions only; the account used for testing held none. It assumes Robinhood reports `average_price` per contract (premium times 100).
- The liquidity rank was tested on a render fed with the 1:35 PM ET scan of 2026-11-11 (SPY first, then QQQ, NVDA, TSLA, AMD, META).
- The full six-ticker dealer build (several hundred SPY and QQQ contracts) has not been run end to end.
