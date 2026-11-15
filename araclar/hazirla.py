"""plan.xlsx + görsel klasörlerinden yayın verisini hazırlar.

Kullanım: python hazirla.py <plan.xlsx> <kaynak klasörü> <çıktı kökü>
Çıktı: <çıktı kökü>/gorseller/<sıra>/<n>.jpg (4:5, JPEG) ve <çıktı kökü>/gonderiler.json
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import openpyxl
from PIL import Image, ImageFilter, ImageOps, ImageStat

TR = timezone(timedelta(hours=3))
ORAN = 3 / 4            # dik 3:4 (Yasin Bey 15.11 "4:3 yapın"); API belgede 4:5 diyor ama 3:4 konteyneri 15.11 denemede FINISHED
EN_GENIS = 1440         # API bundan genişini küçültüyor
DUZ_ZEMIN_STD = 6.0     # kenar şeridinin bu sapmanın altı düz zemin sayılır


def metni_temizle(metin: str) -> str:
    metin = metin.replace("Joy Ayakkabı imzalı bu modelde", "Bu modelde")
    metin = re.sub(r"(?<!\S)#joyayakkabı(?!\S)", "", metin)
    metin = re.sub(r"[ \t]{2,}", " ", metin)
    metin = "\n".join(satir.strip() for satir in metin.split("\n"))
    if re.search(r"joy\s*ayakkab|#joy", metin, re.I):
        raise ValueError("metinde Joy Ayakkabı kaldı: " + metin[:80])
    return metin.strip()


def zaman(tarih: str, saat: str) -> str:
    gun = datetime.strptime(tarih.split()[0] + " " + saat.strip(), "%d.%m.%Y %H:%M")
    return gun.replace(tzinfo=TR).isoformat()


def kenar_sapmasi(im: Image.Image) -> tuple[float, tuple[int, int, int]]:
    w, h = im.size
    serit = Image.new("RGB", (16, h))
    serit.paste(im.crop((0, 0, 8, h)), (0, 0))
    serit.paste(im.crop((w - 8, 0, w, h)), (8, 0))
    st = ImageStat.Stat(serit)
    return max(st.stddev), tuple(int(x) for x in st.median)


def dort_bes(yol: str) -> Image.Image:
    im = ImageOps.exif_transpose(Image.open(yol))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        zemin = Image.new("RGB", im.size, (255, 255, 255))
        zemin.paste(im, mask=im.split()[-1])
        im = zemin
    im = im.convert("RGB")
    w, h = im.size
    if w / h < ORAN:            # dikey ve 3:4ten uzun: yanlara boşluk
        tw, th = round(h * ORAN), h
    elif w / h > 1.91:          # çok yatay: üst-alta boşluk
        tw, th = w, round(w / 1.91)
    else:
        tw, th = w, h
    if (tw, th) != (w, h):
        sapma, renk = kenar_sapmasi(im)
        if sapma <= DUZ_ZEMIN_STD:
            tuval = Image.new("RGB", (tw, th), renk)
        else:
            olcek = max(tw / w, th / h)
            arka = im.resize((round(w * olcek) + 2, round(h * olcek) + 2), Image.LANCZOS)
            sx, sy = (arka.width - tw) // 2, (arka.height - th) // 2
            tuval = arka.crop((sx, sy, sx + tw, sy + th)).filter(ImageFilter.GaussianBlur(40))
        tuval.paste(im, ((tw - w) // 2, (th - h) // 2))
        im = tuval
    if im.width > EN_GENIS:
        im = im.resize((EN_GENIS, round(im.height * EN_GENIS / im.width)), Image.LANCZOS)
    return im


def main(plan_yolu: str, kaynak: str, cikti: str) -> None:
    klasorler = {}
    for kok, _, _ in os.walk(kaynak):
        ad = os.path.basename(kok)
        if re.match(r"\d{3}_", ad):
            if ad in klasorler:
                raise ValueError("aynı klasör iki yerde: " + ad)
            klasorler[ad] = kok

    sayfa = openpyxl.load_workbook(plan_yolu, read_only=True, data_only=True)["Plan"]
    satirlar = list(sayfa.iter_rows(values_only=True))
    baslik = satirlar[0]
    gonderiler = []
    for r in satirlar[1:]:
        if not any(r):
            continue
        d = dict(zip(baslik, r))
        sira = int(d["Sıra"])
        klasor = klasorler[os.path.basename(str(d["Klasör"]))]
        dosyalar = sorted(
            (f for f in os.listdir(klasor) if f.lower().endswith((".jpg", ".jpeg", ".png"))),
            key=lambda f: int(re.match(r"\d+", f).group()),
        )
        if len(dosyalar) != int(d["Görsel sayısı"]):
            raise ValueError(f"{sira}: görsel sayısı tutmuyor")
        hedef = os.path.join(cikti, "gorseller", f"{sira:03d}")
        os.makedirs(hedef, exist_ok=True)
        gorseller = []
        for i, f in enumerate(dosyalar, 1):
            ad = f"{i}.jpg"
            dort_bes(os.path.join(klasor, f)).save(os.path.join(hedef, ad), "JPEG", quality=90, optimize=True)
            gorseller.append(f"gorseller/{sira:03d}/{ad}")
        aciklama = metni_temizle(str(d["Açıklama"]))
        if len(aciklama) > 2200 or len(re.findall(r"#\w+", aciklama)) > 30:
            raise ValueError(f"{sira}: açıklama sınırı aşıyor")
        gonderiler.append({
            "sira": sira,
            "zaman": zaman(str(d["Tarih"]), str(d["Saat"])),
            "urun": d["Ürün adı"],
            "gorseller": gorseller,
            "aciklama": aciklama,
        })
    gonderiler.sort(key=lambda g: (g["zaman"], g["sira"]))
    with open(os.path.join(cikti, "gonderiler.json"), "w", encoding="utf-8") as f:
        json.dump(gonderiler, f, ensure_ascii=False, indent=1)
    print(len(gonderiler), "gönderi,", sum(len(g["gorseller"]) for g in gonderiler), "görsel")


if __name__ == "__main__":
    main(*sys.argv[1:4])
