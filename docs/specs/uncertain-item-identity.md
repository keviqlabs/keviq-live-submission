# Uncertain item identity: candidate matching is not continuity

Design only; test-only draft, not independently mergeable. Runtime baseline: `650fe39`. No runtime, catalog,
prompt, model, schema or price authority changes are included.

**Observed failure.** In `diag-test1x1-1028-1531`, the observation at frame 58 s is `sneaker / trắng` and opens
SP02. At frame 96 s, within the evaluation interval for SP03, the observation is `sandal / trắng`.
Both categories belong to SP02's category list, and its variants are white. SP03 has the same category list
but grey variants. Thus `match()` returns SP02, and `on_product_seen`'s `same` branch reuses the priced SP02
card, changing v4 to v5. The event does not contain a recognised SP03 SKU. The evaluation interval supplies
that name only when assessing the recording. Exact identifiers are in `sku-identity-collision.json`.

**Required distinction.** Keep catalog candidate lookup separate from deciding whether an observation shows
the same physical item. A unique category/colour lookup is not enough evidence to reuse a card: its input
can be wrong, or two items can look alike. High category confidence and a coincidentally matching price do
not establish identity. Do not repair this case by adding white to SP03, reordering catalog entries, or
feeding gold identity into the worker.

**Minimum conservative behavior.** Before the `same` branch, evaluate continuity using the prior observation
and the new source frame. Here an explicit new observation changes category while its coarse lookup would
reuse a priced card. Without stronger identity evidence, keep the previous card and every version unchanged;
close its active appearance interval and open a separate draft at the new frame time. The draft has null SKU,
price and image, no inherited variants, and zero known stock. Preserve the observation's category and colour
as evidence, not an approved identity. The new draft becomes current; it must not silently select SP02 or
SP03. The seller resolves it before SKU-dependent stock, price confirmation or a customer receipt is used.

**Seller choice.** Show the new observation and the earlier card distinctly. Any SKU-choice event or route,
candidate field and the authority to bind that draft require the contracts-owner/product-owner process.
The current schema does not already provide that choice. Keep versions append-only; never copy an old
approved price into an uncertain new draft or rewrite an already pinned order. Selecting an existing SKU
must preserve the distinct appearance and the seller's decision provenance.

**Tradeoff and boundaries.** A category-only guard can split one real product when vision changes its label.
That is a conservative ambiguity, not proof of a new product. Measure duplicate drafts and seller correction
count/time as well as false merges. Conversely, a same-category/same-colour replacement may emit no event at
all; this guard cannot detect it. Stronger continuity may later use an item-specific visual observation or
explicit seller choice. Prompt, model or resolution changes require a separately authorised measurement.
Media-time ordering and bounded waits remain separate work: correct timing cannot recover an identity that
has already been merged, and identity separation alone does not fix late prices or comment attachment.

**Acceptance.** The new recorded-input test must change from red to green: a new unknown draft, unchanged
priced SP02 v1–v4, no model calls and no customer effects. Preserve the 19 existing matcher/card controls.
Also cover category jitter, genuine return to an earlier item, duplicate/stale events, and a later seller
choice in the implementing PR. Re-score the same saved observations before any paid run; report all 28
references, wrong-item receipts, abstentions, duplicate drafts and seller effort without changing gold.
