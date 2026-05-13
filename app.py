import os
import re
import json
import urllib.parse
import urllib.request
import threading
from flask import Flask, jsonify, request

app = Flask(__name__)

TMDB_KEY = os.environ.get('TMDB_KEY', '18029be2c2d8afe9ba42e4c21e25eae9')
TORRENTIO_BASE = 'https://torrentio.strem.fun'

# === CZ/SK filtr ===
CZSK_KW = [
    'czech', 'slovak', 'cesky', 'slovensky', 'ceska', 'slovenska',
    'cz dabing', 'sk dabing', 'cz dab', 'sk dab',
    'czdab', 'skdab', 'dabovano',
    'cz-dabing', 'sk-dabing', 'cz_dabing', 'sk_dabing',
    'lektor cz', 'lektor sk',
    'cz titulky', 'sk titulky', 'titulky cz', 'titulky sk',
    'czech audio', 'slovak audio', 'czech sub', 'slovak sub',
    'cz audio', 'sk audio', 'cz sub', 'sk sub',
    '.cz.', '.sk.', '-cz-', '-sk-', '[cz]', '[sk]', '(cz)', '(sk)',
    'multi cz', 'multi sk',
]

# Znaky typicky pro CZ/SK nazvy souboru
_CZECH_CHARS = set('áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ')

def is_czsk(text):
    t = text.lower()
    # Explicitni klicova slova
    if any(kw in t for kw in CZSK_KW):
        return True
    # Ceske znaky v nazvu = pravdepodobne CZ/SK verze
    if any(c in text for c in _CZECH_CHARS):
        return True
    # Multi audio s vysokym poctem jazyku - casto obsahuje CZ
    if 'multi' in t and any(x in t for x in ['11 lang', '10 lang', '12 lang', 'multisub', 'multi sub', 'multi audio']):
        return True
    return False

