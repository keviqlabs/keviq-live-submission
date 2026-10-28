# Written phone numbers: bounded normalization

Test-only acceptance proposal. Baseline: main `650fe39`. The existing `scan(text) -> list[str]` interface stays.
No shield, worker, model prompt, event field or customer-data source is changed in this draft.

Recognize each of the ten Vietnamese digit words (`không một hai ba bốn năm sáu bảy tám chín`) as one digit,
alongside ASCII digits or digit chunks. Normalize Unicode to NFC and compare words case-insensitively. Only
whole digit-word tokens count; do not split arbitrary words or identifiers. Preserve the original text and
return only the existing `phone_number` signal. There is no model call or new permission involved.

Candidate separators are whitespace, dots, commas and hyphens. They may occur between words, between digits,
or between a digit chunk and a word. An optional literal `+` may precede an international prefix. Other words,
letters, magnitude words (`trăm`, `nghìn`, `triệu`) and unit words (`đôi`, `cái`, `size`) end the candidate;
never concatenate digit fragments across them. A phone must not be extracted from inside an alphanumeric SKU
or colour identifier such as `C0900000013X`.

Validate the **entire maximal candidate**, not a convenient substring: the current supported shapes are
local `0` followed by nine digits, or `84`/`+84` followed by nine digits. This is structural detection, not a
claim that a number is assigned or that all international numbering plans are supported. Retain only enough
state for eleven digits plus an overflow flag; an overlong run remains invalid until its next boundary.
Do not truncate an overlong run and rescan its suffix as a valid phone. Process the input in one linear pass
with bounded candidate storage; do not truncate the whole comment and silently miss its end.

Acceptance comes from all 500 committed C5 rows and their `has_phone` gold flags (20 positives, 480 negatives).
Five written-number positives are expanded with spaces/dots, mixed digit chunks and words, case changes, NFD
Unicode and an international-prefix form. Extra negatives cover prices, sizes, quantities, colour identifiers,
short/overlong candidates and fragments interrupted by ordinary words. Every number is synthetic and derived
from the existing C5 fake-number family. No private gold is loaded and no gold enters a runtime.

When the normalizer lands, run the complete existing shield/label suite as well: digit phones and links must
retain their behavior. This draft intentionally supplies red acceptance tests; it does not implement normalization.
