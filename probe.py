"""Aşama 0, test 2: eksik sertifika zincirini AIA ile tamamlama + liste sayfaları."""
import re, ssl, socket, urllib.request, html, time
from urllib.parse import urlparse
from cryptography import x509
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID
from cryptography.hazmat.primitives.serialization import Encoding

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept-Language": "tr-TR,tr;q=0.9"}
_extra = {}

def ctx_for(host):
    ctx = ssl.create_default_context()
    if host in _extra: ctx.load_verify_locations(cadata=_extra[host])
    return ctx

def fill_chain(host):
    pem = ssl.get_server_certificate((host, 443), timeout=20)
    cert = x509.load_pem_x509_certificate(pem.encode())
    aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    urls = [d.access_location.value for d in aia if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS]
    pems = []
    for u in urls:
        raw = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read()
        try: c = x509.load_der_x509_certificate(raw)
        except Exception: c = x509.load_pem_x509_certificate(raw)
        pems.append(c.public_bytes(Encoding.PEM).decode())
        print("   AIA intermediate:", c.subject.rfc4514_string()[:90], "from", u)
    _extra[host] = "".join(pems)

def get(u, timeout=40):
    host = urlparse(u).hostname
    for attempt in range(2):
        try:
            req = urllib.request.Request(u, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx_for(host)) as r:
                return r.status, r.geturl(), r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
        except Exception as e:
            if attempt == 0 and "CERTIFICATE_VERIFY_FAILED" in str(e) and host not in _extra:
                fill_chain(host); continue
            raise

def items(body):
    out, seen = [], set()
    for href, txt in re.findall(r'<a[^>]+href="([^"]*?/icerik/(\d+)/[^"]*)"[^>]*>(.*?)</a>', body, re.S | re.I) and [(m[0], m[2]) for m in re.findall(r'<a[^>]+href="([^"]*?/icerik/(\d+)/[^"]*)"[^>]*>(.*?)</a>', body, re.S | re.I)] or []:
        t = html.unescape(" ".join(re.sub(r"<[^>]+>", " ", txt).split()))
        if href in seen or len(t) < 8: continue
        seen.add(href); out.append((href, t))
    return out

URLS = [
  "https://bursa.meb.gov.tr/www/duyurular/kategori/2", "https://bursa.meb.gov.tr/www/haberler/kategori/1",
  "https://osmangazi.meb.gov.tr/", "https://yildirim.meb.gov.tr/", "https://inegol.meb.gov.tr/", "https://gemlik.meb.gov.tr/",
  "https://mudanya.meb.gov.tr/", "https://niluferilce.meb.gov.tr/", "https://nilufermem.meb.gov.tr/",
  "https://tegm.meb.gov.tr/", "https://yegitek.meb.gov.tr/", "https://www.meb.gov.tr/",
  "https://www.ua.gov.tr/", "https://www.ua.gov.tr/duyurular/",
  "https://etwinning.meb.gov.tr/feed/", "https://www.bursa.bel.tr/",
]
for u in URLS:
    print("=" * 90); print(u); t0 = time.time()
    try:
        st, fin, body = get(u, timeout=60 if "www.meb.gov.tr" in u else 40)
    except Exception as e:
        print("ERROR", type(e).__name__, str(e)[:200], f"({time.time()-t0:.0f}s)"); continue
    print("STATUS", st, "FINAL", fin, "LEN", len(body), f"({time.time()-t0:.0f}s)")
    t = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I); print("TITLE", html.unescape(t.group(1).strip())[:100] if t else None)
    its = items(body)
    print("ICERIK ITEMS", len(its))
    for href, txt in its[:12]: print("  *", href[:110], "|", txt[:110])
    if not its:
        for href, txt in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S | re.I)[:400]:
            tx = html.unescape(" ".join(re.sub(r"<[^>]+>", " ", txt).split()))
            if re.search(r"duyuru|haber|announce|news|item|<item|çağrı|cagri", href + tx, re.I) and len(tx) > 5: print("  L", href[:110], "|", tx[:100])
        for m in re.findall(r"<item>.*?<title>(.*?)</title>.*?<pubDate>(.*?)</pubDate>", body, re.S)[:8]: print("  RSS", m[1], "|", html.unescape(m[0])[:110])