def fetch_url(url, timeout=15, extra_headers=None):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/html, */*',
            'Accept-Language': 'cs,sk;q=0.9,en;q=0.8',
        }
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f'[fetch_url] {url} => {e}')
        return None

# ─────────────────────────────────────────────
# TMDB helpers
# ─────────────────────────────────────────────
def get_imdb_id(tmdb_id, media_type):
    endpoint = 'tv' if media_type == 'series' else 'movie'
    url = f'https://api.themoviedb.org/3/{endpoint}/{tmdb_id}/external_ids?api_key={TMDB_KEY}'
    raw = fetch_url(url)
    if raw:
        try:
            return json.loads(raw).get('imdb_id', '')
        except Exception:
            pass
    return None

def get_tmdb_titles(tmdb_id, media_type):
    """Vraci seznam titulu (original + alternativni) pro lepsi vyhledavani."""
    endpoint = 'tv' if media_type == 'series' else 'movie'
    titles = set()
    # Zakladni info
    url = f'https://api.themoviedb.org/3/{endpoint}/{tmdb_id}?api_key={TMDB_KEY}&language=cs-CZ'
    raw = fetch_url(url)
    if raw:
        try:
            d = json.loads(raw)
            for k in ('title', 'name', 'original_title', 'original_name'):
                v = d.get(k, '')
                if v:
                    titles.add(v)
        except Exception:
            pass
    # Alternativni tituly
    alt_url = f'https://api.themoviedb.org/3/{endpoint}/{tmdb_id}/alternative_titles?api_key={TMDB_KEY}'
    raw2 = fetch_url(alt_url)
    if raw2:
        try:
            d2 = json.loads(raw2)
            items = d2.get('titles') or d2.get('results') or []
            for item in items:
                iso = item.get('iso_3166_1', '')
                if iso in ('CZ', 'SK', 'US', 'GB', ''):
                    t = item.get('title', '')
                    if t:
                        titles.add(t)
        except Exception:
            pass
    return list(titles)

# ─────────────────────────────────────────────
# TORRENTIO
# ─────────────────────────────────────────────
def search_torrentio(imdb_id, media_type, season=None, episode=None):
    filters = 'sort=qualitysize%7Cqualityfilter=480p,360p,240p,scr,cam'
    if media_type == 'series' and season and episode:
        url = f'{TORRENTIO_BASE}/{filters}/stream/series/{imdb_id}:{season}:{episode}.json'
    else:
        url = f'{TORRENTIO_BASE}/{filters}/stream/movie/{imdb_id}.json'

    print(f'[torrentio] {url}')
    raw = fetch_url(url, timeout=20)
    if not raw:
        return []
    try:
        streams = json.loads(raw).get('streams', [])
        print(f'[torrentio] {len(streams)} streamu celkem')
        return streams
    except Exception as e:
        print(f'[torrentio] parse error: {e}')
        return []

def torrentio_to_result(stream):
    """Prevede Torrentio stream na nas format."""
    name  = stream.get('name', '')
    title = stream.get('title', '')
    info_hash = stream.get('infoHash', '')
    file_idx  = stream.get('fileIdx', 0)

    if not info_hash:
        return None

    # Kvalita a velikost z title
    quality = ''
    size = ''
    seeds = ''
    lines = title.replace('\\n', '\n').split('\n')
    for line in lines:
        line = line.strip()
        if any(q in line for q in ['720p', '1080p', '2160p', '4K', '4k', 'HDR', 'BluRay', 'WEB', 'REMUX']):
            quality = line
        elif any(u in line for u in [' GB', ' MB']):
            size = line
        elif '👤' in line:
            seeds = line

    label_parts = [quality or name or 'Torrent']
    if size:
        label_parts.append(size)
    if seeds:
        label_parts.append(seeds)
    label = ' · '.join(p for p in label_parts if p)

    magnet = f'magnet:?xt=urn:btih:{info_hash}'
    if title:
        # Prvni radek title = nazev souboru
        fname = lines[0].strip() if lines else title
        magnet += '&dn=' + urllib.parse.quote(fname)

    return {
        'label': label,
        'magnet': magnet,
        'info_hash': info_hash,
        'file_idx': file_idx,
        'source': 'Torrentio',
        'czsk': is_czsk(name + ' ' + title),
        'quality': quality,
        'size': size,
        'seeds': seeds,
        'title_raw': title,
    }

# ─────────────────────────────────────────────
# SKATORRENT / CZTORRENT scraping
# ─────────────────────────────────────────────
def search_cztorrent(query, season=None, episode=None):
    """Scrape cztorrent.eu - verejny CZ torrent web."""
    results = []
    try:
        # cztorrent.eu ma verejne vyhledavani
        search_q = query
        if season and episode:
            search_q += f' S{int(season):02d}E{int(episode):02d}'
        url = 'https://www.cztorrent.eu/?search=' + urllib.parse.quote(search_q)
        print(f'[cztorrent] {url}')
        raw = fetch_url(url, timeout=15)
        if not raw:
            return []

        # Hledame torrent linky
        # <a href="/download/HASH/nazev.torrent">
        pattern = re.compile(
            r'<a[^>]+href=["\']([^"\']*\.torrent[^"\']*)["\'][^>]*>([^<]{3,120})</a>',
            re.I
        )
        for match in pattern.finditer(raw):
            href, name = match.group(1), match.group(2).strip()
            name = re.sub(r'\s+', ' ', name)
            if not name or len(name) < 3:
                continue
            if not is_czsk(name) and not any(q in name.lower() for q in ['720p','1080p','2160p']):
                continue
            full_url = href if href.startswith('http') else 'https://www.cztorrent.eu' + href
            results.append({
                'label': name,
                'torrent_url': full_url,
                'magnet': None,
                'source': 'CZTorrent',
                'czsk': is_czsk(name),
                'quality': next((q for q in ['2160p','1080p','720p'] if q in name), ''),
                'size': '',
                'seeds': '',
            })
            if len(results) >= 10:
                break

        print(f'[cztorrent] {len(results)} vysledku')
    except Exception as e:
        print(f'[cztorrent] error: {e}')
    return results

def search_torentino(query, season=None, episode=None):
    """Scrape torentino.cz - populární CZ/SK torrent web."""
    results = []
    try:
        search_q = query
        if season and episode:
            search_q += f' S{int(season):02d}E{int(episode):02d}'
        url = 'https://www.torentino.cz/hledej/?q=' + urllib.parse.quote(search_q)
        print(f'[torentino] {url}')
        raw = fetch_url(url, timeout=15)
        if not raw:
            return []

        # <a href="/torrent/12345/nazev-filmu">Nazev</a>
        pattern = re.compile(
            r'<a[^>]+href=["\'](/torrent/\d+/[^"\']+)["\'][^>]*>\s*([^<]{5,120})\s*</a>',
            re.I
        )
        seen = set()
        for match in pattern.finditer(raw):
            href, name = match.group(1), match.group(2).strip()
            name = re.sub(r'\s+', ' ', name)
            if name in seen or len(name) < 5:
                continue
            seen.add(name)
            full_url = 'https://www.torentino.cz' + href
            results.append({
                'label': name,
                'torrent_url': full_url,
                'magnet': None,
                'source': 'Torentino',
                'czsk': is_czsk(name),
                'quality': next((q for q in ['2160p','1080p','720p'] if q in name), ''),
                'size': '',
                'seeds': '',
            })
            if len(results) >= 10:
                break

        print(f'[torentino] {len(results)} vysledku')
    except Exception as e:
        print(f'[torentino] error: {e}')
    return results

def search_sktorrent(query, season=None, episode=None):
    """Scrape sktorrent.eu."""
    results = []
    try:
        search_q = query
        if season and episode:
            search_q += f' S{int(season):02d}E{int(episode):02d}'
        url = ('https://sktorrent.eu/torrent/torrents_v2.php?search=' +
               urllib.parse.quote(search_q) + '&active=0&category=0')
        print(f'[sktorrent] {url}')
        raw = fetch_url(url, timeout=15)
        if not raw:
            return []

        pattern = re.compile(
            r'<a[^>]+href=["\']([^"\']*download[^"\']*)["\'][^>]*title=["\']([^"\']{5,120})["\']',
            re.I
        )
        seen = set()
        for match in pattern.finditer(raw):
            href, name = match.group(1), match.group(2).strip()
            if name in seen:
                continue
            seen.add(name)
            full_url = href if href.startswith('http') else 'https://sktorrent.eu' + href
            results.append({
                'label': name,
                'torrent_url': full_url,
                'magnet': None,
                'source': 'SKTorrent',
                'czsk': is_czsk(name),
                'quality': next((q for q in ['2160p','1080p','720p'] if q in name), ''),
                'size': '',
                'seeds': '',
            })
            if len(results) >= 10:
                break

        print(f'[sktorrent] {len(results)} vysledku')
    except Exception as e:
        print(f'[sktorrent] error: {e}')
    return results

# ─────────────────────────────────────────────
# HLAVNI SEARCH ENDPOINT
# ─────────────────────────────────────────────
@app.route('/search', methods=['GET'])
def search():
    """
    Parametry:
      tmdb_id    - TMDB ID
      media_type - movie / series
      season     - cislo rady (pro series)
      episode    - cislo dilu (pro series)
      title      - nazev (fallback pro scraping)
      czsk_only  - 1/0 (default 1)
    """
    tmdb_id    = request.args.get('tmdb_id', '')
    media_type = request.args.get('media_type', 'movie')
    season     = request.args.get('season')
    episode    = request.args.get('episode')
    title      = request.args.get('title', '')
    czsk_only  = request.args.get('czsk_only', '1') == '1'

    if not tmdb_id:
        return jsonify({'error': 'tmdb_id required'}), 400

    all_results = []
    errors = []

    # --- Torrentio (rychle, pres IMDB ID) ---
    imdb_id = get_imdb_id(tmdb_id, media_type)
    if imdb_id:
        t_streams = search_torrentio(imdb_id, media_type, season, episode)
        for s in t_streams:
            r = torrentio_to_result(s)
            if r is None:
                continue
            if czsk_only and not r['czsk']:
                continue
            all_results.append(r)
    else:
        errors.append('IMDB ID not found')

    # --- CZ/SK scraping (paralelne) ---
    if title:
        scrape_results = []
        lock = threading.Lock()

        def scrape(fn, *args):
            try:
                res = fn(*args)
                with lock:
                    scrape_results.extend(res)
            except Exception as e:
                print(f'scrape error {fn.__name__}: {e}')

        threads = [
            threading.Thread(target=scrape, args=(search_cztorrent, title, season, episode)),
            threading.Thread(target=scrape, args=(search_torentino, title, season, episode)),
            threading.Thread(target=scrape, args=(search_sktorrent, title, season, episode)),
        ]
        for t in threads:
            t.daemon = True
            t.start()
        for t in threads:
            t.join(timeout=12)

        # Filtruj a pridej
        for r in scrape_results:
            if czsk_only and not r.get('czsk'):
                continue
            all_results.append(r)

    # Deduplikace podle info_hash / label
    seen_hashes = set()
    seen_labels = set()
    deduped = []
    for r in all_results:
        h = r.get('info_hash', '')
        l = r.get('label', '')
        if h and h in seen_hashes:
            continue
        if l and l in seen_labels:
            continue
        if h:
            seen_hashes.add(h)
        if l:
            seen_labels.add(l)
        deduped.append(r)

    # Razeni: nejdrive CZ/SK, pak kvalita
    def sort_key(r):
        czsk = 0 if r.get('czsk') else 1
        q = r.get('quality', '')
        qorder = 0 if '2160' in q else 1 if '1080' in q else 2 if '720' in q else 3
        return (czsk, qorder)

    deduped.sort(key=sort_key)

    print(f'[search] celkem {len(deduped)} vysledku (czsk_only={czsk_only})')

    return jsonify({
        'results': deduped,
        'total': len(deduped),
        'imdb_id': imdb_id or '',
        'errors': errors,
    })


@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({'status': 'ok', 'service': 'MEGA CZ Torrent Backend'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
