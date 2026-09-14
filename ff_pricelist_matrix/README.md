# Price Matrix — Category Discounts Manager (`ff_pricelist_matrix`)

Manage **Pricelist × Product Category → Discount %** from one standard Odoo
editable list, on top of the **standard Odoo pricing engine**.

Every row in the matrix is a real, standard `product.pricelist.item` rule
(*Apply on: Product Category, Compute Price: Discount, Based on: Sales Price*).
Sale Orders, POS, Website and every other consumer of the standard engine keep
working untouched — there is **no parallel pricing engine** and **no custom
front-end**: 100% standard Odoo views.

> 🇪🇬 **الدليل الكامل بالعربية أدناه** — [دليل الاستخدام بالعربية](#دليل-الاستخدام-بالعربية)

## The board

`Sales ▸ Configuration ▸ Price Category Matrix`

* A **100% standard Odoo editable list** of the matrix-managed rules — no
  custom JS or CSS: native search, grouping, dark mode and RTL.
* One row per (Pricelist x Product Category); edit the discount inline.
* New rows are preset for you (category / percentage discount / sales price) —
  pick the pricelist, the category and the %.
* `Import Excel` runs the analysis wizard described below.

## Ownership & safety

The module only ever touches rules it created itself (flag
`managed_by_matrix`). Manual rules are **never modified or deleted**:

* deleting a managed row removes **its own** rule
* creating a row where a manual rule already targets the same
  (pricelist, category) is **blocked** with a clear message
* one managed rule per (pricelist, category) — guaranteed at DB level
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

`Import Excel` on the matrix (Price Matrix Administrator):

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
6. **Apply** writes through the same audited, constraint-checked path

## Audit

`Sales ▸ Configuration ▸ Matrix Change History` — old/new discount,
pricelist, category, user, date and source for every managed change.

## Security

| Group | Rights |
|---|---|
| Price Matrix User | open the matrix (read-only) |
| Price Matrix Administrator | edit rows, Excel import, product exceptions, history |

Multi-company: only pricelists of your active companies are visible and
editable.

## Requirements / notes

* Odoo 19 Community — depends on `sale_management` only.
* On install, the *Pricelists* feature (Odoo 19 setting
  `product.group_product_pricelist`) is enabled for existing internal users —
  without it Odoo ignores pricelists on orders.
* `openpyxl` is required on the server for Excel import.
* Full Arabic translation (`i18n/ar.po`).

## Test

```bash
odoo -d <database> -u ff_pricelist_matrix --test-enable \
  --test-tags /ff_pricelist_matrix --stop-after-init
```

---

## دليل الاستخدام بالعربية

**مصفوفة أسعار الفئات** — إدارة **قائمة الأسعار × فئة المنتج ← نسبة الخصم %**
من قائمة أودو قياسية واحدة، فوق **محرك التسعير القياسي** — بدون أي محرك موازٍ
وبدون أي واجهة مخصوصة: وجهات أودو القياسية 100%.

### الشاشة

`المبيعات ▸ الإعدادات ▸ مصفوفة أسعار الفئات`

* قائمة أودو قياسية 100% قابلة للتعديل المباشر — كل صف = (قائمة أسعار × فئة
  منتج) والنسبة تُدخل في مكانها.
* زر «جديد» يجهّز الصف تلقائيًا (خصم فئة / نسبة مئوية / سعر البيع) — تختار
  القائمة والفئة والنسبة فقط.
* زر «استيراد Excel» يشغّل معالج الاستيراد بالتحليل والمعاينة.

### الملكية والأمان

الموديول يلمس **فقط** القواعد التي أنشأها بنفسه (علامة `managed_by_matrix`) —
القواعد اليدوية لا تُعدل ولا تُحذف أبدًا:

* حذف صف مُدار يحذف قاعدته هو فقط
* إنشاء صف يتعارض مع قاعدة يدوية على نفس (القائمة، الفئة) **يُمنع** برسالة
  واضحة
* قاعدة واحدة مُدارة لكل (قائمة، فئة) — مضمونة على مستوى قاعدة البيانات
* إلغاء التثبيت آمن: القواعد تبقى كقواعد نسبة مئوية قياسية تعمل طبيعيًا

### لماذا السلوك حتمي (Deterministic)؟

أودو يختار أول قاعدة تنطبق حسب الترتيب القياسي: متغير ← منتج ← **فئة** ← عام،
ثم الكسر الكمي (أعلى حد أدنى أولًا)، ثم **الفئة الأعمق تفوز دائمًا** (معرف
الفئة الابن أكبر حتمًا من الأب)، والأحدث أخيرًا — ولهذا يُمنع التكرار داخل
النطاق المُدار حتى لا يعتمد شيء على تاريخ الإنشاء.

### استيراد Excel

1. ارفع الملف (.xlsx)
2. **تحليل**: اكتشاف تلقائي للأعمدة (باركود / مرجع داخلي / فئة / سعر أساسي —
   وكل عمود آخر يُعامل كقائمة أسعار تُطابق بالاسم)
3. المطابقة **بالباركود أولًا ثم المرجع الداخلي** — لا مطابقة بالاسم أبدًا
4. الخصومات تُستخرج لكل فئة: (الأساس − السعر) ÷ الأساس، مع تطبيع فروق
   التقريب (14.999999 ← 15)
5. **معاينة**: عدد العينات والحد الأدنى/الأقصى وحالة الاتساق لكل (فئة × قائمة)
   — الفئات غير المتسقة تُضبط على «تخطي» ولا تُخمَّن أبدًا
6. **تطبيق** يكتب بنفس المسار المُدقق والمسجّل

### التدقيق والصلاحيات

* **سجل تغييرات المصفوفة**: الخصم القديم/الجديد، القائمة، الفئة، المستخدم،
  التاريخ، والمصدر.
* مجموعتان: **مستخدم مصفوفة الأسعار** (عرض فقط) و**مدير مصفوفة الأسعار**
  (تعديل + استيراد + سجل + استثناءات أسعار المنتجات).
* تعدد الشركات: تظهر وتُعدَّل قوائم شركاتك النشطة فقط.

### المتطلبات

* أودو 19 Community — يعتمد على `sale_management` فقط.
* عند التثبيت تُفعَّل ميزة «قوائم الأسعار» تلقائيًا للمستخدمين الداخليين
  الحاليين (بدونها يتجاهل أودو قوائم الأسعار في الأوامر).
* `openpyxl` مطلوبة على السيرفر لقراءة ملفات Excel.
