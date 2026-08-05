"""The recruitment form, described as data rather than as templates.

Why a spec and not eleven hand-written HTML sections: this is the first form of
several. Describing a form as a list of cards and fields means the next one is
a new entry here, not a new set of templates, views and migrations — and it
means one renderer, one validator and one storage shape serve all of them. The
"form builder" phase later on becomes "load this same structure from the
database instead of from Python".

Answers are stored as JSON against the session, not as columns. Seventy-odd
sparse columns per candidate would be a migration every time a question
changes, and most of them empty on every row.

FIELD KINDS
    text, tel, email, number, date_fa, textarea, select, radio, checkbox, file
    rows  — a repeatable table (education, employment, courses)

LAYOUT FLAGS
Each field may carry ``col`` (how many of the card's twelve columns it takes,
default three — four fields to a line), ``newline`` (start a fresh line, so a
group of related questions is never split across two), ``wide`` (the whole
line) and ``heading`` (a sub-heading printed above it). They live here rather
than in the template because how a group of fields reads together is part of
what the group *is* — the three yes/no choices want a line of their own with
room to breathe, and a name wants its Latin spelling beside it.

Every label, option and question below is the wording from the supplied design,
unchanged.
"""

# Job families, and the exact job titles offered for each.
JOB_FAMILIES = [
    ("technical", "فنی، مهندسی و کنترل کیفیت (QC)"),
    ("commercial", "بازرگانی، فروش، بازاریابی و خرید"),
    ("finance", "مالی و اداری"),
    ("warehouse", "انبار و لجستیک"),
    ("it", "فناوری اطلاعات (IT)"),
    ("advisory", "مشاوره و مدیریت"),
    ("support", "پشتیبانی"),
]
JOB_FAMILY_LABELS = dict(JOB_FAMILIES)

JOB_TITLES = {
    "technical": ["کارشناس فنی‌مهندسی و کیوسی", "سرپرست فنی‌مهندسی و کیوسی"],
    "commercial": ["کارشناس فروش", "سرپرست فروش", "کارشناس بازاریابی",
                   "سرپرست بازاریابی", "کارشناس خرید خارجی"],
    "finance": ["مدیر مالی", "مشاور مالیاتی"],
    "warehouse": ["سرپرست لجستیک و انبار", "کارمند انبار"],
    "it": ["سرپرست توسعه‌ی انفورماتیک و IT"],
    "advisory": ["مشاور کسب و کار"],
    "support": ["مسئول دفتر", "سرپرست خدماتی"],
}

# Every job title in one flat list, in family order and de-duplicated.
#
# The "exact job title" dropdown is built from THIS, not from one family, and
# the browser then hides the titles that do not belong to the chosen family.
# Doing it the other way round — an empty dropdown that scripting fills in —
# is what left the field permanently blank before: the field validates against
# its own choices server-side, so a title the server never offered is rejected
# no matter what the user picked, and with scripting off there was nothing to
# pick at all.
ALL_JOB_TITLES = list(dict.fromkeys(
    title for _family, _label in JOB_FAMILIES for title in JOB_TITLES.get(_family, [])
))

# Interview rating rows, scored 1 (weak) to 5 (excellent) by the evaluator.
INTERVIEW_RATINGS = [
    ("comm", "مهارت ارتباط و بیان"),
    ("problem", "حل مسئله و تحلیل"),
    ("attitude", "نگرش، انگیزه و کار تیمی"),
    ("knowledge", "دانش تخصصیِ مرتبط با سمت"),
]


def _f(name, label, kind="text", **kw):
    f = {"name": name, "label": label, "kind": kind}
    f.update(kw)
    return f


# ---------------------------------------------------------------------------
# The cards
# ---------------------------------------------------------------------------
# ``general`` marks the cards an administrator fills in when adding a person
# directly (cards 1–5, 7 and 10 of the design). ``profile`` marks the cards
# whose answers are carried over to the person record when a candidate is
# hired. The design printed a badge saying so on each of those cards; the badge
# is deliberately not reproduced — where the data goes afterwards is a fact
# about the system, not an instruction to the person filling the form in.

