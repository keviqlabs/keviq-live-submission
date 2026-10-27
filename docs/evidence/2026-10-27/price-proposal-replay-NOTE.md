# Scope of the price proposal replay (D-024)

- Question: on test1, do the 3 real price changes (SP01 250000→220000, SP04 180000→160000, SP09 650000→600000) become a `price_proposal` on the right card, and how many proposals carry a later product's price on an earlier card? Numbers only, no threshold.
- Input: the recorded `product_seen` (6) and `speech` (9 first-price lines) of `probe-mini-1027-1545-default`, the session D-023 was measured on, in recorded ingest order. The 3 real changes (utterances 7–9, 30–31, 54–55) are not in that recording, so they are added from the test1 voice rows on the same session clock, each placed at its end time plus the recorded speech ingest lag (median 5 ms). Variant A is this stream (22 events). Variant B adds all 64 voice rows instead of the 9 recorded lines (70 events).
- Run: once on 27/10, branch `price-proposal-route`, card worker `worker/brain/card.py` with `MODEL_API_KEY` empty, so every speech goes through the rule reader. The app runs on the PostgreSQL test database, and each event is posted and handled to completion before the next. Sessions are `replay-pp-test1-1027-2244-A` and `-B`.
- Cost: model_call is 0 in the store for both sessions. The SQL in the file over `events` returns 0 rows for both sessions, so 0 calls and 0 USD.
- A card's product is the product whose first price it holds; `card_labelled_product` is what was in hand at the frame that opened it. A proposal counts as "same product" when the line's product (gold, else the labelled segment) equals the card's price product.

| | A | B |
|---|---|---|
| cards | 6 | 6 |
| card prices changed by speech | 0 | 0 |
| `price_proposal` | 11 | 12 |
| same product | 4 | 4 |
| a later product's price on an earlier card | 7 | 8 |

- SP01: 2 proposals (u8 heard 230000, u9 heard 220100) on the card holding SP01's 250000. That card was opened on the host's shirt; no card was opened for SP01. u7 gave none.
- SP04: 2 proposals (u30 heard 160000, u31 heard 160100) on the card holding SP04's 180000. That card was opened on SP03.
- SP09: 2 proposals (u54 heard 600000, u55 heard 600100) on SP06's card (210000). No card holds SP09's price, because SP09's first price (u49) is itself a proposal on SP06's card, and no card was opened for SP09.
- In A, 7 proposals carry a later product's price: u18 (SP03 on SP02's card), u34 (SP05 on SP04's card), u42, u46 and u49 (SP07, SP08 and SP09 on SP06's card), plus u54 and u55. The first five are the 5 overwrites D-023 removed. B adds u62 (SP01's return price on the card holding SP10's price).
- Heard prices are the rule reader's reading. Three lines read 100 above the spoken price: 220100, 160100 and 600100.
- `price-proposal-replay-test1-1027-2244.json` keeps card ids, versions, prices, utterance indices and times, and SP codes. It holds no line text and no frames.
