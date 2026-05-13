# MEGA CZ Torrent Backend

Flask API pro Kodi addon MEGA CZ - vyhledava CZ/SK torrenty.

## Zdroje
- **Torrentio** - pres IMDB ID (rychle, spolehlave)
- **CZTorrent** - scraping cztorrent.eu
- **Torentino** - scraping torentino.cz  
- **SKTorrent** - scraping sktorrent.eu

## Deploy na Render.com

1. Nahrej tento folder na GitHub (novy repozitar)
2. Na render.com → New → Web Service → pripoj GitHub repo
3. Nastav:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --workers 2 --timeout 30 --bind 0.0.0.0:$PORT`
   - **Plan:** Free
4. Deploy → ziskej URL (napr. `https://megacz-torrent.onrender.com`)
5. Tuto URL zadej do Kodi addonu MEGA CZ

## API

### GET /ping
Test ze server bezi.

### GET /search
Parametry:
- `tmdb_id` - TMDB ID titulu (povinny)
- `media_type` - `movie` nebo `series`
- `season` - cislo rady (pro serialy)
- `episode` - cislo dilu (pro serialy)
- `title` - nazev titulu (pro scraping)
- `czsk_only` - `1` = jen CZ/SK (default), `0` = vse

Priklad:
```
GET /search?tmdb_id=1396&media_type=series&season=1&episode=1&title=Breaking+Bad&czsk_only=1
```

Response:
```json
{
  "results": [
    {
      "label": "1080p BluRay · 8.2 GB · 👤 50",
      "magnet": "magnet:?xt=urn:btih:...",
      "info_hash": "abc123...",
      "source": "Torrentio",
      "czsk": true,
      "quality": "1080p",
      "size": "8.2 GB"
    }
  ],
  "total": 5,
  "imdb_id": "tt0903747"
}
```
