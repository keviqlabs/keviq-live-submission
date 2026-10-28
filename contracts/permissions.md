# Permission table (v1) — code: `orders/rules.py`, machine-readable: `permissions.json`

No prompt can change this table. `orders/rules.py` is not edited without a test; invariants live in `tests/test_rules_invariants.py`.

| Action | Who decides | Check |
|---|---|---|
| Create draft card, answer product questions, draft an order at the attached card's approved price, send the confirmation message | Assistant, on its own | price = attached card's approved live/edited price pinned when drafted; qty ≤ stock |
| Price different from the card (discount); order above threshold (default 2,000,000 VND, set by seller); refund; address change after confirmation | Seller approves in the console | order → `NEEDS_SELLER`; customer only hears "waiting for the shop to confirm" |
| Collecting money; promising delivery outside policy; messaging a customer more than 24 h after their last message | Forbidden | no code path; tests block |
| Hiding a comment that contains a phone number or an unknown link | Assistant, on its own, logged | seller unhides in one tap |

The seller's item choice and unresolved-draft SKU assignment are console edits, like the existing seller price
route; they add no assistant permission or permission-table action. See `README.md` for their request contracts
and runtime checks. Keeping a draft unresolved does not authorize a price, an order or a message. Customer
confirmation is still required before an order is confirmed; item choice alone does not confirm it.