CARD_REQUEST = {
    "key": "request", "index": 1, "general": True, "profile": False,
    "title": "سمت مورد تقاضا و نحوه‌ی آشنایی",
    "fields": [
        _f("req_field", "حوزه‌ی شغلی", "select", required=True, col=4,
           options=[c for c, _ in JOB_FAMILIES],
           option_labels=JOB_FAMILY_LABELS, controls="req_title"),
        _f("req_title", "عنوان دقیق شغلی", "select", required=True, col=4,
           options=ALL_JOB_TITLES, depends_on="req_field"),
        _f("req_referral", "نحوه‌ی آشنایی با فرصت شغلی", "select", col=4,
           options=["وب‌سایت شرکت", "شبکه‌های اجتماعی", "معرفی آشنایان",
                    "سایت‌های کاریابی", "سایر"]),
        _f("req_salary", "حقوق درخواستی (ریال)", "text", col=4, newline=True,
           placeholder="مثلاً ۳۵۰٬۰۰۰٬۰۰۰", money=True, dir="ltr"),
        _f("req_available_from", "تاریخ آمادگی برای شروع به کار", "date_fa", col=4),
        _f("req_worktype", "نوع همکاری مورد نظر", "radio", wide=True, newline=True,
           options=["تمام‌وقت", "پاره‌وقت", "پروژه‌ای", "مشاوره‌ای"]),
    ],
}

CARD_IDENTITY = {
    "key": "identity", "index": 2, "general": True, "profile": True,
    "title": "اطلاعات هویتی و تماس",
    # Line by line: the four names together, then the identity documents, then
    # where to find the person, then the three choices on a line of their own.
    # A two-option radio squeezed into a text-input-width column looked wrong,
    # and reading a form is mostly reading its lines.
    "fields": [
        _f("p_first_name", "نام", required=True),
        _f("p_last_name", "نام خانوادگی", required=True),
        # Not in the original design. The Latin spelling is what the sign-in
        # name is built from, and transliterating a Persian name automatically
        # gets it wrong often enough that it has to be typed by a human.
        _f("p_first_name_en", "نام (لاتین)", required=True, dir="ltr",
           placeholder="Ali", help="برای ساخت نام کاربری استفاده می‌شود."),
        _f("p_last_name_en", "نام خانوادگی (لاتین)", required=True, dir="ltr",
           placeholder="Bayati", help="برای ساخت نام کاربری استفاده می‌شود."),
        _f("p_father_name", "نام پدر", newline=True),
        _f("p_id_number", "شماره شناسنامه", numeric=True),
        _f("p_national_id", "کد ملی", required=True, numeric=True, placeholder="۱۰ رقم"),
        # Internal code lives on the English Record card after the person exists
        # (not on this Persian identity sheet).
        _f("p_birth_date", "تاریخ تولد", "date_fa", placeholder="۱۳۷۰.۰۵.۱۲"),
        _f("p_birth_place", "محل تولد", newline=True),
        _f("p_city", "شهر محل سکونت"),
        _f("p_mobile", "تلفن همراه", "tel", required=True, numeric=True,
           placeholder="۰۹__ ___ ____"),
        _f("p_phone", "تلفن ثابت", "tel", numeric=True),
        _f("p_email", "پست الکترونیک", "email", col=6, newline=True, dir="ltr",
           placeholder="name@example.com"),
        _f("p_address", "نشانی محل سکونت", col=6),
        _f("p_gender", "جنسیت", "radio", col=4, newline=True, options=["آقا", "خانم"]),
        _f("p_marital", "وضعیت تأهل", "radio", col=4, options=["مجرد", "متأهل"]),
        _f("p_military", "وضعیت خدمت سربازی (آقایان)", "select", col=4,
           options=["پایان خدمت", "معافیت دائم", "معافیت تحصیلی", "مشمول"]),
        # Both files are deliberately NOT on the administrator's screen — see
        # people.forms.PersonForm.SKIP_FIELDS. They stay described here because
        # the person uploads them for themselves later and the description of
        # what a person record holds should not have a hole in it.
        _f("p_photo", "عکس پرسنلی", "file", wide=True, accept="image/*"),
        # Moved here from the skills card in the original design. That card is
        # not shown when an administrator enters a person by hand, which left
        # no way to attach a CV to anyone who was not hired through the
        # questionnaire.
        _f("p_resume", "بارگذاری رزومه", "file", wide=True, accept=".pdf,.doc,.docx"),
    ],
}

# Shown only when marital status is «متأهل» (wired in people.js).
CARD_CHILDREN = {
    "key": "children", "index": 16, "general": True, "profile": True,
    "title": "فرزندان",
    "note": "فقط در صورت متأهل بودن نمایش داده می‌شود. ابتدا تعداد را وارد کنید، سپس مشخصات هر فرزند را اضافه کنید.",
    "kind": "rows", "add_label": "افزودن فرزند",
    "married_only": True,
    "columns": [
        _f("name", "نام و نام خانوادگی"),
        _f("gender", "جنسیت", "select", options=["پسر", "دختر"]),
        _f("birth_date", "تاریخ تولد", placeholder="۱۴۰۰.۰۱.۰۱"),
        _f("national_id", "کد ملی", numeric=True),
    ],
}

