# -*- coding: utf-8 -*-
import base64
import io
import logging
import re
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MAX_ROWS = 10000


class PriceMatrixImport(models.TransientModel):
    """Excel -> (Pricelist x Category x Discount %) import wizard.

    Flow: upload file -> Analyze (build a preview, nothing is written) ->
    review consistent / inconsistent categories -> Apply.
    The final write goes through the same batched ``price.matrix.board``
    upsert so ownership, constraints and audit stay in one place.
    """

    _name = 'price.matrix.import'
    _description = 'Price Matrix Excel Import'

    file = fields.Binary(
        string="Excel File", required=True, attachment=False)
    file_name = fields.Char(string="File Name")
    rounding_tolerance = fields.Float(
        string="Rounding Tolerance %",
        default=0.01,
        digits=(6, 4),
        help="Two discounts are considered identical when they differ by "
             "less than this tolerance. Values are also normalized to the "
             "shortest decimal representation within the tolerance "
             "(e.g. 14.999999 -> 15).",
    )
    state = fields.Selection(
        selection=[
            ('draft', "Upload"),
            ('analyzed', "Preview"),
            ('done', "Applied"),
        ],
        default='draft',
    )
    line_ids = fields.One2many(
        comodel_name='price.matrix.import.line',
        inverse_name='wizard_id',
        string="Category Analysis",
    )
    rows_total = fields.Integer(readonly=True)
    products_matched = fields.Integer(readonly=True)
    unmatched_rows = fields.Integer(
        readonly=True,
        help="Rows whose product could not be matched by Barcode nor by "
             "Internal Reference. Name-only matching is deliberately not "
             "performed because product names may repeat.",
    )
    missing_categories = fields.Integer(
        readonly=True,
        help="Rows whose category does not exist in the database. "
             "Categories are never invented by the import.",
    )
    invalid_prices = fields.Integer(readonly=True)
    snapped_count = fields.Integer(
        readonly=True,
        help="Discounts normalized to a rounder value within tolerance "
             "(e.g. 14.999999 -> 15).",
    )
    column_report = fields.Text(readonly=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _to_float(value):
        """Best-effort float conversion ("1,234.50" -> 1234.5)."""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(',', '')
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _classify(title):
        text = title.strip().lower()
        if 'barcode' in text or 'باركود' in text:
            return 'barcode'
        if any(key in text for key in (
                'default_code', 'internal reference', 'reference', 'sku',
                'كود', 'مرجع')):
            return 'code'
        if any(key in text for key in (
                'categ', 'category', 'فئة', 'تصنيف')):
            return 'category'
        if any(key in text for key in ('base', 'list', 'أساسي', 'الأساس')):
            return 'base'
        return 'pricelist'

    def _load_rows(self):
        try:
            import openpyxl
        except ImportError as exc:
            raise UserError(_(
                "The Python library \"openpyxl\" is required to read Excel "
                "files but is not available on this server.")) from exc
        try:
            workbook = openpyxl.load_workbook(
                io.BytesIO(base64.b64decode(self.file)),
                read_only=True, data_only=True,
            )
        except Exception as exc:
            raise UserError(_(
                "Could not read the Excel file: %s", exc)) from exc
        worksheet = workbook.active
        rows = []
        for row in worksheet.iter_rows(values_only=True):
            if any(cell is not None and str(cell).strip()
                   for cell in row):
                rows.append(list(row))
            if len(rows) > MAX_ROWS:
                raise UserError(_(
                    "The file has more than %s data rows. Please split it.",
                    MAX_ROWS))
        workbook.close()
        return rows

    def _resolve_category(self, raw_name, by_path, by_name):
        """Resolve a category by full path, then unique name, then unique
        leaf name. Ambiguous names resolve to None (never guessed)."""
        if raw_name is None:
            return None
        label = str(raw_name).strip()
        if not label:
            return None
        parts = [part.strip() for part in re.split(r'[/>\t]', label)
                 if part.strip()]
        if not parts:
            return None
        full = ' / '.join(parts).lower()
        if full in by_path:
            return by_path[full]
        if full in by_name:
            return by_name[full]
        return by_name.get(parts[-1].lower())

    @staticmethod
    def _normalize(discount, tolerance):
        """Snap a discount to the shortest decimal form within tolerance."""
        for decimals in (0, 1, 2, 3):
            candidate = round(discount, decimals)
            if abs(discount - candidate) <= tolerance:
                return candidate
        return round(discount, 4)

    # ------------------------------------------------------------------
    # Step 2 — Analyze
    # ------------------------------------------------------------------
    def action_analyze(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_("This file was already analyzed."))
        rows = self._load_rows()
        if len(rows) < 2:
            raise UserError(_("The file must contain a header row and data."))

        header = [str(cell).strip() if cell is not None else ''
                  for cell in rows[0]]
        roles = [self._classify(title) if title else 'skip'
                 for title in header]
        if 'category' not in roles:
            raise UserError(_(
                "No \"Product Category\" column found in the header row."))
        if 'pricelist' not in roles:
            raise UserError(_(
                "No pricelist price columns found in the header row. "
                "Every column that is not barcode/code/category/base is "
                "treated as a pricelist column."))

        Pricelist = self.env['product.pricelist'].sudo()
        companies = self.env.companies
        pl_by_name = {
            pl.name.strip().lower(): pl
            for pl in Pricelist.search(
                [('company_id', 'in', companies.ids + [False])])
        }

        columns = []  # (index, role, pricelist)
        matched, skipped = [], []
        for index, (title, role) in enumerate(zip(header, roles)):
            if role == 'pricelist':
                pricelist = pl_by_name.get(title.lower())
                if pricelist:
                    columns.append((index, role, pricelist))
                    matched.append(title)
                else:
                    skipped.append(title)
            elif role != 'skip':
                columns.append((index, role, None))

        Product = self.env['product.product'].sudo().with_context(
            active_test=True)
        barcode_map = defaultdict(list)
        code_map = defaultdict(list)
        for product in Product.search_read(
                domain=['|', ('barcode', '!=', False),
                        ('default_code', '!=', False)],
                fields=['barcode', 'default_code']):
            if product['barcode']:
                barcode_map[str(product['barcode']).strip()].append(
                    product['id'])
            if product['default_code']:
                code_map[str(product['default_code']).strip()].append(
                    product['id'])

        Category = self.env['product.category'].sudo()
        by_path, by_name = {}, defaultdict(list)
        for categ in Category.search([]):
            by_path[categ.complete_name.lower()] = categ
            by_name[categ.name.strip().lower()].append(categ)
        by_name_unique = {
            name: cats[0] for name, cats in by_name.items() if len(cats) == 1
        }

        samples = defaultdict(list)
        counters = defaultdict(int)
        matched_products = set()

        for row in rows[1:]:
            counters['rows_total'] += 1
            get = lambda role: next(  # noqa: E731
                (row[idx] for idx, r, _pl in columns if r == role), None)
            categ = self._resolve_category(
                get('category'), by_path, by_name_unique)
            if not categ:
                counters['missing_categories'] += 1
                continue
            product = None
            barcode = get('barcode')
            if barcode is not None and str(barcode).strip():
                ids = barcode_map.get(str(barcode).strip(), [])
                if len(ids) == 1:
                    product = Product.browse(ids[0])
                elif len(ids) > 1:
                    counters['ambiguous_products'] += 1
            if product is None:
                code = get('code')
                if code is not None and str(code).strip():
                    ids = code_map.get(str(code).strip(), [])
                    if len(ids) == 1:
                        product = Product.browse(ids[0])
                    elif len(ids) > 1:
                        counters['ambiguous_products'] += 1
            if product is None:
                counters['unmatched_rows'] += 1
                continue
            matched_products.add(product.id)

            base = self._to_float(get('base'))
            if base is None or base <= 0:
                base = product.list_price
            if not base or base <= 0:
                counters['invalid_prices'] += 1
                continue

            for idx, role, pricelist in columns:
                if role != 'pricelist':
                    continue
                price = self._to_float(row[idx]) if idx < len(row) else None
                if price is None or price <= 0:
                    counters['invalid_prices'] += 1
                    continue
                discount = (base - price) / base * 100.0
                if discount < -0.01:
                    counters['invalid_prices'] += 1
                    continue
                discount = max(discount, 0.0)
                samples[(categ.id, pricelist.id)].append(discount)

        line_vals = []
        for (categ_id, pl_id), values in samples.items():
            low, high = min(values), max(values)
            is_consistent = (high - low) <= self.rounding_tolerance
            average = sum(values) / len(values)
            suggested = self._normalize(average, self.rounding_tolerance)
            if not is_consistent:
                suggested = round(average, 4)
            elif abs(suggested - average) > 1e-9:
                counters['snapped_count'] += 1
            suggested = min(max(suggested, 0.0), 100.0)
            line_vals.append({
                'wizard_id': self.id,
                'categ_id': categ_id,
                'pricelist_id': pl_id,
                'sample_count': len(values),
                'min_discount': round(low, 4),
                'max_discount': round(high, 4),
                'is_consistent': is_consistent,
                'suggested_discount': suggested,
                'chosen_discount': 0.0,
                'action': 'apply' if is_consistent else 'skip',
            })

        self.line_ids.unlink()
        if line_vals:
            self.env['price.matrix.import.line'].create(line_vals)

        report_lines = [
            _("Matched pricelist columns: %s",
              ", ".join(matched) or _("none")),
            _("Skipped columns (no matching pricelist): %s",
              ", ".join(skipped) or _("none")),
        ]
        if counters.get('ambiguous_products'):
            report_lines.append(_(
                "Ambiguous barcode/reference matches skipped: %s",
                counters['ambiguous_products']))
        self.write({
            'state': 'analyzed',
            'rows_total': counters['rows_total'],
            'products_matched': len(matched_products),
            'unmatched_rows': counters['unmatched_rows'],
            'missing_categories': counters['missing_categories'],
            'invalid_prices': counters['invalid_prices'],
            'snapped_count': counters['snapped_count'],
            'column_report': "\n".join(report_lines),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # ------------------------------------------------------------------
    # Step 4 — Apply
    # ------------------------------------------------------------------
    def action_apply(self):
        self.ensure_one()
        self.env['price.matrix.board']._ensure_manager()
        if self.state != 'analyzed':
            raise UserError(_("Analyze the file before applying it."))
        lines = self.line_ids.filtered(
            lambda line: line.action == 'apply'
            and line.categ_id and line.pricelist_id)
        if not lines:
            raise UserError(
                _("No category is selected for import."))
        cells = [
            {
                'pricelist_id': line.pricelist_id.id,
                'categ_id': line.categ_id.id,
                'discount': (line.chosen_discount
                             or line.suggested_discount),
            }
            for line in lines
        ]
        result = self.env['price.matrix.board'].write_cells(
            cells, force=True, source='import')
        self.write({'state': 'done'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _(
                    "Import applied — %(created)s created, %(updated)s "
                    "updated, %(deleted)s removed.",
                    created=result['created'],
                    updated=result['updated'],
                    deleted=result['deleted']),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class PriceMatrixImportLine(models.TransientModel):
    _name = 'price.matrix.import.line'
    _description = 'Price Matrix Import Analysis Line'
    _order = 'is_consistent, pricelist_id, categ_id'

    wizard_id = fields.Many2one(
        comodel_name='price.matrix.import', required=True,
        ondelete='cascade')
    categ_id = fields.Many2one(
        comodel_name='product.category', string="Product Category",
        required=True, readonly=True)
    pricelist_id = fields.Many2one(
        comodel_name='product.pricelist', string="Pricelist",
        required=True, readonly=True)
    sample_count = fields.Integer(string="Products", readonly=True)
    min_discount = fields.Float(string="Min %", digits=(6, 4), readonly=True)
    max_discount = fields.Float(string="Max %", digits=(6, 4), readonly=True)
    is_consistent = fields.Boolean(string="Consistent", readonly=True)
    suggested_discount = fields.Float(
        string="Suggested %", digits=(6, 4), readonly=True)
    chosen_discount = fields.Float(
        string="Chosen %", digits=(6, 4),
        help="Overrides the suggested discount when applying this line "
             "(a value of 0 keeps the suggested one).")
    action = fields.Selection(
        selection=[('apply', "Apply"), ('skip', "Skip")],
        default='apply', required=True,
        help="Inconsistent categories default to \"Skip\": review them and "
             "either choose a discount or leave them out.")
