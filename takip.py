"""Müzik yarışması ve Erasmus+/eTwinning duyuru takibi.

Kaynak sitelerdeki duyuruları okur, ortaokul müzik öğretmenini ilgilendirenleri
süzer, yeni olanları ntfy ile telefona bildirir. Son başvuru tarihi bulunan
duyuruları yarismalar.ics takvimine ekler.
"""
import html
import http.client
import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

TSI = timezone(timedelta(hours=3))
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
STATE = "durum.json"
FOUND = "bulunanlar.md"
ICS = "yarismalar.ics"
MAX_DETAIL_FETCHES = 25

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "tr-TR,tr;q=0.9"}

ILCELER = ["osmangazi", "nilufer", "yildirim", "inegol", "gemlik", "mudanya", "mustafakemalpasa", "karacabey",
           "orhangazi", "gursu", "kestel", "yenisehir", "iznik", "orhaneli", "keles", "buyukorhan", "harmancik"]

# (ad, adres, tür). "meb": MEB sitelerinin ortak /icerik/<no>/ yapısı.
SOURCES = [("Bursa İl MEM", "https://bursa.meb.gov.tr/", "meb"),
           ("Temel Eğitim GM", "https://tegm.meb.gov.tr/", "meb"),
           ("YEĞİTEK", "https://yegitek.meb.gov.tr/", "meb"),
           ("Ulusal Ajans", "https://www.ua.gov.tr/", "ua"),
           ("Ulusal Ajans teklif çağrıları", "https://www.ua.gov.tr/anasayfa/icerikler/teklif-cagrilari/", "ua"),
           ("eTwinning", "https://etwinning.meb.gov.tr/feed/", "rss"),
           ("Bursa Büyükşehir", "https://www.bursa.bel.tr/", "bel")]
SOURCES += [(f"{i.capitalize()} İlçe MEM", f"https://{i}.meb.gov.tr/", "meb") for i in ILCELER]

# --- Süzgeç (metin önce Türkçe karakterlerden arındırılır) ---------------------
MUSIC = r"muzik|koro|sarki|turku|beste|orkestra|enstruman|calgi|baglama|keman|piyano|flut|ezgi|polifon|solfej|perkusyon|gitar|mehter|\bses yarismasi|\bmarsi(ni)? guzel okuma"
COMPETITION = r"yarisma|festival|senlik|secme|basvuru|katilim|cagri|son tarih|odul|olimpiyat"
COMPETITION_TITLE = r"yarisma|festival|senlik"
ERASMUS = r"erasmus|etwinning|\bka1\d\d|\bka2\d\d|akreditasyon|teklif cagri|hareketlilik|ulusal ajans"
CALL = r"basvuru|cagri|son tarih|teklif|akreditasyon|takvim"
NEWS_ONLY = r"tamamlandi|gerceklestirildi|ziyaret|cikarmasi|toplantisi|sonuclari|sonuc listesi|tamamlanan|odul toreni|kutlandi"
OTHER_LEVEL = r"\blise|ilkokul|okul oncesi|anaokul|universite|meslek|\bvet\b|\badu\b|yetiskin|genclik|spor alani|yuksekogretim"
OUR_LEVEL = r"ortaokul|temel egitim|okul egitimi|\bsch\b|tum kademe"
NOT_MUSIC_TITLE = r"siir|resim|kompozisyon|fotograf|hikaye|deneme|makale|afis|karikatur|bilgi yarismasi|cizgi"
NEVER = r"ihale|atama|norm kadro|nakil|kantin|ogretmenler arasi|ogretmen yarisma|personel alim|mulakat|yer degistirme"

