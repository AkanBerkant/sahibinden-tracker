#!/usr/bin/env python3
"""
Sahibinden.com Scraper - nodriver ile Cloudflare bypass
Resim URL'leri dahil, JSON çıktı → GitHub Pages
"""

import asyncio
import nodriver as uc
from bs4 import BeautifulSoup
import json
import os
import re
import sys
from datetime import datetime

BASE = "https://www.sahibinden.com"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
JSON_PATH = os.path.join(DATA_DIR, "ilanlar.json")


def clean_price(text):
    if not text:
        return ""
    m = re.search(r'([\d.]+)\s*(TL|₺|USD|EUR|GBP)', text)
    return f"{m.group(1)} {m.group(2)}" if m else text.strip()


def parse_konum(text):
    parts = [p.strip() for p in text.split("/")]
    return (
        parts[0] if len(parts) > 0 else "",
        parts[1] if len(parts) > 1 else "",
        parts[2] if len(parts) > 2 else "",
    )


def parse_fiyat_sayi(text):
    if not text:
        return 0.0
    m = re.search(r'([\d.]+)', text)
    return float(m.group(1).replace(".", "")) if m else 0.0


async def bypass_sahibinden_check(pg):
    """Sahibinden 'Devam Et' + Turnstile checkbox otomatik geç"""
    for attempt in range(20):
        title = await pg.evaluate("document.title") or ""
        url = await pg.evaluate("window.location.href") or ""

        if "yükleniyor" not in title.lower() and "/cs/tloading" not in url:
            return True

        # 1) Turnstile checkbox'ını tıkla (iframe içinde)
        try:
            iframes = await pg.query_selector_all("iframe")
            for iframe in iframes:
                src = await iframe.get_attribute("src") or ""
                if "turnstile" in src or "challenge" in src or "cloudflare" in src:
                    print(f"  🔓 Turnstile iframe bulundu, tıklanıyor...")
                    # iframe'in ortasına tıkla
                    await iframe.click()
                    await asyncio.sleep(3)
                    break
        except Exception:
            pass

        # 2) Turnstile checkbox - JS ile iframe bul ve tıkla
        try:
            await pg.evaluate("""
                (() => {
                    const iframes = document.querySelectorAll('iframe');
                    for (const f of iframes) {
                        const src = f.src || '';
                        if (src.includes('turnstile') || src.includes('challenge') || src.includes('cloudflare')) {
                            f.click();
                            // iframe içine mouse event gönder
                            const rect = f.getBoundingClientRect();
                            const x = rect.left + rect.width / 2;
                            const y = rect.top + rect.height / 2;
                            const evt = new MouseEvent('click', {bubbles: true, clientX: x, clientY: y});
                            f.dispatchEvent(evt);
                            return true;
                        }
                    }
                    return false;
                })()
            """)
        except Exception:
            pass

        # 3) "Devam Et" butonunu tıkla
        try:
            btn = await pg.find("Devam Et", best_match=True, timeout=2)
            if btn:
                print(f"  🔓 'Devam Et' tıklanıyor...")
                await btn.click()
                await asyncio.sleep(3)
                continue
        except Exception:
            pass

        # 4) JS fallback - herhangi bir buton/link
        try:
            clicked = await pg.evaluate("""
                (() => {
                    const els = document.querySelectorAll('button, a.btn, input[type=submit], a[class*=button], a');
                    for (const e of els) {
                        if (e.textContent.trim().includes('Devam')) { e.click(); return true; }
                    }
                    return false;
                })()
            """)
            if clicked:
                await asyncio.sleep(3)
                continue
        except Exception:
            pass

        # 5) Mouse ile Turnstile alanına tıkla (koordinat bazlı)
        try:
            await pg.evaluate("""
                (() => {
                    const el = document.querySelector('[class*=turnstile], [id*=turnstile], [class*=challenge]');
                    if (el) { el.click(); return true; }
                    return false;
                })()
            """)
        except Exception:
            pass

        print(f"  ⏳ Bekleniyor... ({attempt+1})")
        await asyncio.sleep(2)

    return False


