import csv
import io
import json
import re
import unicodedata
from pathlib import Path
from datetime import date, datetime
import requests

ROOT = Path(__file__).resolve().parent
TODAY = date.today()


def norm(s):
    """Mala slova + uklanjanje dijakritika."""
    s = str(s or "").lower().strip()
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def parse_number(value):
    """
    Čita:
    284.99 -> 284.99
    284,99 -> 284.99
    1.099,99 -> 1099.99
    1099 -> 1099
    """
    if value is None:
        return None

    s = str(value).strip().replace(" ", "")
    if not s:
        return None

    try:
        if "," in s and "." in s:
            # srpski zapis: 1.099,99
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")

        return float(s)
    except ValueError:
        return None


def fetch(src):
    """Pokušava glavni URL, zatim fallback."""
    for url in (src.get("url"), src.get("fallback")):
        if not url:
            continue

        try:
            print(f"Downloading {src['name']}: {url}")

            r = requests.get(
                url,
                timeout=60,
                headers={
                    "User-Agent": "Mozilla/5.0 PriceTracker/2.0",
                    "Accept": "text/csv,text/plain,*/*",
                },
            )

            r.raise_for_status()

            if len(r.content) < 100:
                raise ValueError("Downloaded file is suspiciously small.")

            return r.content

        except Exception as e:
            print(f"{src['name']} failed: {e}")

    return None


def parse_csv(content):
    text = content.decode("utf-8-sig", errors="replace")

    sample = text[:5000]

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        delimiter = dialect.delimiter
    except Exception:
        delimiter = ";"

    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


def get_field(row, wanted):
    """Pronalazi kolonu nezavisno od sitnih razlika u nazivu."""
    wanted = norm(wanted)

    for key, value in row.items():
        if norm(key) == wanted:
            return value

    return ""


def product_name(row):
    return get_field(row, "Naziv proizvoda")


def category_name(row):
    return get_field(row, "NAZIV KATEGORIJE")


def price_regular(row):
    return parse_number(get_field(row, "Redovna cena"))


def price_unit(row):
    return parse_number(get_field(row, "Cena po jedinici mere"))


def price_sale(row):
    return parse_number(get_field(row, "Snižena cena"))


def price_type(row):
    return norm(get_field(row, "VRSTA_CENOVNIKA"))


def parse_date(value):
    value = str(value or "").strip()

    for fmt in ("%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass

    return None


def sale_is_active(row):
    sale = price_sale(row)

    if sale is None:
        return False

    start = parse_date(get_field(row, "Datum početka sniženja"))
    end = parse_date(get_field(row, "Datum kraja sniženja"))

    if start and TODAY < start:
        return False

    if end and TODAY > end:
        return False

    return True


def is_current_price_list(row):
    """
    Ne koristimo MESECNI_PRESEK za današnje poređenje.
    """
    t = price_type(row)

    if not t:
        return True

    return "vazeci_cenovnik" in t


def matches_product(row, product):
    """
    Ključne reči tražimo prvenstveno u NAZIVU PROIZVODA,
    ne u celoj kategoriji.
    """
    name = norm(product_name(row))

    if not name:
        return False

    include = [norm(x) for x in product.get("include", [])]
    exclude = [norm(x) for x in product.get("exclude", [])]

    # Sve include reči moraju biti u nazivu proizvoda
    if not all(word in name for word in include):
        return False

    # Nijedna exclude reč ne sme biti u nazivu
    if any(word in name for word in exclude):
        return False

    return True


def comparable_price(row, product):
    """
    Odlučuje koju cenu koristimo za poređenje.

    RSD/kg i RSD/l -> Cena po jedinici mere.
    RSD/kom -> stvarna cena pakovanja/komada.
    """
    unit = product.get("unit", "")

    regular = price_regular(row)
    unit_price = price_unit(row)
    sale = price_sale(row)

    if unit in ("RSD/kg", "RSD/l"):
        # Kod robe koja se poredi po kg/l prvenstveno koristimo
        # zvaničnu cenu po jedinici mere.
        price = unit_price if unit_price is not None else regular

        # Ako je proizvod već prodavan po kg/l i ima aktivnu akciju,
        # snižena cena je uporediva direktno.
        raw_unit = norm(get_field(row, "Jedinica mere"))

        if sale_is_active(row) and raw_unit in (
            "kg", "kilogram", "l", "lit", "litar"
        ):
            price = sale

        return price

    # RSD/kom
    if sale_is_active(row):
        return sale

    return regular


def status_for(price, target):
    if price <= target:
        return "KUPI"

    if price <= target * 1.10:
        return "BLIZU"

    return "CEKAJ"


def main():
    products = json.loads(
        (ROOT / "products.json").read_text(encoding="utf-8")
    )["products"]

    sources = json.loads(
        (ROOT / "sources.json").read_text(encoding="utf-8")
    )["sources"]

    results = []

    for src in sources:
        if not src.get("enabled"):
            continue

        content = fetch(src)

        if not content:
            print(f"{src['name']}: NO DATA")
            continue

        try:
            rows = parse_csv(content)
        except Exception as e:
            print(f"{src['name']}: CSV parse error: {e}")
            continue

        print(f"{src['name']}: {len(rows)} rows downloaded")

        # Samo važeći cenovnik
        current_rows = [
            row for row in rows
            if is_current_price_list(row)
        ]

        print(
            f"{src['name']}: "
            f"{len(current_rows)} rows in current price list"
        )

        for product in products:
            candidates = []

            for row in current_rows:
                if not matches_product(row, product):
                    continue

                price = comparable_price(row, product)

                if price is None or price <= 0:
                    continue

                candidates.append((price, row))

            if not candidates:
                print(
                    f"{src['name']} | {product['name']}: no match"
                )
                continue

            price, row = min(candidates, key=lambda x: x[0])

            result = {
                "date": str(TODAY),
                "store": src["name"],
                "product_id": product["id"],
                "product": product["name"],
                "matched_product": product_name(row),
                "category": category_name(row),
                "price": round(price, 2),
                "regular_price": price_regular(row),
                "sale_price": (
                    price_sale(row)
                    if sale_is_active(row)
                    else None
                ),
                "target": product["target_price"],
                "unit": product["unit"],
                "status": status_for(
                    price,
                    product["target_price"]
                ),
                "barcode": get_field(row, "Barkod proizvoda"),
                "price_list_date": get_field(row, "Datum cenovnika"),
            }

            results.append(result)

            print(
                f"{src['name']} | "
                f"{product['name']} -> "
                f"{result['matched_product']} -> "
                f"{price:.2f} {product['unit']} -> "
                f"{result['status']}"
            )

    # Najkorisniji rezultati prvi
    order = {
        "KUPI": 0,
        "BLIZU": 1,
        "CEKAJ": 2,
    }

    results.sort(
        key=lambda x: (
            order.get(x["status"], 9),
            x["product"],
            x["price"],
        )
    )

    docs = ROOT / "docs"
    history = ROOT / "data" / "history"

    docs.mkdir(exist_ok=True)
    history.mkdir(parents=True, exist_ok=True)

    output = json.dumps(
        results,
        ensure_ascii=False,
        indent=2,
    )

    (docs / "results.json").write_text(
        output,
        encoding="utf-8",
    )

    (history / f"{TODAY}.json").write_text(
        output,
        encoding="utf-8",
    )

    print()
    print("=" * 60)
    print(f"Saved {len(results)} valid matches.")
    print("=" * 60)


if __name__ == "__main__":
    main()