TR_MAP = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛI", "cgiosuaiucgiosuaiui")
MONTHS = {"ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6, "temmuz": 7, "agustos": 8,
          "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12}


def norm(s):
    return " ".join(re.sub(r"['’`]", "", s).translate(TR_MAP).lower().split())


def has(pattern, text):
    return re.search(pattern, text) is not None


def classify(text):
    """'muzik', 'erasmus' ya da None. 'belki' = başlık yarışma diyor, gövdeye bakılmalı."""
    t = norm(text)
    if has(NEVER, t):
        return None
    if has(OTHER_LEVEL, t) and not has(OUR_LEVEL, t):
        return None
    if has(MUSIC, t) and has(COMPETITION, t):
        return "muzik"
    if has(ERASMUS, t) and has(CALL, t) and not has(NEWS_ONLY, t):
        return "erasmus"
    if has(COMPETITION_TITLE, t) and not has(NEWS_ONLY, t) and not has(NOT_MUSIC_TITLE, t):
        return "belki"
    return None


# --- Ağ --------------------------------------------------------------------------
_extra_ca = {}


def _fill_chain(host):
    """Eksik ara sertifikayı sunucu sertifikasındaki AIA adresinden indirir."""
    pem = ssl.get_server_certificate((host, 443), timeout=20)
    cert = x509.load_pem_x509_certificate(pem.encode())
    aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    pems = []
    for d in aia:
        if d.access_method != AuthorityInformationAccessOID.CA_ISSUERS:
            continue
        raw = urllib.request.urlopen(urllib.request.Request(d.access_location.value, headers=UA), timeout=20).read()
        try:
            c = x509.load_der_x509_certificate(raw)
        except ValueError:
            c = x509.load_pem_x509_certificate(raw)
        pems.append(c.public_bytes(Encoding.PEM).decode())
    _extra_ca[host] = "".join(pems)


def get(url, timeout=40):
    host = urlparse(url).hostname
    for attempt in range(2):
        ctx = ssl.create_default_context()
        if _extra_ca.get(host):
            ctx.load_verify_locations(cadata=_extra_ca[host])
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout, context=ctx) as r:
                charset = r.headers.get_content_charset() or "utf-8"
                try:
                    raw = r.read()
                except http.client.IncompleteRead as e:  # bazı siteler bağlantıyı erken kesiyor
                    raw = e.partial
                return raw.decode(charset, "replace")
        except Exception as e:
            if attempt == 0 and "CERTIFICATE_VERIFY_FAILED" in str(e) and host not in _extra_ca:
                _fill_chain(host)
                continue
            raise


def text_of(fragment):
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", fragment).split()))


# --- Duyuru listeleri ------------------------------------------------------------
def parse_links(body, base, href_pattern):
    """Aynı bağlantıya giden metinleri (başlık + özet) birleştirir."""
    items = {}
    for href, inner in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S | re.I):
        if not re.search(href_pattern, href):
            continue
        url = urljoin(base, html.unescape(href))
        t = text_of(inner)
        if len(t) < 6:
            continue
        it = items.setdefault(url, {"url": url, "title": t, "extra": ""})
        if t != it["title"] and t not in it["extra"]:
            it["extra"] += " " + t
    for it in items.values():
        it["title"] = re.sub(r"^\d{2} \w{3} \d{4} \d{2}:\d{2} ", "", it["title"])  # liste tarih ön eki
    return list(items.values())


def parse_rss(body):
    out = []
    for item in re.findall(r"<item>(.*?)</item>", body, re.S):
        def tag(n):
            m = re.search(rf"<{n}[^>]*>(.*?)</{n}>", item, re.S)
            return text_of(re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.S)) if m else ""
        if tag("link"):
            out.append({"url": tag("link"), "title": tag("title"), "extra": tag("description")[:600]})
    return out


def fetch_source(name, url, kind):
    try:
        body = get(url)
    except Exception as e:
        if kind == "meb" and ".meb.gov.tr" in url and "Name or service not known" in str(e):
            alt = url.replace(".meb.gov.tr", "16.meb.gov.tr")
            body = get(alt)
            url = alt
        else:
            raise
    if kind == "meb":
        items = parse_links(body, url, r"/icerik/\d+/")
        for it in items:
            m = re.search(r"/icerik/(\d+)/", it["url"])
            it["id"] = f'{urlparse(it["url"]).hostname}:{m.group(1)}'
    elif kind == "ua":
        items = parse_links(body, url, r"/haber/[^\"]+")
    elif kind == "rss":
        items = parse_rss(body)
    else:
        items = parse_links(body, url, r"^(https://www\.bursa\.bel\.tr)?/(haber|duyuru)/[a-z0-9-]+")
    for it in items:
        it.setdefault("id", it["url"].rstrip("/"))
        it["source"] = name
    return items


