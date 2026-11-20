"""Zamanı gelen Instagram gönderilerini Instagram Graph API ile yayınlar.

GitHub Actions'ta zamanlanmış olarak çalışır; yalnız standart kütüphane.
Ortam değişkenleri:
  IG_TOKEN     sayfa erişim tokeni (GitHub secret)
  IG_USER_ID   Instagram işletme hesabı kimliği
  GORSEL_KOK   görsellerin herkese açık adres kökü, sonu "/" ile
Dosyalar (depo kökü): gonderiler.json (plan), durum.json (yayınlananlar)
Kullanım:
  python araclar/yayinla.py               zamanı gelenleri yayınlar
  python araclar/yayinla.py --kuru        sıradaki gönderinin konteynerini kurar, yayınlamaz
  python araclar/yayinla.py --en-cok 3    bir çalışmada en çok 3 gönderi (varsayılan 2)
  python araclar/yayinla.py --saat-disi   09:00-23:00 dışında da yayınlar
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://graph.facebook.com/v27.0/"
TR = timezone(timedelta(hours=3))
KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(KOK, "gonderiler.json")
DURUM = os.path.join(KOK, "durum.json")
YAYIN_SAATI = (9, 23)            # TR saatiyle bu aralık dışında birikmiş gönderi atılmaz
GECICI_KODLAR = {1, 2, 4, 17, 32, 341, 613}


class ApiHatasi(Exception):
    def __init__(self, mesaj: str, gecici: bool = False):
        super().__init__(mesaj)
        self.gecici = gecici


def api(yontem: str, yol: str, token: str, tekrar: bool = True, **alanlar) -> dict:
    """tekrar=False: yayınlama gibi iki kez yapılmaması gereken istekte yeniden deneme yok."""
    alanlar["access_token"] = token
    veri = urllib.parse.urlencode(alanlar)
    if yontem == "GET":
        istek = urllib.request.Request(API + yol + "?" + veri)
    else:
        istek = urllib.request.Request(API + yol, data=veri.encode(), method="POST")
    for deneme in range(4):
        try:
            with urllib.request.urlopen(istek, timeout=60) as yanit:
                return json.load(yanit)
        except urllib.error.HTTPError as h:
            try:
                hata = json.load(h).get("error", {})
            except ValueError:
                hata = {}
            gecici = h.code >= 500 or hata.get("is_transient") or hata.get("code") in GECICI_KODLAR
            mesaj = f"{yontem} {yol}: HTTP {h.code} kod {hata.get('code')}/{hata.get('error_subcode')} {hata.get('message', '')}"
            if hata.get("error_user_msg"):
                mesaj += " | " + hata["error_user_msg"]
            if not gecici or not tekrar or deneme == 3:
                raise ApiHatasi(mesaj, bool(gecici)) from None
            print("geçici hata, yeniden denenecek:", mesaj)
        except (urllib.error.URLError, TimeoutError) as h:
            if not tekrar or deneme == 3:
                raise ApiHatasi(f"{yontem} {yol}: bağlantı hatası {h}", True) from None
            print("bağlantı hatası, yeniden denenecek:", h)
        time.sleep(15 * (deneme + 1))
    raise AssertionError("ulaşılmaz")


def hazir_bekle(konteyner: str, token: str) -> None:
    for _ in range(36):
        yanit = api("GET", konteyner, token, fields="status_code,status")
        durum = yanit.get("status_code")
        if durum == "FINISHED":
            return
        if durum in ("ERROR", "EXPIRED"):
            raise ApiHatasi(f"konteyner {konteyner} {durum}: {yanit.get('status')}")
        time.sleep(5)
    raise ApiHatasi(f"konteyner {konteyner} 3 dakikada hazır olmadı", True)


def konteyner_kur(g: dict, ig: str, token: str, gorsel_kok: str) -> str:
    adresler = [gorsel_kok + yol for yol in g["gorseller"]]
    if len(adresler) == 1:
        kimlik = api("POST", f"{ig}/media", token, image_url=adresler[0], caption=g["aciklama"])["id"]
    else:
        cocuklar = []
        for adres in adresler:
            cocuk = api("POST", f"{ig}/media", token, image_url=adres, is_carousel_item="true")["id"]
            hazir_bekle(cocuk, token)
            cocuklar.append(cocuk)
        kimlik = api("POST", f"{ig}/media", token, media_type="CAROUSEL",
                     children=",".join(cocuklar), caption=g["aciklama"])["id"]
    hazir_bekle(kimlik, token)
    return kimlik


def sade(metin: str) -> str:
    return " ".join((metin or "").split())


def son_paylasimlar(ig: str, token: str) -> dict:
    """Hesaptaki son gönderilerin açıklama -> medya kimliği eşlemesi (çift paylaşımı önler)."""
    veri = api("GET", f"{ig}/media", token, fields="id,caption,timestamp", limit="50").get("data", [])
    return {sade(m.get("caption")): m["id"] for m in veri if m.get("caption")}


def oku(yol: str, bos):
    if not os.path.exists(yol):
        return bos
    with open(yol, encoding="utf-8") as f:
        return json.load(f)


def guncelle() -> None:
    """Actions'ta depoyu main'in son hâline getirir.

    Kuyrukta bekleyen çalışma, tetiklendiği anın commit'ini alır; önceki çalışma
    o arada durum.json'a yazıp push ettiyse onu görmez, aynı gönderiyi yeniden
    dener ve durum.json çakışmasıyla kırmızı biter (18.11 ve 20.11).
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        subprocess.run(["git", "-C", KOK, "pull", "--ff-only", "-q"], check=True)


