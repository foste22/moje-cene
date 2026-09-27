# Moje cene – GitHub-ready MVP

Prva verzija ličnog trackera cena.

## Trenutno povezano
- Lidl
- Maxi / Delhaize
- Aman

DIS, dm i Lilly su već predviđeni u `sources.json`, ali ostavljeni isključeni dok ne potvrdimo pouzdan mašinski čitljiv izvor.

## Pokretanje
```bash
pip install -r requirements.txt
python update_prices.py
```

## GitHub
1. Napravi novi repo i ubaci sadržaj ovog foldera.
2. Actions → `Update prices` → Run workflow.
3. Settings → Pages → Deploy from branch → `main` / `docs`.
4. `products.json` sadrži proizvode, pragove i ključne reči.
5. Workflow se automatski pokreće svakog jutra (05:15 UTC).

## Važno
Ovo je MVP parser. Pre produkcijske upotrebe treba proveriti stvarna imena kolona i način izražavanja jediničnih cena kod svakog trgovca, pa po potrebi napraviti adapter po lancu.
