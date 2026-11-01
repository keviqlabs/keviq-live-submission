# Receipt keeps the confirmed card version: run of 1 November

Synthetic data only, no Model API key, no real Messenger, 0 USD.

## Tests, red then green

`tests/test_orders_receipt_version.py` (12 tests: 7 for the change, 4 that keep current behaviour, 1 for the C5
receipt count) run against `orders/flow.py` before the change, then after it:

```
$ python -m pytest -q tests/test_orders_receipt_version.py   # in an export of the tree before the change
7 failed, 5 passed in 0.09s
$ uv run pytest -q tests/test_orders_receipt_version.py      # this branch
12 passed in 0.05s
```

The 7 that failed before: the three image-only versions before drafting (`assert 1 == 2`, `1 == 3`: the order
stayed on v1), a card without a SKU (`CONFIRM_SENT` instead of `NEEDS_SELLER`), a SKU change after the quote
(`CONFIRMED` instead of `NEEDS_SELLER`), the quote payload (`KeyError: 'image_url'`) and the C5 count
(`(1, 1, 0) == (1, 1, 1)`: receipts, version ok, with image).

## End-to-end run

App and worker on port 8060 with a fresh PostgreSQL database named `*_test` in a local container, provider key
blank. Each tree ran the same steps:

1. `python -m connectors.sim.replay --session demo1 --speed 40` (fixtures on).
2. `python -m connectors.sim.replay --file docs/evidence/2026-11-01/receipt-version-lock-session.jsonl --speed 1`:
   three live cards with hand-written order labels (`fx-` card events keep the labels worker off).
   - `card-sb`: v1 has no image; the comment; v2 adds only the image; then the label (drafting).
   - `card-sc`: v1 has no image; the comment and the label (quote on v1); v2 adds only the image after the quote.
   - `card-sd`: v1 has an image and no SKU.
3. `CONFIRM:<order_id>` through `POST /api/sessions/{sid}/sim/messenger` for each order in `CONFIRM_SENT`.
4. Events read back over SSE (`/events?cursor=0&follow=false`).

"before" is the main tree with the cancel-question changes and without this change; "after" adds it.

```
== before: locked_card in flow.py: 0
  sb-demo1-before: events=69 receipts with image 2/2; worker-made card versions=0
    approval_queue=['o-05', 'o-14']
  sb-lock-before: events=29 receipts with image 1/3; worker-made card versions=0
    o-sb: DRAFT->CONFIRM_SENT->CONFIRMED receipt v1 image=None
    o-sc: DRAFT->CONFIRM_SENT->CONFIRMED receipt v1 image=None
    o-sd: DRAFT->CONFIRM_SENT->CONFIRMED receipt v1 image=sim://sd-v1.png
    msg confirm_order: {'order_id': 'o-sc', 'card_version': 1, 'sku': 'SC-1', 'name': 'Giày thử C'}
    msg confirm_order: {'order_id': 'o-sd', 'card_version': 1, 'sku': None, 'name': 'Giày thử D'}
    msg confirm_order: {'order_id': 'o-sb', 'card_version': 1, 'sku': 'SB-1', 'name': 'Giày thử B'}
    msg receipt: {'order_id': 'o-sb', 'card_version': 1, 'image_url': None}
    msg receipt: {'order_id': 'o-sc', 'card_version': 1, 'image_url': None}
    msg receipt: {'order_id': 'o-sd', 'card_version': 1, 'image_url': 'sim://sd-v1.png'}
== after: locked_card in flow.py: 5
  sb-demo1-after: events=69 receipts with image 2/2; worker-made card versions=0
    approval_queue=['o-05', 'o-14']
  sb-lock-after: events=26 receipts with image 1/2; worker-made card versions=0
    o-sb: DRAFT->CONFIRM_SENT->CONFIRMED receipt v2 image=sim://sb-v2.png
    o-sc: DRAFT->CONFIRM_SENT->CONFIRMED receipt v1 image=None
    o-sd: DRAFT->NEEDS_SELLER
    msg confirm_order: {'order_id': 'o-sc', 'card_version': 1, 'sku': 'SC-1', 'name': 'Giày thử C', 'image_url': None}
    msg waiting_for_shop: {'order_id': 'o-sd'}
    msg confirm_order: {'order_id': 'o-sb', 'card_version': 2, 'sku': 'SB-1', 'name': 'Giày thử B', 'image_url': 'sim://sb-v2.png'}
    msg receipt: {'order_id': 'o-sb', 'card_version': 2, 'sku': 'SB-1', 'name': 'Giày thử B', 'image_url': 'sim://sb-v2.png'}
    msg receipt: {'order_id': 'o-sc', 'card_version': 1, 'sku': 'SC-1', 'name': 'Giày thử C', 'image_url': None}
```

Receipts with an image: demo1 2/2 before and 2/2 after (both receipts there are fixture orders, so demo1 does not
exercise this change; its 69 events and the approval queue `o-05`, `o-14` are unchanged). Lock session: 1/3 before,
1/2 after. The receipt of `o-sb` moves from v1 without an image to v2 with the image. `o-sd` (no SKU) goes to the
seller instead of being confirmed, so it leaves the receipt count. `o-sc` keeps v1 without an image on both trees:
an image that arrives after the quote does not reach the receipt.
