import sqlite3
import feedparser
import requests
from bs4 import BeautifulSoup
import os
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# ==========================================================================
# KONFIGURASI PATH
# ==========================================================================
BASE_DIR = Path(__file__).parent.resolve()

# ⚠️ SESUAIKAN NAMA FOLDER DATABASE DENGAN YANG ADA DI REPO ANDA
DB_PATH = str(BASE_DIR / "database_hasil5" / "database.sqlite") 
RSS_FILE = str(BASE_DIR / "rss_list.txt")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

ADSTERRA_CODE = """
<div style="text-align:center; margin:20px 0;">
    <script type="text/javascript">
        atOptions = {'key' : 'f9e40eb3b3cd05f1038aac7b36b33ad2','format' : 'iframe','height' : 90,'width' : 728,'params' : {}};
    </script>
    <script type="text/javascript" src="https://upholdbrilliancerecast.com/f9e40eb3b3cd05f1038aac7b36b33ad2/invoke.js"></script>
</div>
"""

# ==========================================================================
# FUNGSI DATABASE
# ==========================================================================
def inisialisasi_database():
    # Pastikan folder database ada
    db_dir = os.path.dirname(DB_PATH)
    os.makedirs(db_dir, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS artikel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT UNIQUE, 
            judul TEXT, 
            konten TEXT, 
            gambar TEXT,
            kategori TEXT DEFAULT 'other', 
            tanggal_publish TEXT, 
            tanggal_update TEXT
        )
    """)
    
    # Cek apakah kolom baru sudah ada (tambahkan jika belum)
    cursor.execute("PRAGMA table_info(artikel)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'gambar' not in columns: cursor.execute("ALTER TABLE artikel ADD COLUMN gambar TEXT")
    if 'kategori' not in columns: cursor.execute("ALTER TABLE artikel ADD COLUMN kategori TEXT DEFAULT 'other'")
    if 'tanggal_publish' not in columns: cursor.execute("ALTER TABLE artikel ADD COLUMN tanggal_publish TEXT")
    if 'tanggal_update' not in columns: cursor.execute("ALTER TABLE artikel ADD COLUMN tanggal_update TEXT")
    
    conn.commit()
    conn.close()

# ==========================================================================
# FUNGSI SCRAPING
# ==========================================================================
def ekstrak_konten(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        if response.status_code != 200: return None, None, None
        
        soup = BeautifulSoup(response.text, 'html.parser')

        # Ambil Gambar
        gambar = None
        og_image = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'og:image'})
        if og_image and og_image.get('content'): gambar = og_image['content']

        # Ambil Tanggal
        tanggal = None
        for meta_prop in ['article:published_time', 'article:modified_time', 'datePublished']:
            meta = soup.find('meta', property=meta_prop) or soup.find('meta', attrs={'name': meta_prop})
            if meta and meta.get('content'):
                tanggal = meta['content']
                break

        # Ambil Paragraf
        paragraphs = soup.find_all('p')
        teks_lengkap = []
        for p in paragraphs:
            text = p.get_text().strip()
            if len(text) > 40 and not any(x in text.lower() for x in ['cookie', 'privacy policy', 'subscribe', 'all rights reserved', 'terms of use']):
                teks_lengkap.append(f"<p>{text}</p>")

        return "\n".join(teks_lengkap) if teks_lengkap else None, gambar, tanggal
    except Exception:
        return None, None, None

def proses_feed(feed_url):
    feed_url = feed_url.strip()
    if not feed_url or feed_url.startswith("#"): return 0, 0

    baru = 0
    duplikat = 0

    try:
        resp = requests.get(feed_url, headers=HEADERS, timeout=8)
        if resp.status_code != 200: return 0, 0
        
        feed = feedparser.parse(resp.content)
        conn = sqlite3.connect(DB_PATH, timeout=15)
        cursor = conn.cursor()

        # Ambil 5 artikel terbaru per feed
        for entry in feed.entries[:5]:
            link = entry.get('link', '')
            judul = entry.get('title', 'Tanpa Judul')
            if not link: continue

            cursor.execute("SELECT id FROM artikel WHERE link = ?", (link,))
            if cursor.fetchone():
                duplikat += 1
                continue

            html_teks, gambar, tanggal = ekstrak_konten(link)
            if not html_teks:
                html_teks = f"<p>{entry.get('summary', 'Konten tidak tersedia.')}</p>"

            if not tanggal:
                tanggal = datetime.now().isoformat()

            # Susun konten
            img_tag = f'<div style="text-align:center; margin-bottom:20px;"><img src="{gambar}" alt="{judul}" style="max-width:100%; height:auto;"></div>' if gambar else ""
            konten_lengkap = f"{img_tag}\n{ADSTERRA_CODE}\n{html_teks}\n{ADSTERRA_CODE}"

            try:
                cursor.execute(
                    "INSERT INTO artikel (link, judul, konten, gambar, tanggal_publish, tanggal_update) VALUES (?, ?, ?, ?, ?, ?)",
                    (link, judul, konten_lengkap, gambar, tanggal, datetime.now().isoformat())
                )
                conn.commit()
                baru += 1
            except sqlite3.IntegrityError:
                duplikat += 1
            except Exception:
                pass
                
        conn.close()
    except Exception:
        pass
        
    return baru, duplikat

# ==========================================================================
# MAIN
# ==========================================================================
if __name__ == "__main__":
    print("=" * 50)
    print("MULAI IMPORT KE DATABASE (TANPA EXPORT)")
    print("=" * 50)
    
    if not os.path.exists(RSS_FILE):
        print(f"[ERROR] File {RSS_FILE} tidak ditemukan!")
        exit(1)
        
    inisialisasi_database()
    
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        rss_urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        
    print(f"[*] Memproses {len(rss_urls)} sumber RSS...")
    
    total_baru, total_duplikat = 0, 0
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(proses_feed, url): url for url in rss_urls}
        for future in as_completed(futures):
            b, d = future.result()
            total_baru += b
            total_duplikat += d

    print("\n" + "="*45)
    print(f"[SELESAI]")
    print(f" - Artikel Baru disimpan : {total_baru}")
    print(f" - Dilewati (Duplikat)   : {total_duplikat}")
    print("="*45)
