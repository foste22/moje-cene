# Moje cene — finalna stabilna osnova

Ova verzija je napravljena da prestane sa ručnim krpljenjem pojedinačnih primera.

## Šta rešava

- pravilno čitanje decimalnih cena;
- samo `VAZECI_CENOVNIK`, bez mešanja mesečnih preseka;
- fiksna pakovanja (`500g`, `480 gr`, `5kg`, `1l`, `2x500g`, `1/2KG`);
- promenljiva masa (`ca.`, `cca`, `RF`, `rinfuz`) bez pogrešnog deljenja;
- jaja po komadu (`6/1`, `10/1`, `30kom`) i isključivanje prepeličjih jaja;
- aktivne akcije, posebno za fiksno pakovanje i robu na meru;
- kategorije + cele reči/prefiksi + negativni filteri, umesto prostog substring pretraživanja;
- brašno samo T-400/T-500;
- izbegavanje nepraktičnog bulk pakovanja crnog luka većeg od 2 kg;
- normalizovane redovne i akcijske cene u istoj jedinici;
- ako jedan izvor padne, prethodni rezultat se zadržava i jasno označava kao `stale`;
- `data/debug/YYYY-MM-DD.json` čuva najbolje kandidate i razloge odbacivanja;
- automatski testovi sprečavaju povratak već rešenih grešaka;
- PWA interfejs pravilno sortira KUPI → BLIZU → ČEKAJ i grupiše ponude po proizvodu.

## Izvori

Uključeni: Lidl, Maxi, Aman.

`dm` i `Lilly` imaju pripremljene javne CSV izvore, ali su isključeni dok se ne dodaju proizvodi za higijenu/kućnu hemiju.

DIS je namerno isključen dok ne postoji pouzdan aktuelni mašinski čitljiv feed. Bolje je ne prikazati DIS nego staru cenu predstaviti kao današnju.

## Zamena u postojećem repozitorijumu

Zameni:
- `update_prices.py`
- `products.json`
- `sources.json`
- `requirements.txt`
- `docs/index.html`
- `docs/manifest.json`

Dodaj:
- `docs/sw.js`
- `tests/test_price_parser.py`

I zameni workflow:
- `.github/workflows/update-prices.yml`

Zatim ručno pokreni **Actions → Update prices → Run workflow**.
