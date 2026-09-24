"""Récupération d'image de chaîne : YouTube, Spotify, SoundCloud, ou scraping OG en fallback."""
import json
import os
import re
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
MAX_HTML_BYTES = 500_000
MAX_IMAGE_BYTES = 4 * 1024 * 1024


# ─────────────────────────────────────────────
# PARSER OG
# ─────────────────────────────────────────────

class _OGParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.og = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        a = dict(attrs)
        prop = (a.get("property") or a.get("name") or "").lower()
        if prop.startswith("og:") and a.get("content"):
            self.og.setdefault(prop, a["content"])


# ─────────────────────────────────────────────
# EXTRACTEURS SPÉCIFIQUES
# ─────────────────────────────────────────────

def _youtube_video_id(url):
    """Retourne l'ID vidéo YouTube ou None."""
    u = urlparse(url)
    host = (u.netloc or "").lower()
    if host.endswith("youtu.be"):
        vid = u.path.lstrip("/").split("/")[0]
        return vid or None
    if "youtube.com" in host:
        if u.path == "/watch":
            return parse_qs(u.query).get("v", [None])[0]
        m = re.match(r"^/(embed|shorts|live|v)/([^/?#]+)", u.path)
        if m:
            return m.group(2)
    return None


def _try_youtube(url):
    vid = _youtube_video_id(url)
    if not vid:
        return None
    # maxresdefault n'existe pas pour toutes les vidéos, hqdefault est toujours là
    for candidate in (
        f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
    ):
        try:
            r = requests.head(candidate, timeout=6, allow_redirects=True,
                              headers={"User-Agent": USER_AGENT})
            if r.status_code == 200 and int(r.headers.get("Content-Length", "0") or 0) > 2000:
                return candidate
        except Exception:
            pass
    # dernier recours : renvoyer hqdefault sans vérif
    return f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"


def _try_oembed(url, endpoint):
    """Spotify et SoundCloud exposent un endpoint oEmbed standard."""
    try:
        r = requests.get(
            endpoint,
            params={"url": url, "format": "json"},
            timeout=8,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        return data.get("thumbnail_url")
    except Exception:
        return None


def _try_spotify(url):
    if "spotify.com" in urlparse(url).netloc.lower():
        return _try_oembed(url, "https://open.spotify.com/oembed")
    return None


def _try_soundcloud(url):
    if "soundcloud.com" in urlparse(url).netloc.lower():
        return _try_oembed(url, "https://soundcloud.com/oembed")
    return None


def _try_generic_og(source_url, timeout=8):
    """Scraping OG classique — fallback pour tout le reste."""
    try:
        r = requests.get(
            source_url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"},
            allow_redirects=True,
        )
        r.raise_for_status()
    except Exception:
        return None

    html = r.text[:MAX_HTML_BYTES]
    p = _OGParser()
    try:
        p.feed(html)
    except Exception:
        pass
    img = p.og.get("og:image")

    if not img:
        m = re.search(
            r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if m:
            img = m.group(1)

    if not img:
        return None

    if img.startswith("//"):
        img = "https:" + img
    elif img.startswith("/"):
        parsed = urlparse(source_url)
        img = f"{parsed.scheme}://{parsed.netloc}{img}"
    return img


# ─────────────────────────────────────────────
# SÉLECTION DE LA MÉTHODE
# ─────────────────────────────────────────────

def _resolve_image_url(source_url):
    """Essaie YouTube → Spotify → SoundCloud → scraping OG générique."""
    for fn in (_try_youtube, _try_spotify, _try_soundcloud):
        try:
            result = fn(source_url)
            if result:
                return result
        except Exception:
            pass
    return _try_generic_og(source_url)


def _ext_from_content_type(ct):
    ct = (ct or "").lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    return None


# ─────────────────────────────────────────────
# TÉLÉCHARGEMENT
# ─────────────────────────────────────────────

def download_og_image(source_url, track_id, meta_dir, timeout=10):
    """Télécharge l'image dans meta_dir/<track_id>.<ext>. Retourne le nom, ou None."""
    if not source_url:
        return None

    img_url = _resolve_image_url(source_url)
    if not img_url:
        print(f"[og] aucune image trouvée pour {source_url}")
        return None

    try:
        r = requests.get(
            img_url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            stream=True,
        )
        r.raise_for_status()
    except Exception as e:
        print(f"[og] téléchargement échoué ({e}) : {img_url}")
        return None

    ext = _ext_from_content_type(r.headers.get("Content-Type"))
    if not ext:
        guess = os.path.splitext(urlparse(img_url).path)[1].lower()
        ext = guess if guess in (".jpg", ".jpeg", ".png", ".webp", ".gif") else ".jpg"
    if ext == ".jpeg":
        ext = ".jpg"

    filename = f"{track_id}{ext}"
    abs_path = os.path.join(meta_dir, filename)

    # Nettoie les anciennes variantes d'extension
    for old_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        old = os.path.join(meta_dir, f"{track_id}{old_ext}")
        if old != abs_path and os.path.exists(old):
            try:
                os.unlink(old)
            except Exception:
                pass

    written = 0
    try:
        with open(abs_path, "wb") as f:
            for chunk in r.iter_content(65536):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_IMAGE_BYTES:
                    raise ValueError("image trop grosse")
                f.write(chunk)
    except Exception as e:
        print(f"[og] écriture échouée ({e})")
        try:
            os.unlink(abs_path)
        except Exception:
            pass
        return None

    print(f"[og] track {track_id} → {filename} ({written} octets, source={img_url[:60]}...)")
    return filename
