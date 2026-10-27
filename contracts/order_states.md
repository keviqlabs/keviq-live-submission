# Order states (v1) — machine-readable copy: `order_states.json`

```
DRAFT ──► CONFIRM_SENT ──► CONFIRMED ──► CLOSED
  │            │               │
  └────────────┴───────────────┴──► NEEDS_SELLER (approval queue) ──► DRAFT | CONFIRM_SENT | CONFIRMED | CANCELLED
any non-terminal state ──► CANCELLED
```

- Terminal: `CLOSED`, `CANCELLED`.
- `CONFIRM_SENT → CONFIRMED` only with `actor = customer` (the customer replied yes). `NEEDS_SELLER → CONFIRMED` only when the customer had already confirmed before the order went to the seller (e.g. address change after confirmation).
- Only the seller moves an order out of `NEEDS_SELLER` (the customer may still cancel).
- While an order is in `NEEDS_SELLER` the customer only receives "waiting for the shop to confirm".
- Every transition emits an `order` event with `prev_state`, `state`, `actor` and a non-empty `reason`.
- `PAYMENT_PENDING → PAID` is **not** in P0. Static VietQR is only displayed on the receipt; the seller marks payment received by hand.