CARD_EDUCATION = {
    "key": "education", "index": 3, "general": True, "profile": True,
    "title": "سوابق تحصیلی", "kind": "rows", "add_label": "افزودن ردیف تحصیلی",
    "columns": [
        _f("level", "مقطع", "select",
           options=["دیپلم", "کاردانی", "کارشناسی", "کارشناسی ارشد", "دکتری"]),
        _f("field", "رشته‌ی تحصیلی"),
        _f("institute", "دانشگاه / مؤسسه"),
        _f("grad_year", "سال فراغت", numeric=True, placeholder="۱۳۹۸"),
        _f("gpa", "معدل", numeric=True, placeholder="۱۷٫۲"),
    ],
}

CARD_EMPLOYMENT = {
    "key": "employment", "index": 4, "general": True, "profile": True,
    "title": "سوابق شغلی", "kind": "rows", "add_label": "افزودن سابقه‌ی شغلی",
    "columns": [
        _f("company", "نام شرکت"),
        _f("title", "سمت"),
        _f("from", "از تاریخ", numeric=True, placeholder="۱۳۹۷"),
        _f("to", "تا تاریخ", numeric=True, placeholder="۱۴۰۰"),
        _f("leave_reason", "علت ترک کار"),
    ],
}

CARD_COURSES = {
    "key": "courses", "index": 5, "general": True, "profile": True,
    "title": "دوره‌ها و گواهی‌نامه‌های حرفه‌ای", "kind": "rows",
    "add_label": "افزودن دوره",
    "columns": [
        _f("title", "عنوان دوره"),
        _f("institute", "مؤسسه"),
        _f("date", "تاریخ", numeric=True, placeholder="۱۴۰۱"),
        _f("duration", "مدت", placeholder="ساعت"),
        _f("cert", "گواهی‌نامه", "select", options=["دارد", "ندارد"]),
    ],
}

CARD_GENERAL_TEST = {
    "key": "general_test", "index": 6, "general": False, "profile": False,
    "title": "آزمون مهارت‌های عمومی", "tag": "انگلیسی · Excel",
    "note": "به پرسش‌ها پاسخ دهید. نمره‌ای نمایش داده نمی‌شود.",
    "test": ["en", "xl"],
    "fields": [
        _f("sk_driving", "گواهی‌نامه‌ی رانندگی", "select", options=["دارم", "ندارم"]),
        _f("sk_travel", "امکان مأموریت و سفر کاری", "select",
           options=["بله", "خیر", "با محدودیت"]),
    ],
    "fields_title": "سایر (خوداظهاری)",
}

CARD_MOTIVATION = {
    "key": "motivation", "index": 7, "general": True, "profile": True,
    "title": "انگیزه، علایق و اهداف",
    "fields": [
        _f("mot_reason", "انگیزه‌ی اصلی شما از پیوستن به مجموعه چیست؟", "textarea", wide=True),
        _f("mot_interests", "علایق و توانمندی‌های اصلی خود را بنویسید.", "textarea", wide=True),
        _f("mot_priority1", "اولویت اول", col=4, newline=True),
        _f("mot_priority2", "اولویت دوم", col=4),
        _f("mot_priority3", "اولویت سوم", col=4),
    ],
}

CARD_BEHAVIOUR = {
    "key": "behaviour", "index": 8, "general": False, "profile": False,
    "title": "سبک کاری و رفتار حرفه‌ای", "tag": "تشریحی",
    "note": "این پرسش‌ها نمره نمی‌گیرند؛ پاسخ متنی است و در مصاحبه پیگیری می‌شود.",
    "fields": [
        _f("beh_pressure",
           "۱) موقعیتی که زیر فشار زمانی یا در شرایط دشوار مجبور به تصمیم‌گیری شدید. "
           "چه کردید و نتیجه چه شد؟", "textarea", wide=True),
        _f("beh_conflict",
           "۲) زمانی که با یک هم‌تیمی یا مدیر اختلاف‌نظر جدی داشتید؛ چگونه مدیریت کردید؟",
           "textarea", wide=True),
        _f("beh_role", "۳) پرسش تخصصیِ حوزه", "textarea", wide=True),
    ],
}

CARD_SPECIALIST_TEST = {
    "key": "specialist_test", "index": 9, "general": False, "profile": False,
    "title": "آزمون تخصصی", "tag": "وابسته به حوزه",
    "note": "پرسش‌های تخصصیِ حوزه‌ی انتخاب‌شده (سطح کارشناسی). نمره‌ای نمایش داده نمی‌شود.",
    "test": ["mod"],
    "fields": [
        _f("mod_years", "سابقه‌ی مرتبط با این حوزه (سال)", "number"),
        _f("mod_key_skill", "مهم‌ترین مهارت یا ابزار تخصصیِ شما"),
    ],
}