async def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    headless = "--headless" in sys.argv

    print(f"🚀 Sahibinden Scraper | {limit} ilan | headless={headless}\n")

    os.makedirs(DATA_DIR, exist_ok=True)

    browser = await uc.start(
        headless=headless,
        lang="tr-TR",
    )

    # Ana sayfa → bypass
    print("🌐 Ana sayfa açılıyor...")
    page = await browser.get(BASE)
    await asyncio.sleep(5)
    await bypass_sahibinden_check(page)
    title = await page.evaluate("document.title")
    print(f"✅ {title}\n")

    # Satılık daire listesi
    print("📋 İlan listesi açılıyor...")
    page = await browser.get(f"{BASE}/satilik-daire?sorting=date_desc")
    await asyncio.sleep(5)
    await bypass_sahibinden_check(page)

    html = await page.get_content()
    soup = BeautifulSoup(html, "html.parser")

    # İlan URL'lerini çek
    urls = []
    seen = set()
    for a in soup.find_all("a", href=re.compile(r"/ilan/")):
        href = a.get("href", "").split("?")[0]
        if href and href not in seen:
            seen.add(href)
            full = BASE + href if href.startswith("/") else href
            urls.append(full)
            if len(urls) >= limit:
                break

    print(f"📋 {len(urls)} ilan bulundu.\n")

    if not urls:
        print("❌ İlan bulunamadı!")
        browser.stop()
        return

    # Mevcut veriyi yükle (varsa)
    existing = {}
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            old_data = json.load(f)
            for item in old_data.get("ilanlar", []):
                existing[item.get("url")] = item

    # Her ilanın detayını çek
    details = []
    for i, url in enumerate(urls, 1):
        # Daha önce çekilmişse atla
        if url in existing:
            print(f"  [{i}/{len(urls)}] Zaten var, atlanıyor...")
            details.append(existing[url])
            continue

        print(f"  [{i}/{len(urls)}] Çekiliyor...")
        await asyncio.sleep(1.5)

        page = await browser.get(url)
        await asyncio.sleep(2)
        await bypass_sahibinden_check(page)

        detail_html = await page.get_content()
        s = BeautifulSoup(detail_html, "html.parser")

        # Başlık
        el = s.select_one("h1.classifiedDetailTitle") or s.select_one("h1")
        baslik = el.get_text(strip=True) if el else ""

        # Fiyat
        fiyat = ""
        price_h3 = s.select_one("div.classifiedInfo h3")
        if price_h3:
            span = price_h3.select_one("span")
            if span:
                fiyat = clean_price(span.get_text(strip=True))

        # Konum
        el = s.select_one("div.classifiedInfo h2")
        konum = el.get_text(strip=True) if el else ""
        il, ilce, mahalle = parse_konum(konum)

        # Detay bilgileri
        info = {}
        for item in s.select("ul.classifiedInfoList li"):
            strong = item.select_one("strong")
            span = item.select_one("span")
            if strong and span:
                key = strong.get_text(strip=True)
                val = span.get_text(strip=True)
                if key and val:
                    info[key] = val

        # Resim URL'leri
        images = []
        # Ana galeri resimleri
        for img in s.select("div.classifiedDetailMainPhoto img, div.galleryContainer img"):
            src = img.get("src") or img.get("data-src") or ""
            if src and "sahibinden" in src:
                images.append(src)
        # Küçük resimler (thumbnail'dan büyük versiyona çevir)
        for img in s.select("div.classifiedDetailPhotos img, ul.classifiedDetailThumbList img"):
            src = img.get("src") or img.get("data-src") or ""
            if src and "sahibinden" in src:
                # Küçük resmi büyük versiyona çevir
                src = src.replace("/Thumbs/", "/X5/").replace("/thumbs/", "/X5/")
                if src not in images:
                    images.append(src)
        # data-src attribute'ları
        for el in s.select("[data-src]"):
            src = el.get("data-src", "")
            if src and ("sahibinden" in src or "shbdn" in src) and src not in images:
                images.append(src)

        # Açıklama
        el = s.select_one("div#classifiedDescription")
        aciklama = el.get_text(strip=True)[:500] if el else ""

        d = {
            "ilan_no": info.get("İlan No", ""),
            "baslik": baslik,
            "fiyat": fiyat,
            "fiyat_sayi": parse_fiyat_sayi(fiyat),
            "konum": konum,
            "il": il,
            "ilce": ilce,
            "mahalle": mahalle,
            "emlak_tipi": info.get("Emlak Tipi", ""),
            "m2_brut": info.get("m² (Brüt)", ""),
            "m2_net": info.get("m² (Net)", ""),
            "oda_sayisi": info.get("Oda Sayısı", ""),
            "bina_yasi": info.get("Bina Yaşı", ""),
            "bulundugu_kat": info.get("Bulunduğu Kat", ""),
            "kat_sayisi": info.get("Kat Sayısı", ""),
            "isitma": info.get("Isıtma", ""),
            "banyo_sayisi": info.get("Banyo Sayısı", ""),
            "aidat": info.get("Aidat (TL)", ""),
            "images": images,
            "aciklama": aciklama,
            "url": url,
            "cekme_tarihi": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        details.append(d)
        print(f"  ✅ {baslik[:45]}")
        print(f"     💰 {fiyat}  |  📍 {konum}  |  📷 {len(images)} resim")

    # Eski verileri birleştir (yeni çekilenler + eskiler)
    all_urls = {d["url"] for d in details}
    for url, item in existing.items():
        if url not in all_urls:
            details.append(item)

    # JSON'a kaydet
    if details:
        output = {
            "son_guncelleme": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "toplam_ilan": len(details),
            "ilanlar": details
        }
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        print(f"\n{'='*50}")
        print(f"  🎉 {len(details)} ilan kaydedildi!")
        fiyatlar = [d["fiyat_sayi"] for d in details if d["fiyat_sayi"] > 0]
        if fiyatlar:
            print(f"  💰 Fiyat: {min(fiyatlar):,.0f} - {max(fiyatlar):,.0f} TL")
        total_imgs = sum(len(d.get("images", [])) for d in details)
        print(f"  📷 Toplam {total_imgs} resim URL'si")
        print(f"  📁 {JSON_PATH}")
        print(f"{'='*50}")
    else:
        print("❌ Hiç ilan çekilemedi!")

    browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
