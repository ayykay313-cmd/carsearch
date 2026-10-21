# Car Watch

Watches UAE used-car sites for hybrids and sends Telegram alerts for **new** matches only, plus price
drops on cars you've already been told about.

| Run | When (Dubai time) | Sites | What you get |
| --- | --- | --- | --- |
| **Watch** | every 30 min, 6am–11:30pm | Toyota Pre-Owned, Honda Pre-Owned | instant alert for an available **2026+ RAV4 hybrid**, **any Camry hybrid** or **any Accord hybrid** |
| **Daily report** | 1pm | all sites | everything that matches your criteria, incl. official-site cars the watch skips (e.g. 2025 RAV4 hybrids) |

- Only **available** cars: reserved/booked/sold/coming-soon listings are skipped (and alerted if they come back).
- Only full hybrids (HEV) and plug-ins: 48V/12V mild hybrids are excluded.
- A car is reported once. Tap **👎** in Telegram to remove a car from consideration for good.
- Everything is editable in [config.yaml](config.yaml): criteria, watch rules (`watch:`), watch hours,
  priority models, red-flag words, deal-score thresholds, sites.

## Run it in the cloud (GitHub Actions) — recommended

Your computer doesn't need to be on. The workflow [.github/workflows/carwatch.yml](.github/workflows/carwatch.yml)
runs the 30-minute watch and the daily report on GitHub's servers.