def kaydet(durum: dict) -> None:
    gecici = DURUM + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(durum, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(gecici, DURUM)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kuru", action="store_true")
    ap.add_argument("--en-cok", type=int, default=2)
    ap.add_argument("--saat-disi", action="store_true")
    a = ap.parse_args()

    token = os.environ.get("IG_TOKEN", "").strip()
    ig = os.environ.get("IG_USER_ID", "").strip()
    gorsel_kok = os.environ.get("GORSEL_KOK", "").strip()
    if not (token and ig and gorsel_kok.endswith("/")):
        print("IG_TOKEN, IG_USER_ID ve sonu / ile biten GORSEL_KOK gerekli")
        return 2

    guncelle()
    plan = oku(PLAN, [])
    durum = oku(DURUM, {})
    simdi = datetime.now(TR)
    bekleyen = [g for g in plan if str(g["sira"]) not in durum]
    print(f"{simdi:%d.%m.%Y %H:%M} TR · plan {len(plan)} · yayınlanan {len(durum)} · kalan {len(bekleyen)}")

    hesap = api("GET", ig, token, fields="username,media_count")
    print(f"hesap @{hesap.get('username')} · gönderi sayısı {hesap.get('media_count')}")

    if a.kuru:
        if not bekleyen:
            print("kuru deneme: bekleyen gönderi yok")
            return 0
        g = bekleyen[0]
        kimlik = konteyner_kur(g, ig, token, gorsel_kok)
        print(f"kuru deneme tamam: sıra {g['sira']} ({g['urun']}), {len(g['gorseller'])} görsel, "
              f"konteyner {kimlik} FINISHED, yayınlanmadı (24 saatte kendiliğinden düşer)")
        return 0

    zamani_gelen = [g for g in bekleyen if datetime.fromisoformat(g["zaman"]) <= simdi]
    if not zamani_gelen:
        print("zamanı gelen gönderi yok")
        return 0
    if not a.saat_disi and not (YAYIN_SAATI[0] <= simdi.hour < YAYIN_SAATI[1]):
        print(f"{len(zamani_gelen)} gönderi bekliyor ama yayın saati dışında, sabah atılacak")
        return 0

    onceki = son_paylasimlar(ig, token)
    hata_var = False
    for g in zamani_gelen[: a.en_cok]:
        anahtar = str(g["sira"])
        mevcut = onceki.get(sade(g["aciklama"]))
        if mevcut:
            durum[anahtar] = {"media_id": mevcut, "zaman": simdi.isoformat(timespec="seconds"), "not": "hesapta zaten vardı"}
            kaydet(durum)
            print(f"sıra {anahtar}: hesapta zaten var ({mevcut}), işaretlendi")
            continue
        try:
            konteyner = konteyner_kur(g, ig, token, gorsel_kok)
            medya = api("POST", f"{ig}/media_publish", token, tekrar=False, creation_id=konteyner)["id"]
        except ApiHatasi as h:
            print(f"sıra {anahtar} ({g['urun']}) YAYINLANAMADI: {h}")
            hata_var = True
            break
        durum[anahtar] = {"media_id": medya, "zaman": datetime.now(TR).isoformat(timespec="seconds")}
        kaydet(durum)
        print(f"sıra {anahtar} ({g['urun']}) yayınlandı: medya {medya}, plan {g['zaman']}")
    kalan = len(zamani_gelen) - a.en_cok
    if kalan > 0 and not hata_var:
        print(f"{kalan} gönderi daha zamanı geçmiş bekliyor, sonraki çalışmada atılacak")
    return 1 if hata_var else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ApiHatasi as h:
        print("HATA:", h)
        sys.exit(1)
