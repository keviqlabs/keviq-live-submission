# Permission table (v1) — code: `orders/rules.py`, machine-readable: `permissions.json`

No prompt can change this table. `orders/rules.py` is not edited without a test; invariants live in `tests/test_rules_invariants.py`.

| Action | Who decides | Check |
|---|---|---|
| Create draft card, answer product questions, draft an order at the live card price, send the confirmation message | Assistant, on its own | price = live card price; qty ≤ stock |
| Price different from the card (discount); order above threshold (default 2,000,000 VND, set by seller); refund; address change after confirmation | Seller approves in the console | order → `NEEDS_SELLER`; customer only hears "waiting for the shop to confirm" |
| Collecting money; promising delivery outside policy; messaging a customer more than 24 h after their last message | Forbidden | no code path; tests block |
| Hiding a comment that contains a phone number or an unknown link | Assistant, on its own, logged | seller unhides in one tap |
