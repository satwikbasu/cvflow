# indian-market-data-endpoints

- **Repo:** https://github.com/satwikbasu/indian-market-data-endpoints
- **License:** MIT
- **What it is:** A wire-spec'd catalogue of undocumented public HTTP endpoints for Indian capital-markets data — each endpoint documented with required headers, response shape, quirks, and a copy-pasteable `curl` example, plus a minimal Python wrapper library (`httpx`).
- **Scope:** 7 documented endpoints across AMFI, NSE, niftyindices, and BSE, including:
  - AMFI `NAVAll.txt` — daily NAV for ~16k MF schemes in one ~3 MB file
  - AMFI NAV history (date-range, 18+ years backfillable) and TER JSON API (expense ratios)
  - NSE daily indices CSV (OHLC + P/E + P/B + div yield for ~140 indices)
  - niftyindices.com Total Return Index (inception-to-date via POST)
  - AMFI categorywise monthly/quarterly AAUM
  - BSE LODR Reg 31 quarterly shareholding via iXBRL (required a non-obvious `Origin`-header handshake)
- **Tech:** Python 3.10+, httpx; HTTP reverse-engineering, iXBRL parsing.
- **Highlights:** Documents header tricks that defeated prior scraping attempts; clean per-endpoint docs; installable wrapper (`pip install -e wrappers/python`).
