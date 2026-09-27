import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import update_prices as up

CFG = json.loads((ROOT / "products.json").read_text(encoding="utf-8"))
PRODUCTS = {p["id"]: p for p in CFG["products"]}


def row(**kwargs):
    base = {
        "KATEGORIJA": "",
        "NAZIV KATEGORIJE": "",
        "Naziv proizvoda": "",
        "Robna marka": "",
        "Barkod proizvoda": "",
        "Jedinica mere": "",
        "Naziv trgovca - formata": "",
        "Datum cenovnika": "27-09-2026",
        "Redovna cena": "",
        "Cena po jedinici mere": "",
        "Snižena cena": "",
        "Datum početka sniženja": "",
        "Datum kraja sniženja": "",
        "VRSTA_CENOVNIKA": "VAZECI_CENOVNIK",
    }
    base.update(kwargs)
    return base


class ParserTests(unittest.TestCase):
    def test_decimal_parser(self):
        self.assertEqual(up.parse_number("284.99"), 284.99)
        self.assertEqual(up.parse_number("284,99"), 284.99)
        self.assertEqual(up.parse_number("1.099,99"), 1099.99)

    def test_whole_chicken_approx_weight_is_not_divided(self):
        r = row(
            KATEGORIJA="8",
            **{
                "NAZIV KATEGORIJE": "Sveže i prerađeno meso",
                "Naziv proizvoda": "Celo pile MK12,46 ca. 1,78 kg",
                "Jedinica mere": "kom.",
                "Redovna cena": "284.99",
                "Cena po jedinici mere": "284.99",
            }
        )
        p = PRODUCTS["celo_pile"]
        ok, _, pack = up.match_product(r, p)
        self.assertTrue(ok)
        self.assertTrue(pack["variable_weight"])
        prices = up.normalize_prices(r, p, pack)
        self.assertEqual(prices["current"], 284.99)

    def test_fixed_500g_pack_is_normalized(self):
        r = row(
            KATEGORIJA="8",
            **{
                "NAZIV KATEGORIJE": "Sveže i prerađeno meso",
                "Naziv proizvoda": "Pileci batak i karabatak 500g",
                "Jedinica mere": "kg",
                "Redovna cena": "219.99",
                "Cena po jedinici mere": "439.98",
            }
        )
        p = PRODUCTS["batak_karabatak"]
        ok, _, pack = up.match_product(r, p)
        self.assertTrue(ok)
        prices = up.normalize_prices(r, p, pack)
        self.assertAlmostEqual(prices["current"], 439.98, places=2)

    def test_gr_abbreviation_is_normalized(self):
        r = row(
            KATEGORIJA="8",
            **{
                "NAZIV KATEGORIJE": "Sveže i prerađeno meso",
                "Naziv proizvoda": "Pileci file 480 gr Sveze M",
                "Jedinica mere": "kg",
                "Redovna cena": "403.20",
                "Cena po jedinici mere": "403.20",
            }
        )
        p = PRODUCTS["pileci_file"]
        ok, _, pack = up.match_product(r, p)
        self.assertTrue(ok)
        self.assertAlmostEqual(pack["kg"], 0.48)
        prices = up.normalize_prices(r, p, pack)
        self.assertAlmostEqual(prices["current"], 840.0, places=2)

    def test_eggs_are_per_piece(self):
        r = row(
            KATEGORIJA="1",
            **{
                "NAZIV KATEGORIJE": "Mleko, mlečni i mešoviti proizvodi, jaja",
                "Naziv proizvoda": "Jaja iz podnog uzgoja M 30/1 30kom",
                "Jedinica mere": "kom.",
                "Redovna cena": "479.99",
            }
        )
        p = PRODUCTS["jaja"]
        ok, _, pack = up.match_product(r, p)
        self.assertTrue(ok)
        self.assertEqual(pack["count"], 30)
        prices = up.normalize_prices(r, p, pack)
        self.assertEqual(prices["current"], 16.0)

    def test_quail_eggs_are_rejected(self):
        r = row(
            KATEGORIJA="1",
            **{
                "NAZIV KATEGORIJE": "Mleko, mlečni i mešoviti proizvodi, jaja",
                "Naziv proizvoda": "Jaja japanske prepelice 12/1",
                "Redovna cena": "219.99",
            }
        )
        ok, reason, _ = up.match_product(r, PRODUCTS["jaja"])
        self.assertFalse(ok)
        self.assertEqual(reason, "excluded_term")

    def test_fractional_half_kilo(self):
        pack = up.parse_package(PRODUCTS["pasulj"], "PASULJ TETOVAC 1/2KG ALLORO")
        self.assertEqual(pack["kg"], 0.5)

    def test_multipack_weight(self):
        pack = up.parse_package(PRODUCTS["pasulj"], "PASULJ 2x500g")
        self.assertEqual(pack["kg"], 1.0)

    def test_active_sale_variable_weight(self):
        r = row(
            KATEGORIJA="7",
            **{
                "NAZIV KATEGORIJE": "Smrznuti proizvodi",
                "Naziv proizvoda": "OSLIC RF",
                "Jedinica mere": "KG",
                "Redovna cena": "499.99",
                "Cena po jedinici mere": "499.99",
                "Snižena cena": "399.99",
                "Datum početka sniženja": "01-01-2026",
                "Datum kraja sniženja": "31-12-2026",
            }
        )
        p = PRODUCTS["oslic"]
        ok, _, pack = up.match_product(r, p)
        self.assertTrue(ok)
        old_today = up.TODAY
        try:
            up.TODAY = date(2026, 9, 27)
            prices = up.normalize_prices(r, p, pack)
        finally:
            up.TODAY = old_today
        self.assertEqual(prices["current"], 399.99)
        self.assertTrue(prices["is_sale"])

    def test_hibiscus_does_not_match_mackerel(self):
        r = row(
            KATEGORIJA="2",
            **{
                "NAZIV KATEGORIJE": "Bezalkoholna pića, kafa, čaj",
                "Naziv proizvoda": "Fuze Tea Breskva i Hibiskus 1,5l",
                "Redovna cena": "159.99",
            }
        )
        ok, _, _ = up.match_product(r, PRODUCTS["skusa"])
        self.assertFalse(ok)

    def test_tuna_salad_is_rejected(self):
        r = row(
            KATEGORIJA="9",
            **{
                "NAZIV KATEGORIJE": "Sveža i prerađena riba",
                "Naziv proizvoda": "Tuna sal.Mexico Maxi 160g",
                "Redovna cena": "134.99",
            }
        )
        ok, reason, _ = up.match_product(r, PRODUCTS["tunjevina"])
        self.assertFalse(ok)
        self.assertEqual(reason, "excluded_term")

    def test_marinated_pork_neck_is_rejected(self):
        r = row(
            KATEGORIJA="8",
            **{
                "NAZIV KATEGORIJE": "Sveže i prerađeno meso",
                "Naziv proizvoda": "Svinjski vrat sa koskom mariniran 500g",
                "Redovna cena": "399.99",
            }
        )
        ok, reason, _ = up.match_product(r, PRODUCTS["svinjski_vrat"])
        self.assertFalse(ok)
        self.assertEqual(reason, "excluded_term")

    def test_flour_requires_type_400_or_500(self):
        good = row(
            KATEGORIJA="13",
            **{
                "NAZIV KATEGORIJE": "Brašno",
                "Naziv proizvoda": "Psenicno brasno T500 1kg",
                "Redovna cena": "49.99",
            }
        )
        bad = row(
            KATEGORIJA="13",
            **{
                "NAZIV KATEGORIJE": "Brašno",
                "Naziv proizvoda": "Psenicno brasno T850 1kg",
                "Redovna cena": "45.99",
            }
        )
        self.assertTrue(up.match_product(good, PRODUCTS["brasno"])[0])
        self.assertFalse(up.match_product(bad, PRODUCTS["brasno"])[0])

    def test_bulk_onion_is_rejected(self):
        r = row(
            KATEGORIJA="3",
            **{
                "NAZIV KATEGORIJE": "Sveže voće i povrće",
                "Naziv proizvoda": "Crni luk, 5kg",
                "Redovna cena": "129.99",
            }
        )
        ok, reason, _ = up.match_product(r, PRODUCTS["luk"])
        self.assertFalse(ok)
        self.assertEqual(reason, "package_too_large")

    def test_plain_sunflower_oil_brand_without_word_sunflower_can_match(self):
        r = row(
            KATEGORIJA="15",
            **{
                "NAZIV KATEGORIJE": "Ulja i masti",
                "Naziv proizvoda": "ULJE ALLORO 1L",
                "Redovna cena": "199.99",
            }
        )
        self.assertTrue(up.match_product(r, PRODUCTS["ulje"])[0])

    def test_other_oil_is_rejected(self):
        r = row(
            KATEGORIJA="15",
            **{
                "NAZIV KATEGORIJE": "Ulja i masti",
                "Naziv proizvoda": "Maslinovo ulje 1L",
                "Redovna cena": "999.99",
            }
        )
        self.assertFalse(up.match_product(r, PRODUCTS["ulje"])[0])

    def test_monthly_snapshot_is_not_current(self):
        r = row(VRSTA_CENOVNIKA="MESECNI_PRESEK")
        self.assertFalse(up.is_current_price_list(r))

    def test_maxi_format_filter(self):
        src = {"allowed_formats_exact": ["Maxi"]}
        r1 = row(**{"Naziv trgovca - formata": "Maxi"})
        r2 = row(**{"Naziv trgovca - formata": "Shop and Go"})
        self.assertTrue(up.source_row_allowed(src, r1))
        self.assertFalse(up.source_row_allowed(src, r2))

    def test_fixed_pack_sale_is_normalized(self):
        r = row(
            KATEGORIJA="8",
            **{
                "NAZIV KATEGORIJE": "Sveže i prerađeno meso",
                "Naziv proizvoda": "Pileci batak i karabatak 500g",
                "Jedinica mere": "kg",
                "Redovna cena": "219.99",
                "Cena po jedinici mere": "439.98",
                "Snižena cena": "199.99",
                "Datum početka sniženja": "01-01-2026",
                "Datum kraja sniženja": "31-12-2026",
            }
        )
        p = PRODUCTS["batak_karabatak"]
        ok, _, pack = up.match_product(r, p)
        self.assertTrue(ok)
        old_today = up.TODAY
        try:
            up.TODAY = date(2026, 9, 27)
            prices = up.normalize_prices(r, p, pack)
        finally:
            up.TODAY = old_today
        self.assertAlmostEqual(prices["regular"], 439.98, places=2)
        self.assertAlmostEqual(prices["sale"], 399.98, places=2)
        self.assertAlmostEqual(prices["current"], 399.98, places=2)

    def test_fixed_pack_derivation_beats_bad_official_unit(self):
        r = row(
            KATEGORIJA="8",
            **{
                "NAZIV KATEGORIJE": "Sveže i prerađeno meso",
                "Naziv proizvoda": "Svinjska plecka kocke 510g",
                "Jedinica mere": "kg",
                "Redovna cena": "433.49",
                "Cena po jedinici mere": "433.49",
            }
        )
        p = PRODUCTS["svinjska_plecka"]
        ok, _, pack = up.match_product(r, p)
        self.assertTrue(ok)
        prices = up.normalize_prices(r, p, pack)
        self.assertAlmostEqual(prices["current"], 849.98, places=2)
        self.assertIn("official_unit_mismatch", prices["warnings"])


if __name__ == "__main__":
    unittest.main()
