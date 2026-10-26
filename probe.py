"""Aşama 0: kaynakların GitHub sunucularından erişilebilirliğini ve sayfa yapısını test eder."""
import re, ssl, urllib.request, html
URLS = [
  "https://bursa.meb.gov.tr/", "https://osmangazi.meb.gov.tr/", "https://nilufer.meb.gov.tr/",
  "https://yildirim.meb.gov.tr/", "https://inegol.meb.gov.tr/",
  "https://www.meb.gov.tr/", "https://www.meb.gov.tr/duyurular", "https://tegm.meb.gov.tr/", "https://yegitek.meb.gov.tr/",
  "https://www.ua.gov.tr/", "https://www.ua.gov.tr/duyurular/", "https://etwinning.meb.gov.tr/", "https://www.bursa.bel.tr/",
  "https://www.ktb.gov.tr/",
]
ctx = ssl.create_default_context()
for u in URLS:
    print("=" * 100); print(u)
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept-Language": "tr-TR,tr;q=0.9"})
        with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
            body = r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
            print("STATUS", r.status, "FINAL", r.geturl(), "LEN", len(body))
    except Exception as e:
        print("ERROR", type(e).__name__, e); continue
    t = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    print("TITLE", html.unescape(t.group(1).strip()) if t else None)
    for m in re.finditer(r'<link[^>]+(rss|atom)[^>]*>', body, re.I): print("FEED", m.group(0)[:200])
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S | re.I)
    shown = 0
    for href, txt in links:
        txt = re.sub(r"<[^>]+>", " ", txt); txt = html.unescape(" ".join(txt.split()))
        if re.search(r"duyuru|haber|icerik|announce|news|rss|etkinlik", href + " " + txt, re.I) and len(txt) > 3:
            print("  LINK", href[:150], "|", txt[:110]); shown += 1
            if shown >= 25: break