# --- Ayrıntı sayfası: gövde metni ve son başvuru tarihi --------------------------
def detail_text(it):
    body = text_of(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", get(it["url"]), flags=re.S | re.I))
    pos = body.find(it["title"][:40])
    return body[pos:pos + 5000] if pos >= 0 else body[:5000]


DATE_RE = r"(\d{1,2})[./](\d{1,2})[./](20\d\d)|(\d{1,2}) (ocak|subat|mart|nisan|mayis|haziran|temmuz|agustos|eylul|ekim|kasim|aralik) (20\d\d)"


def find_deadline(text, today):
    t = norm(text)
    for m in re.finditer(r"son basvuru|son gonderim|son teslim|son tarih|tarihine kadar|tarihleri arasinda", t):
        window = t[max(0, m.start() - 120):m.end() + 120]
        dates = []
        for d in re.finditer(DATE_RE, window):
            try:
                if d.group(1):
                    dates.append(date(int(d.group(3)), int(d.group(2)), int(d.group(1))))
                else:
                    dates.append(date(int(d.group(6)), MONTHS[d.group(5)], int(d.group(4))))
            except ValueError:
                pass
        dates = [d for d in dates if today <= d <= today + timedelta(days=400)]
        if dates:
            return max(dates)  # "x ile y tarihleri arasında" -> bitiş
    return None


# --- Bildirim, takvim, kayıt -------------------------------------------------------
def notify(title, message, url=None, priority=4, tags=("musical_note",)):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC yok, bildirim atlandı:", title)
        return
    payload = {"topic": NTFY_TOPIC, "title": title[:250], "message": message[:3500], "priority": priority,
               "tags": list(tags)}
    if url:
        payload["click"] = url
        payload["actions"] = [{"action": "view", "label": "Duyuruyu aç", "url": url}]
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def ics_escape(s):
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def fold(line):
    b = line.encode("utf-8")
    out = []
    while len(b) > 74:
        cut = 74
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(b[:cut])
        b = b[cut:]
    out.append(b)
    return "\r\n ".join(x.decode("utf-8") for x in out)


def write_ics(state, today):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//muzik-yarisma-takip//TR", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:Müzik Yarışmaları – Son Başvuru", "X-WR-TIMEZONE:Europe/Istanbul",
             "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    for key, rec in sorted(state["found"].items(), key=lambda kv: kv[1].get("deadline") or ""):
        if not rec.get("deadline"):
            continue
        d = date.fromisoformat(rec["deadline"])
        if d < today - timedelta(days=30):
            continue
        uid = re.sub(r"[^A-Za-z0-9]", "-", key)[-80:]
        lines += ["BEGIN:VEVENT", f"UID:{uid}@muzik-yarisma-takip", f"DTSTAMP:{rec['stamp']}",
                  f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
                  "TRANSP:TRANSPARENT", fold("SUMMARY:" + ics_escape("⏰ Son başvuru: " + rec["title"])),
                  fold("DESCRIPTION:" + ics_escape(f'{rec["source"]}\n{rec["url"]}')), fold("URL:" + rec["url"])]
        for trig, txt in (("-P6DT14H", "1 hafta kaldı: "), ("-PT14H", "Yarın son gün: ")):
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{trig}",
                      fold("DESCRIPTION:" + ics_escape(txt + rec["title"])), "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    with open(ICS, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


def write_found(state):
    rows = sorted(state["found"].values(), key=lambda r: r["seen"], reverse=True)
    out = ["# Bulunan duyurular", "", "En yeni üstte. Son başvuru tarihi duyuru metninden otomatik okunur, kontrol et.", "",
           "| Bulundu | Tür | Duyuru | Kaynak | Son başvuru |", "|---|---|---|---|---|"]
    for r in rows:
        title = r["title"].replace("|", "/")
        kind = "Müzik" if r["kind"] == "muzik" else "Erasmus+/eTwinning"
        out.append(f'| {r["seen"]} | {kind} | [{title}]({r["url"]}) | {r["source"]} | {r.get("deadline") or "–"} |')
    with open(FOUND, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"seen": {}, "found": {}}


def main():
    now = datetime.now(TSI)
    today = now.date()
    state = load_state()
    first_run = not state["seen"]

    items, failures = [], []
    for name, url, kind in SOURCES:
        try:
            got = fetch_source(name, url, kind)
            print(f"{name}: {len(got)} duyuru")
            items += got
        except Exception as e:
            failures.append(name)
            print(f"{name}: HATA {type(e).__name__}: {str(e)[:150]}")
    if len(failures) > len(SOURCES) // 2:
        sys.exit(f"Kaynakların çoğuna ulaşılamadı ({len(failures)}/{len(SOURCES)}); hiçbir şey değiştirilmedi.")

    # İlk çalıştırmada her şeyi değerlendir; yoksa eski duyurular sonraki saatlerde "yeni" diye bildirilir.
    new_hits, detail_budget = [], 300 if first_run else MAX_DETAIL_FETCHES
    for it in items:
        if it["id"] in state["seen"]:
            continue
        kind = classify(it["title"] + " " + it["extra"])
        body = ""
        if kind and detail_budget > 0:
            detail_budget -= 1
            try:
                body = detail_text(it)
            except Exception as e:
                print("  ayrıntı alınamadı:", it["url"], e)
            if kind == "belki":
                kind = "muzik" if has(MUSIC, norm(body)) and not has(NEVER, norm(body)) else None
        elif kind == "belki":
            continue  # bütçe bitti; bir sonraki çalıştırmada yeniden değerlendirilir
        state["seen"][it["id"]] = today.isoformat()
        if kind not in ("muzik", "erasmus"):
            continue
        deadline = find_deadline(it["title"] + " " + it["extra"] + " " + body, today)
        rec = {"title": it["title"], "url": it["url"], "source": it["source"], "kind": kind,
               "seen": today.isoformat(), "deadline": deadline.isoformat() if deadline else None,
               "stamp": now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}
        state["found"][it["id"]] = rec
        new_hits.append(rec)
        print("  YENİ:", kind, rec["title"], rec["deadline"])

    if first_run:
        lines = [f'• {r["title"]}' + (f' (son: {r["deadline"]})' if r["deadline"] else "") for r in new_hits[:8]]
        notify("Müzik yarışması takibi kuruldu ✅",
               f"{len(SOURCES) - len(failures)} kaynak izleniyor. Şu an sitelerde {len(new_hits)} ilgili duyuru var"
               + (":\n" + "\n".join(lines) if lines else ".") + "\nBundan sonra yalnızca yeni duyurular bildirilecek.",
               priority=3, tags=("white_check_mark",))
    else:
        for r in new_hits:
            msg = f'{r["source"]}' + (f'\nSon başvuru: {date.fromisoformat(r["deadline"]):%d.%m.%Y}' if r["deadline"] else "")
            if r["kind"] == "muzik":
                notify("🎵 " + r["title"], msg, r["url"], priority=5, tags=("musical_note", "loudspeaker"))
            else:
                notify("🇪🇺 " + r["title"], msg, r["url"], priority=4, tags=("airplane",))

    cutoff = (today - timedelta(days=400)).isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    write_found(state)
    write_ics(state, today)
    print(f"Bitti: {len(new_hits)} yeni ilgili duyuru, {len(failures)} kaynak hatası.")


if __name__ == "__main__":
    main()