CARD_REFERENCES = {
    "key": "references", "index": 10, "general": True, "profile": True,
    "title": "مراجع، تماس ضروری و تأییدیه",
    "fields": [
        _f("ref1_name", "نام مرجع اول", col=4),
        _f("ref1_relation", "سمت / نسبت", col=4),
        _f("ref1_phone", "تلفن تماس", "tel", col=4, numeric=True),
        _f("ref2_name", "نام مرجع دوم", col=4, newline=True),
        _f("ref2_relation", "سمت / نسبت", col=4),
        _f("ref2_phone", "تلفن تماس", "tel", col=4, numeric=True),
        _f("emg_name", "نام و نام خانوادگی", col=4, newline=True,
           group="تماس ضروری", heading="تماس ضروری"),
        _f("emg_relation", "نسبت", col=4, group="تماس ضروری"),
        _f("emg_phone", "تلفن تماس", "tel", col=4, numeric=True, group="تماس ضروری"),
    ],
}

CARD_SUPERVISORY = {
    "key": "supervisory", "index": 11, "general": False, "profile": False,
    "title": "سوابق سرپرستی و مدیریتی",
    "fields": [
        _f("sup_headcount", "تعداد نفرات تحت سرپرستی (بیشترین)", "number"),
        _f("sup_years", "سابقه‌ی مدیریت/سرپرستی (سال)", "number"),
        _f("sup_decision", "نمونه‌ای از یک تصمیم مدیریتی دشوار", "textarea", wide=True),
    ],
}

# Added at the user's request; not part of the original design. Kept in its own
# card because bank details are the one thing on this form that people expect
# to be able to point at and check in isolation.
CARD_FINANCIAL = {
    "key": "financial", "index": 12, "general": True, "profile": True,
    "title": "اطلاعات مالی",
    "note": "برای واریز حقوق. فقط برای مدیران قابل مشاهده است.",
    "fields": [
        _f("fin_card_number", "شماره کارت", col=4, numeric=True, dir="ltr",
           placeholder="۱۶ رقم"),
        _f("fin_iban", "شماره شبا", col=4, dir="ltr",
           placeholder="IR________________________", help="۲۴ رقم پس از IR."),
        _f("fin_bank", "نام بانک", col=4),
        _f("fin_account_holder", "نام صاحب حساب", col=6, newline=True,
           help="اگر با نام خود شخص یکی نیست."),
    ],
}

ALL_CARDS = [
    CARD_REQUEST, CARD_IDENTITY, CARD_CHILDREN, CARD_EDUCATION, CARD_EMPLOYMENT,
    CARD_COURSES, CARD_GENERAL_TEST, CARD_MOTIVATION, CARD_BEHAVIOUR,
    CARD_SPECIALIST_TEST, CARD_REFERENCES, CARD_SUPERVISORY, CARD_FINANCIAL,
]

# What the administrator sees when adding a person by hand: the "general" cards
# in the design's own order, plus the financial card. No tests, no essay
# questions — those only make sense for someone applying for a job.
ADMIN_PERSON_CARDS = [c for c in ALL_CARDS if c.get("general")]

# Cards 1, 2, 16 (children), 3, 4, 5, 7, 10, and financial (12).
ADMIN_PERSON_CARD_INDEXES = [1, 2, 16, 3, 4, 5, 7, 10, 12]
if [c["index"] for c in ADMIN_PERSON_CARDS] != ADMIN_PERSON_CARD_INDEXES:  # pragma: no cover
    raise RuntimeError(
        "people.spec: ADMIN_PERSON_CARDS resolved to "
        f"{[c['index'] for c in ADMIN_PERSON_CARDS]}, expected "
        f"{ADMIN_PERSON_CARD_INDEXES}")

# What a candidate sees at the kiosk: everything except the financial card,
# which is asked for only once somebody is actually hired.
CANDIDATE_CARDS = [c for c in ALL_CARDS if c["key"] != "financial"]

# Cards whose answers are carried into the person record on hire.
PROFILE_CARDS = [c for c in ALL_CARDS if c.get("profile")]

CARDS_BY_KEY = {c["key"]: c for c in ALL_CARDS}


def iter_fields(cards):
    """Every simple field across ``cards`` (row tables excluded)."""
    for card in cards:
        for f in card.get("fields", []):
            yield card, f


def field_names(cards):
    return [f["name"] for _card, f in iter_fields(cards)]
