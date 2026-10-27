# Speech leads, Spark confirms: preparation draft

**Preparation only. Not a decision, not merged.** The Founder picks the product-recognition direction for the 30/10 gate. Until then, no code goes in either direction. Cost of this page: 0 USD. Every number comes from data already on disk (test1, 27/10).

## Flow being considered
1. A `speech.final` arrives from Voice (vi).
2. Candidate check. This is text only, with no model call. A candidate needs both:
   - a product noun: giày, đôi, áo, tay cầm, bàn phím, chiếc, or a catalog name from `keywords()` in `worker/brain/card.py`;
   - a switch phrase: tiếp theo, sang, đây là, cuối cùng, đầu tiên, thứ nhất/hai/ba, giơ lại, món.
3. Spark confirms on the nearest cached frame (`frames.py` keeps 180 s at 1 fps). The question is binary: "is the product held now the same item as the current card?" Inputs are one frame and the current card's name and colour as text. The answer is `same`, `different` or `no product`.
4. What each answer does:
   - `different` opens a new draft card. Its price stays null ("chờ giá") until the seller sets it; price rules do not change.
   - `same` keeps the card.
   - `no product` keeps the card and rechecks once on the frame 5 s later.
5. A new card gets a SAM cut-out, as it does now.
6. A safety net for silent switches: one Spark frame read every 10 s, even when nobody speaks.

Today, by contrast, every `frame` event (1 per second, `worker/media`) goes to `see_product`, which makes one Spark read per frame.

## What test1 shows
Sample: 11 product changes in one recording of 351.5 s with one speaker and 64 `speech.final`.

**The gold cannot answer "does speech come first?".** `labels.json` places each change at the spoken cue (its method, step 2). So "11/11 changes have a spoken cue" is true by construction and proves nothing. The independent signal is the Spark trace `spark_1280.json`: 351 frames at default effort, the 77.8 % run.

| measure (n = 11 changes) | result |
|---|---|
| speech.final ready **before** the Spark result | 8/11 |
| speech.final ready **after** the Spark result | 2/11 (by 3.6 s and 1.4 s) |
| Spark never stable on the new item | 1/11 (SP03) |
| how much earlier speech was (s, sorted) | 1.8, 1.9, 2.1, 3.6, 8.1, 8.1, 8.7, 26.2 |
| switch with > 10 s of silence before the cue | 1 (SP10: 17.5 s; Spark saw the new item 3.3 s before the cue) |

How the table was computed:
- Speech is ready at the end of the cue utterance.
- Spark is ready at the first of 3 consecutive frames that match the new item, plus 3.4 s. The 3.4 s is the minimal-effort p50 from `probe-mini-1027-1545-minimal`, n = 20.
- Mixing default-effort detections with minimal-effort latency is a stated approximation. Run 6a (minimal on all 351 frames) replaces it.

The candidate rule over all 64 finals gives 17 candidates:
- **12** fall inside a change band and cover all 11 changes.
- **5** fall outside:
  - 3 restate the item already on the card. Spark answers `same` and the card is kept; they cost calls only.
  - 2 announce the next item 7.5–10 s before it is held. Spark sees the old item or empty hands, so the 5 s recheck is needed.
- The rule was written from the script's phrasing and checked on the same transcript, with no held-out data. It is a starting point, not a measured rule.

## Cost per live minute (estimate)
Measured unit costs (unit costs come from the `model_call` rows):

| unit | cost | n | source |
|---|---|---|---|
| Spark frame read, minimal, 360×640 | 0.0013061 USD/call, p50 3.4 s | 20 | probe-mini-1027-1545-minimal |
| Spark frame read, default, 720×1280 | 0.005776 USD/call, p50 7.7 s | 351 | spark_1280 |
| Voice | 0.0030 USD/min (0.01755 USD for 351.5 s) | | |
| SAM | 0.0025 USD/call | 33 | |

SAM runs about 1.9 times a minute, i.e. 0.0047 USD/min. That is the same in every option.

| option | Spark calls/min | total USD/min | live minutes under 30 USD/day |
|---|---|---|---|
| now: every frame, minimal 640 | 60 | 0.086 | ~349 |
| now: every frame, default 1280 | 60 | 0.354 | ~85 |
| speech leads: 2.9 candidates + 6 safety net per minute | 8.9 | 0.019–0.031 | ~970–1550 |

The speech-leads range covers two cases: a binary confirm costing 1× or 2× a minimal read. **Its real cost is NOT_RUN.**

## Risks
1. **Speech and hands disagree.** In test1, 3 utterances differ from the script (`labels.json` `heard_vs_script`); one is an ordinal corrected mid-sentence. The confirm protects the card, because Spark decides `different`. The text hint must never set name or price on its own.
2. **Silent switch** (the SP10 case). The 10 s safety net catches it, costing about 6 calls/min. The worst delay is 10 s plus latency.
3. **Announced early, or two items in one sentence**. This was 2 of the 17 candidates. One recheck at +5 s handles it. If the old item is still in view, the candidate is dropped.
4. **Noise, music, a second speaker.** test1 has one quiet speaker. **NOT_RUN.**
5. **The binary question itself.** 0 calls so far, so its accuracy, latency and tokens are **NOT_RUN**. This draft assumes a text description of the card plus one frame. Two images would double the image tokens.
6. **Items outside the fashion enum** (controller, keyboard). Spark's category enum is fashion-only:
   - SP09 took 23.5 s to become stable;
   - SP03 never became stable.

   A "same item?" question does not use the enum. This is the main expected gain, and it is **not measured**.
7. **Voice latency.** `speech.final` latency at live speed is not measured per utterance; `voice.json` ran at 1× over a file.

## Before choosing
- Run 6a (minimal on all 351 frames) and replace the Spark trace above with its output.
- If the Founder picks this direction, run the binary confirm on the 17 candidate frames plus the 11 change frames: ≤ 28 calls, ≤ 0.08 USD at 2× minimal cost. It would be a measurement session on the `keviq` ledger. Not run.

## Evidence (outside git, because test1 is media)
- Inputs, sha256:
  - `data/local/test1/labels.json` eeeb5a69d9eb790ce366892a3ae8523cc3f4ab699cc62b0636567a2669ac7e65
  - `probe/voice.json` 1d076d5e8121757166426189c72442526a364d1e8763756924dd817c2113e796
  - `probe/spark_1280.json` 415bcdfc020d20f4c49ac903c8daedfcdf4f1eb54c11a89664a770f500eec6a1
- Script and output, sha256, kept in the integration owner's notes:
  - `analyze.py` 4f9da032b2dc7391a4262b910163a343d9fe3f058ff0a5b2645e6562ab74c666
  - `out.txt` 537da5730078d17e4be3f2465844a96370a9b639e166403de3ecde8821d2d56d