1. **Create a Telegram bot** (steps below) and note the bot token and your chat id.
2. **Create a GitHub repository** at <https://github.com/new>, e.g. `car-watch`.
   - *Public* repo: Actions minutes are free and unlimited. The code and `config.yaml` are visible;
     your secrets and the database are not (but run logs, which list cars checked, are).
   - *Private* repo: the free plan includes 2,000 Actions minutes/month. This setup uses roughly
     2,500–3,500 a month (watch ≈ 2–3 min × 35 a day, daily ≈ 10–15 min), so runs would stop part-way
     through the month unless you add a payment method (about $4–12/month at GitHub's rates) or check
     less often.
3. **Push this folder** to the repo (GitHub shows the commands after you create it), e.g.:
   ```bash
   git remote add origin https://github.com/<you>/car-watch.git
   ```
   ```bash
   git push -u origin main
   ```
4. **Add secrets**: repo → *Settings → Secrets and variables → Actions → New repository secret*:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `CLAUDE_CODE_OAUTH_TOKEN` *(optional, for AI verdicts)*: run `claude setup-token` on your
     computer and paste the token it prints. It uses your Claude subscription, not an API key.
5. **Test it**: repo → *Actions → Car Watch → Run workflow*, pick `watch`, then `daily`.
6. **Turn off the local schedule** so you don't get every alert twice:
   ```bash
   python -m carwatch uninstall-schedule
   ```

How it works on GitHub:
- The database (seen cars, prices, your 👎 taps) is kept in the Actions cache between runs. If GitHub
  ever drops that cache, the next run treats everything as new and you get one big report.
- GitHub can start scheduled runs late (often 5–20 minutes, occasionally more) when it's busy.
- In a **public** repo, GitHub pauses scheduled workflows after 60 days with no commits. It emails
  you first; re-enable it under *Actions → Car Watch → Enable workflow* (or push any small change).
- Each run's `logs/` and `reports/` are attached to the run as an artifact for 14 days.
  GitHub also emails you if a run fails, and Car Watch sends a Telegram alert (the watch at most
  once a day).
- The official Toyota/Honda sites, DubiCars and the others may treat GitHub's data-centre IPs
  differently from your home connection. If a site blocks the cloud runs, the report says
  "⛔ blocked" for it (no workaround attempted); the fallback is running locally (below).

## Run it on your computer (Windows / Mac / Linux)

```bash
python -m pip install -r requirements.txt
```
```bash
python -m playwright install chromium
```
```bash
python -m carwatch install-schedule
```

This installs three scheduled jobs:
- **Daily report** at 13:00 Dubai time (converted to your time zone). If the computer was asleep or
  off, it runs as soon as possible afterwards.
- **Watch** every 30 minutes, 6am–11:30pm Dubai time (`watch.interval_minutes`).
- **Telegram sync** every 15 minutes, to apply your 👎 taps.

Windows uses Task Scheduler (`CarWatchDaily`, `CarWatchPreownedWatch`, `CarWatchTelegramSync`), Mac
uses launchd, Linux uses cron. Remove them with `python -m carwatch uninstall-schedule`. Locally the
secrets go in `secrets.env` (copy `secrets.env.example`; it's git-ignored).

## Telegram bot (free, about 2 minutes)

1. In Telegram, open **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`.
   BotFather replies with a token like `123456789:AA...`.
2. Copy `secrets.env.example` to `secrets.env` and paste the token after `TELEGRAM_BOT_TOKEN=`.
3. Open your new bot in Telegram, press **Start** and send it any message (e.g. `hi`).
4. Save your chat id automatically (it's printed too, for the GitHub secret):
   ```bash
   python -m carwatch telegram-setup
   ```
5. Check it works:
   ```bash
   python -m carwatch telegram-test
   ```

Keep the token private: anyone who has it can post as your bot.

### Removing cars from consideration

Every listing has a number (`#1`, `#2`, …) and a **👎 #N** button under the message.

- Tap **👎 #N** and that car is never reported again: no new-listing or price-drop alerts, even if
  it's relisted on another site. The button turns into **↩️ #N**; tap it to undo.
- Or type in the chat: `remove 3`, `remove 3 7`, `undo 3` (numbers from the latest message).
- Only messages from your own chat (`TELEGRAM_CHAT_ID`) are acted on.

Taps are picked up at the start of every run (every 30 minutes in the cloud, every 15 minutes locally).
Telegram keeps unread taps for 24 hours. `python -m carwatch status` lists everything you've removed.

## AI verdicts (optional)

Top listings in the daily report get a one-line verdict from `claude -p` (Claude Code, headless),
using your subscription: locally your `claude` login, in the cloud the `CLAUDE_CODE_OAUTH_TOKEN`
secret. `ANTHROPIC_API_KEY` and other API settings are removed from its environment, so an API key is
never used. If it's missing or fails, the report goes out without verdicts. The watch skips
verdicts to stay fast. Turn them off with `ai_summary.enabled: false`.

Locally, if `logs/run.log` shows "OAuth access token has expired", run `claude` in a terminal, type
`/login`, then check:

```bash
claude -p "say OK"
```

## Commands

```bash
python -m carwatch run                 # daily report now (all sites)
python -m carwatch watch               # watch now (Toyota/Honda pre-owned)
python -m carwatch run --dry-run       # save the report, don't send or mark as seen
python -m carwatch run --sources dubicars,cars24 --no-ai
python -m carwatch status              # removed cars, last 10 runs and per-site status
python -m carwatch telegram-sync       # apply Telegram taps now
```

## How it decides

| Step | Rule |
| --- | --- |
| Every site | available (not reserved/booked/sold/coming soon), a hybrid (site fuel type says Hybrid, or title/trim/description says hybrid/HEV), not a mild hybrid, not electric/diesel |
| Toyota/Honda Pre-Owned | only the listed models (Camry, RAV4, Accord); no price/km/year/location limits. The watch narrows this to the `watch.rules` (RAV4 2026+, any Camry, any Accord) |
| Other sites | year ≥ `min_year`, `min_km` ≤ km < `max_km` (0 km new stock excluded), price < `max_price_aed`, GCC specs, sedan/SUV, not an excluded make, Dubai (main) or Sharjah/Abu Dhabi (lower priority) |
| New | matches now and was never included in a sent message (by site listing ID) |
| Price drop | already reported, still matches, price lower than when you were last told |
| Plug-in | PHEV keywords in title/trim/description → own section |
| Red flags | whole-word keywords in title + description; "accident free", "no accidents" etc. are ignored → Flagged section |
| Mislabel | site says Hybrid but "hybrid"/"HEV" (or Lexus-style `300h`) is nowhere in title, trim or description, or the title says Turbo/Petrol → Flagged section |
| Mild hybrids | excluded: `mild_hybrid_keywords` (48V, MHEV, …) or a model in `mild_hybrid_models` (Volvo B5s, Audi TFSI, Mercedes 200s, Suzuki "Hybrid") |
| Deal score | % below the average price of other hybrids with the same make, model and year (GCC, last 60 days). Needs 3+ comparables; 10% = 💰 deal, 15% = 🔥 great deal. Until then, sorted by price then km |
| Duplicates | the same car on two sites (make, model, year, km within 1%) is reported once, with an "also on" link |

## Sources

| Site | Status | How |
| --- | --- | --- |
| Toyota Pre-Owned | ✅ top priority, every 30 min | toyota.ae model pages (`camry-hybrid`, `camry`, `rav4`) rendered in headless Chromium (Playwright), since the cars load via a token-protected API. "Reserved" cards skipped. A page whose results don't load is retried, then reported as not loaded (never as "no cars"). Detail pages are off-limits in robots.txt and never opened |
| Honda Pre-Owned | ✅ top priority, every 30 min | honda.ae `accord-hybrid` (watch) and `accord` (daily) pages, same method |
| DubiCars | ✅ daily | `/uae/used/hybrid-cars?pt=<price>&kt=<km>` (allowed by robots.txt), plus priority-model pages to catch hybrids filed under "Petrol"; detail page fetched only for new candidates. Sold/inactive cards skipped |
| CarSwitch | ✅ daily | `/uae/used-cars/electric-hybrid-cars-for-sale-in-uae`; robots.txt bans its filter parameters, so filtering is local. Detail page for candidates (body, fuel type, drops EVs). No seller description, so red flags only check the title |
| Cars24 | ✅ daily | `/buy-used-hybrid-cars-dubai/` plus Camry/RAV4/Accord pages. Card data includes status (RESERVED / COMING_SOON skipped), GCC badge, hub city and fuel type |
| Kavak | ✅ daily | `/ae/preowned/hybrid` plus the year pages from `min_year` on (Kavak's hybrid filter misses some HEVs; the trim text is checked). robots.txt asks for 20 s between requests, which is respected. Booked cars skipped. Kavak shows no city (delivers UAE-wide), so its cars count as Dubai; specs are taken from its `gcc/` image path |
| Dubizzle | ⛔ blocked | Imperva bot protection on plain requests; skipped, not bypassed |
| YallaMotor | ⛔ blocked | Cloudflare challenge (HTTP 403); skipped, not bypassed |

Request volume: each watch loads 3 pages. The daily run makes about 30–40 requests to DubiCars,
a handful each to CarSwitch, Cars24 and the official sites, and about 10–20 to Kavak, plus a detail
page for each new candidate (at most 40 per site per run), with random 4–8 s pauses (20–25 s on Kavak).
