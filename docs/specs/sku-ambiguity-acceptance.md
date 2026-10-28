# SKU ambiguity: acceptance and minimum distinguishing evidence

Test/spec-only draft on main `650fe39`, kept as a foundation for the identity implementation; do not merge
independently. The original 19 controls pass: several matching catalog items return `None`, and an ambiguous
new observation opens an unknown draft instead of reusing a known SKU. A newly added recorded-input case is
red: a changed observation can still return one coarse candidate and reuse a priced card without evidence
of physical continuity. See the [one-page design](uncertain-item-identity.md) and
[recorded identifiers](../evidence/2026-10-28/sku-identity-collision.json).

The committed C5 catalog has **no pair with the same exact `kind` and colour**. It has two pairs at the coarse
category/colour resolution actually accepted by the matcher:

| Observed category | Colour | Candidate SKUs | Distinction missing from the input |
| --- | --- | --- | --- |
| `other` | `đen` | SP07 controller / SP09 keyboard | Controller versus keyboard |
| `other` | `trắng` | SP08 controller / SP10 keyboard | Controller versus keyboard |

Expected behavior: return `None` independent of catalog order. Missing colour also abstains when several items
remain. On an explicitly new `product_seen`, keep the prior seller-priced card unchanged and open a different
draft with null SKU/price and no borrowed variants or stock. The handler test bootstraps the prior known card
with a temporary one-item fixture, approves its price through the real app route, then restores the complete
catalog **before** the ambiguous observation. This fixture setup does not claim a successful vision match.
A separate synthetic same-kind/colour duplicate checks the general invariant without changing the C5 catalog.

For these two pairs, the minimum additional distinguishing evidence is an observation that separates controller
from keyboard, tied to the same frame/appearance, or the seller explicitly choosing one SKU. The catalog's `kind`
is descriptive metadata; it is not proof that the video showed that kind. Gold identity, catalog position, a
high confidence on the same coarse category, an old current card, and a price that happens to fit a SKU are not
independent distinguishing evidence. Preserve abstention when evidence is missing or contradictory.

No `observed_kind` field or seller-selection route is implemented here. Any new event/API field or authority
change needs the existing contracts-owner/product-owner process before implementation. No gold is supplied to
runtime handlers, and no catalog, prompt, worker, app, schema, measure or shield code is changed.

These regression tests cover matcher abstention and card separation only. They do not prove correct visual
recognition, correct media-time attachment, order fallback to the seller, or recovery of the missed C5
SKUs. Those claims require their own accepted implementation and measured evidence.
