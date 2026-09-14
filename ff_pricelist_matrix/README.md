# Price Matrix — Category Discounts Manager (`ff_pricelist_matrix`)

Manage **Pricelist × Product Category → Discount %** from one spreadsheet-like
screen, on top of the **standard Odoo pricing engine**.

Every cell in the matrix is a real, standard `product.pricelist.item` rule
(*Apply on: Product Category, Compute Price: Discount, Based on: Sales Price*).
Sale Orders, POS, Website and every other consumer of the standard engine keep
working untouched — there is **no parallel pricing engine**.

## The board

`Sales ▸ Configuration ▸ Price Category Matrix`

A **100% standard Odoo editable list** of the matrix-managed rules — no custom
JS or CSS, so it inherits the native look, dark mode, RTL, search and grouping:

* one row per (Pricelist x Product Category), edit the discount inline
* create a row with the standard **New** button (defaults are preset:
  category discount / percentage / sales price)
* search by pricelist or category, group by either, "Active Pricelists" filter
* `Import Excel` runs the analysis wizard described below (upload, preview,
  apply)

Every row is a real standard `product.pricelist.item` rule (*Apply on:
Product Category, Compute Price: Discount, Based on: Sales Price*), so Sale
Orders, POS, Website and every other consumer of the standard engine keep
working untouched — there is **no parallel pricing engine**.

## Ownership & safety

The module only ever touches rules it created itself (flag
`managed_by_matrix`). Manual rules are **never modified or deleted**:

* clearing a managed cell removes **its own** rule
* editing a cell that a manual rule already targets is **blocked** unless you
  force it (with two same-target rules the most recent one wins — standard
  `id desc` tie-break)
* one managed rule per (pricelist, category); quantity breaks / date-windowed
  manual rules remain fully supported by the standard engine
* uninstall-safe: managed rules survive as ordinary percentage rules; the
  ownership flag column is simply dropped

## Why the behavior is deterministic

Standard Odoo picks the **first applicable rule** in the order
`applied_on, min_quantity desc, categ_id desc, id desc`:

1. variant rules → product rules → **category rules** → global rules
2. higher `min_quantity` first (quantity breaks win at their quantity)
3. deeper category first (a child category id is always greater than its
   parent's, so `categ_id desc` = most specific wins)
4. newest rule wins (this is why duplicates inside the managed scope are
   forbidden)

## Excel import

`Import Excel` on the board (Price Matrix Administrator):

1. Upload the workbook (`.xlsx`)
2. **Analyze** — columns are auto-detected: Barcode / Internal Reference /
   Product Category / Base (or List) Price; every other column is a pricelist
   column matched **by name** to an existing pricelist
3. Products are matched **by Barcode first, then Internal Reference** —
   never by name
4. Discounts are reverse-engineered per category: `(Base − Price) / Base`;
   values within the rounding tolerance are normalized (14.999999 → 15)
5. **Preview** shows per (category × pricelist): sample count, min/max,
   consistent? and a suggested %. Consistent categories are pre-selected;
   **inconsistent ones default to Skip** — never guessed
6. **Apply** writes through the same audited batched upsert

## Audit

`Sales ▸ Configuration ▸ Matrix Change History` — old/new discount,
pricelist, category, user, date, source (board / bulk / import).

## Security

| Group | Rights |
|---|---|
| Price Matrix User | open the board (read-only) |
| Price Matrix Administrator | edit cells, bulk fill/copy, Excel import, product exceptions, history |

The engine writes run as superuser after an explicit group check, so matrix
users get no blanket write access to `product.pricelist.item`. Multi-company:
only pricelists of your active companies are visible and editable.

## Requirements / notes

* Odoo 19 Community — depends on `sale_management` only.
* On install, the *Pricelists* feature (Odoo 19 setting
  `product.group_product_pricelist`) is enabled for existing internal users —
  without it Odoo ignores pricelists on orders. Toggling it off later in
  Settings remains possible.
* `openpyxl` is required on the server for Excel import (present in this
  environment: 3.1.2).

## Test

```bash
odoo -d <database> -u ff_pricelist_matrix --test-enable \
  --test-tags /ff_pricelist_matrix --stop-after-init
```
