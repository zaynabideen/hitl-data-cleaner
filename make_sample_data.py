"""Generate a synthetic messy e-commerce orders CSV for testing.

100% fabricated data. No real customers, no personal data.
Deliberately injects the 4 issue types the v1 cleaner handles.
"""

import csv
import random
from datetime import date, timedelta

random.seed(42)

FIRST = ["Ayesha", "Bilal", "Hina", "Omar", "Sana", "Tariq", "Zara", "Imran",
         "Nadia", "Faisal", "Mariam", "Usman", "Laila", "Kamran", "Rabia"]
LAST = ["Khan", "Ahmed", "Malik", "Sheikh", "Butt", "Qureshi", "Farooq",
        "Chaudhry", "Siddiqui", "Raza"]
PRODUCTS = ["Wireless Mouse", "USB-C Hub", "Laptop Stand", "Mechanical Keyboard",
            "Webcam 1080p", "Desk Lamp", "Monitor Arm", "Cable Organiser"]
COUNTRIES = ["United Kingdom", "uk", "UK ", "united kingdom", "  United Kingdom",
             "Pakistan", "pakistan", "PAKISTAN", "Ireland", "ireland"]
STATUSES = ["shipped", "Shipped", "SHIPPED", "pending", "Pending",
            "cancelled", "Cancelled", "delivered", "Delivered"]

START = date(2025, 1, 6)


def iso(d):
    return d.strftime("%Y-%m-%d")


def uk(d):
    return d.strftime("%d/%m/%Y")


def us(d):
    return d.strftime("%m/%d/%Y")


def build_rows(n=180):
    rows = []
    for i in range(n):
        d = START + timedelta(days=random.randint(0, 200))
        name = f"{random.choice(FIRST)} {random.choice(LAST)}"

        # ISSUE 1: mixed date formats.
        # Majority ISO, a minority in UK and US style.
        r = random.random()
        if r < 0.76:
            order_date = iso(d)
        elif r < 0.90:
            order_date = uk(d)
        else:
            order_date = us(d)

        # ISSUE 2: inconsistent casing / stray whitespace in text columns.
        country = random.choice(COUNTRIES)
        status = random.choice(STATUSES)
        if random.random() < 0.12:
            name = f"  {name} "
        if random.random() < 0.08:
            name = name.upper()

        rows.append({
            "order_id": f"ORD-{1000 + i}",
            "order_date": order_date,
            "customer_name": name,
            "email": f"{name.strip().lower().replace(' ', '.')}@example.invalid",
            "country": country,
            "product": random.choice(PRODUCTS),
            "quantity": random.randint(1, 5),
            "unit_price": round(random.uniform(6.5, 189.0), 2),
            "status": status,
        })

    # ISSUE 3: missing values scattered across a few columns.
    for col, count in [("country", 11), ("email", 7), ("quantity", 5),
                       ("unit_price", 4), ("status", 6)]:
        for idx in random.sample(range(len(rows)), count):
            rows[idx][col] = random.choice(["", "  ", "N/A", "null", "-"])

    # ISSUE 4: duplicate rows.
    # 9 exact duplicates and 5 near-duplicates (same order_id, differing case).
    for idx in random.sample(range(len(rows)), 9):
        rows.append(dict(rows[idx]))
    for idx in random.sample(range(len(rows) - 9), 5):
        near = dict(rows[idx])
        near["customer_name"] = near["customer_name"].upper()
        near["status"] = str(near["status"]).title()
        rows.append(near)

    random.shuffle(rows)
    return rows


FIELDS = ["order_id", "order_date", "customer_name", "email", "country",
          "product", "quantity", "unit_price", "status"]


def build_month2(n=150):
    """Next month's file - same shape, but drifted.

    Used to prove the replay engine stops instead of guessing:
      * a NEW column (`discount_code`) nothing has ever been approved for
      * a NEW issue in `product`, which was clean last month
      * the old date/duplicate/missing issues still present, so the recipe
        has something legitimate to auto-apply
    """
    global START
    START = date(2025, 8, 4)
    rows = build_rows(n)

    for r in rows:
        # new column, never seen by the recipe
        r["discount_code"] = random.choice(
            ["", "SAVE10", "save10", " SAVE10", "WELCOME", "welcome", ""])
        # new issue: product names now arrive with case/whitespace variants
        if random.random() < 0.3:
            r["product"] = random.choice([
                r["product"].upper(), r["product"].lower(),
                f"  {r['product']}", f"{r['product']} "])
    return rows


def write(rows, path, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path}: {len(rows)} rows")


def main(path="sample_orders_messy.csv"):
    write(build_rows(), path, FIELDS)


def main_month2(path="sample_orders_month2.csv"):
    write(build_month2(), path, FIELDS + ["discount_code"])


if __name__ == "__main__":
    import sys
    if "--month2" in sys.argv:
        main_month2()
    else:
        main()
        main_month2()
