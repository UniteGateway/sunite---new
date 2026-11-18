# SUNITE — Proposal Generator module

A five-step proposal builder for Unite Solar sales staff and franchise partners.

1. **Client** — name, address, DISCOM, tariff category, contract load, transformer, sponsor, quote number and validity.
2. **Power bills** — upload bills (PDF or photo). The AI reads units, bill amount, contract demand, category and past-month history, then fills the average monthly consumption and average tariff (bill amount ÷ units). All values stay editable.
3. **Site & photos** — address, Google Maps link or `lat, long`; upload site photos or Google Earth screenshots. The AI reads measured area, any capacity written on the image (e.g. `2.5MWp`), shading and notes, and suggests capacity per plot (ground ≈ 8,400 m²/MWp, rooftop ≈ 100 sq ft/kWp).
4. **System & budget** — fixed sponsor budget (incl. GST) or rate per kWp; net metering, behind-the-meter, battery (BESS), grid sale / open access, GO charges; assumptions and payment schedule. Warns when land or contract load is exceeded.
5. **Proposal** — cover, quotation, site page with photos, energy balance, 25-year financials, payment schedule and terms. Downloads as a print-ready HTML file (Print → Save as PDF).

A live quote card shows total price, share of consumption met, year-1 benefit, payback and 25-year benefit.

## Files

- `index.html` — the whole module (HTML, CSS and JavaScript in one file). External: Inter from Google Fonts, pdf.js 3.11.174 from cdnjs (renders the first page of PDF bills).

## Pricing and energy model

- Solar GST: 70:30 rule — 5% on 70% (goods) + 18% on 30% (services).
- Battery GST: 18%, entered as an all-inclusive amount.
- GO / allotment charges: no GST.
- In budget mode the grid-sale block takes the solar the budget allows after net metering and behind-the-meter; the total is held exactly to the budget.
- Monthly energy balance: solar meets daytime load first, surplus charges the battery (90% usable, 88% round trip), surplus beyond the battery is credited under net metering; grid-sale units are priced at the grid-sale tariff.
- 25-year model: tariff escalation, panel degradation and battery fade are editable.

## Integrating with the SUNITE app

The page was built as a hosted claude.ai artifact. Two features use that host's runtime:

| Feature | Runtime call | What to wire in the SUNITE app |
|---|---|---|
| Bill and site-photo reading | `askJSON(prompt, blob)` → `claude.use('sample')` | Replace `askJSON` with a call to your backend, which sends the image and prompt to the Claude API and returns the JSON object. |
| Proposal download | `downloads.save()` → `claude.use('downloads')` | Replace with a normal browser download (`<a download>` with a Blob URL) or server-side PDF generation. |

When the runtime is not present, the page still works: bill and photo reading is hidden and the figures are typed in by hand, and Copy summary replaces the download button.

Draft data is kept in the browser's `localStorage` under `sunite-pg` (photos are not stored). For multi-user use, move state to the app's database.
