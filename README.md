# Signal Board

A read-only daily scanner for the S&P 500: moving-average crossovers, MACD
crossovers, RSI extremes, and a handful of basic candlestick patterns
(Doji, Hammer, Shooting Star, Bullish/Bearish Engulfing). It writes its
findings to a small JSON file that a static dashboard reads and displays.

**It never places trades.** It only reads market data and flags things that
might be worth a closer look. You stay the one deciding what to do with it.

## How it works

1. `scanner.py` pulls the current S&P 500 ticker list and ~14 months of
   daily price history per ticker (free data via `yfinance` / Yahoo Finance).
2. It computes SMA20/50/200, MACD, and RSI14, plus checks the most recent
   candle for a few simple patterns.
3. Results are written to `docs/data.json`.
4. `docs/index.html` is a static dashboard that fetches that JSON and
   renders a sortable, filterable table.
5. A GitHub Actions workflow (`.github/workflows/scan.yml`) runs the
   scanner on a schedule, commits the updated `data.json`, and deploys
   `docs/` to GitHub Pages — so the whole thing runs without your laptop
   needing to be on.

## One-time setup

1. **Create a new GitHub repository** (public or private both work for
   GitHub Pages on a free personal account, though private repos with Pages
   require a paid plan on org accounts — personal accounts are fine either
   way).
2. **Push these files** to the repo:
   ```
   git init
   git add .
   git commit -m "Initial signal board"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git push -u origin main
   ```
3. **Enable GitHub Pages**: in the repo, go to *Settings → Pages*, and under
   "Build and deployment" set **Source** to **GitHub Actions**.
4. **Run it once manually** to confirm everything works: go to the
   *Actions* tab → "Daily Stock Scan" → *Run workflow*. Watch it run
   (it takes a few minutes to pull ~500 tickers' worth of data).
5. Once it finishes, your dashboard is live at:
   `https://<your-username>.github.io/<repo-name>/`

After that, it runs automatically on the schedule in `scan.yml`
(weekdays at 21:30 UTC by default — adjust the cron line if you want a
different time) with no further action needed from you.

## Customizing

- **Change the schedule**: edit the `cron:` line in
  `.github/workflows/scan.yml`. [crontab.guru](https://crontab.guru) is
  useful for getting the syntax right. Cron times in GitHub Actions are
  always UTC.
- **Change the ticker universe**: swap out `get_sp500_tickers()` in
  `scanner.py` for any list you want — a manual watchlist, the Nasdaq 100,
  etc.
- **Add/remove signal types**: each detector lives in its own clearly
  named function in `scanner.py` (`detect_candlestick_patterns`,
  `detect_signals`) — straightforward to extend.
- **Trigger a manual run anytime**: the `workflow_dispatch` trigger means
  you can hit "Run workflow" in the Actions tab whenever you want a fresh
  scan outside the schedule.

## Things worth knowing

- **Data source caveat**: `yfinance` pulls from Yahoo Finance's public
  endpoints, which aren't an official paid API — it's free and reliable in
  practice, but if Yahoo changes something, the scan could fail on a given
  day. The workflow will show a red X in the Actions tab if that happens.
- **End-of-day only**: this scans daily candles, so it reflects the
  previous trading day's close, not live intraday price action.
- **It's intentionally simple**: candlestick detection here is straightforward
  rule-based logic on OHLC values, not a black-box library, so you can read
  exactly what triggers each signal.

## Files

```
scanner.py                       # the analysis script
requirements.txt                 # python deps
docs/index.html                  # the dashboard
docs/data.json                   # latest scan results (auto-updated)
.github/workflows/scan.yml       # the scheduler
```
