"""
Erzeugt das PDF-Etikett zum Aufkleben auf Transport-Boxen.
Inhalt: Kautionsregelung, Videobeweis-Hinweis, Schäden, Kontakt.
Format A5 hochkant, randbeschnittsicher.
"""
import json, os
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.pdfgen import canvas

BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = json.load(open(os.path.join(BASE, 'settings.json'), encoding='utf-8'))

# Farben
ORANGE      = HexColor('#ff5a1f')
ORANGE_DARK = HexColor('#c2410c')
GELB        = HexColor('#fff8e7')
GELB_RAND   = HexColor('#facc15')
ROT         = HexColor('#dc2626')
ROT_HELL    = HexColor('#fee2e2')
GRUEN       = HexColor('#16a34a')
GRUEN_HELL  = HexColor('#dcfce7')
DUNKEL      = HexColor('#1e293b')
GRAU        = HexColor('#475569')
HELLGRAU    = HexColor('#f8fafc')
LINIE       = HexColor('#e2e8f0')

W, H = A5  # 148 x 210 mm
MARGIN = 8 * mm
INNER_W = W - 2 * MARGIN

def euro(n):
    return f'{n} €'

def round_rect(c, x, y, w, h, r, fill_color=None, stroke_color=None, stroke_width=0):
    if fill_color is not None:
        c.setFillColor(fill_color)
    if stroke_color is not None:
        c.setStrokeColor(stroke_color)
        c.setLineWidth(stroke_width)
    c.roundRect(x, y, w, h, r, fill=1 if fill_color else 0, stroke=1 if stroke_color else 0)

def make_pdf(path):
    c = canvas.Canvas(path, pagesize=A5)
    c.setTitle('HupfGaudi Vilshofen - Box-Etikett Kaution & Videobeweis')

    # ─────────────────────────────────────
    # HEADER mit Verlauf-Effekt
    # ─────────────────────────────────────
    c.setFillColor(ORANGE)
    c.rect(0, H - 24*mm, W, 24*mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont('Helvetica-Bold', 18)
    c.drawCentredString(W/2, H - 11*mm, 'HupfGaudi™ Vilshofen')
    c.setFont('Helvetica', 9)
    c.drawCentredString(W/2, H - 17*mm, 'Wichtige Hinweise zur Kaution & Rückgabe')
    # Akzent-Linie
    c.setFillColor(GELB_RAND)
    c.rect(0, H - 25*mm, W, 1*mm, fill=1, stroke=0)

    y = H - 31*mm

    # ─────────────────────────────────────
    # KAUTION-BOX (gelb)
    # ─────────────────────────────────────
    box_h = 28*mm
    round_rect(c, MARGIN, y - box_h, INNER_W, box_h, 3*mm, GELB, GELB_RAND, 1)
    c.setFillColor(DUNKEL)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(MARGIN + 4*mm, y - 6*mm, '💰  Kaution (in bar bei Abholung)')
    # Beträge in Spalten
    c.setFont('Helvetica', 8.5)
    c.setFillColor(GRAU)
    c.drawString(MARGIN + 4*mm, y - 11*mm, 'Hüpfburg')
    c.drawString(W/2 + 2*mm, y - 11*mm, 'Partyzubehör')
    c.setFont('Helvetica-Bold', 16)
    c.setFillColor(ORANGE_DARK)
    c.drawString(MARGIN + 4*mm, y - 18*mm, euro(SETTINGS.get('kautionHupfburg', 150)))
    c.drawString(W/2 + 2*mm, y - 18*mm, euro(SETTINGS.get('kautionPartyzubehoer', 50)))
    c.setFillColor(GRAU)
    c.setFont('Helvetica-Oblique', 7.5)
    c.drawString(MARGIN + 4*mm, y - 24*mm,
                 'Sofortige Rückerstattung nach Videobeweis bei sauberer Rückgabe.')
    y -= box_h + 4*mm

    # ─────────────────────────────────────
    # VIDEOBEWEIS (rot, prominent)
    # ─────────────────────────────────────
    box_h = 44*mm
    round_rect(c, MARGIN, y - box_h, INNER_W, box_h, 3*mm, ROT_HELL, ROT, 1.2)
    # Roter Header-Streifen oben
    c.setFillColor(ROT)
    c.roundRect(MARGIN, y - 8*mm, INNER_W, 8*mm, 3*mm, fill=1, stroke=0)
    c.rect(MARGIN, y - 8*mm, INNER_W, 4*mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(MARGIN + 3*mm, y - 5.5*mm, '📹  VIDEOBEWEIS BEI RÜCKGABE PFLICHT')
    # Inhalt
    c.setFillColor(DUNKEL)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(MARGIN + 4*mm, y - 13.5*mm, 'Bitte filme die Hüpfburg beim Abbau!')
    c.setFont('Helvetica', 8.5)
    c.setFillColor(GRAU)
    c.drawString(MARGIN + 4*mm, y - 19*mm, '✓  Sauberkeit, Trockenheit & Vollständigkeit zeigen')
    c.drawString(MARGIN + 4*mm, y - 23.5*mm, '✓  Nachweis, dass die Burg sauber zurückkommt')
    # Trennlinie
    c.setStrokeColor(ROT)
    c.setLineWidth(0.5)
    c.line(MARGIN + 4*mm, y - 27*mm, W - MARGIN - 4*mm, y - 27*mm)
    # Konsequenzen
    c.setFillColor(ROT)
    c.setFont('Helvetica-Bold', 8.5)
    c.drawString(MARGIN + 4*mm, y - 31*mm, '⚠  Ohne Videobeweis:')
    c.setFillColor(DUNKEL)
    c.setFont('Helvetica', 8.5)
    c.drawString(MARGIN + 4*mm, y - 35.5*mm, '• Kaution wird vollständig einbehalten')
    c.drawString(MARGIN + 4*mm, y - 40*mm, '• Reinigungs- & Schadenspauschalen kommen on top')
    y -= box_h + 4*mm

    # ─────────────────────────────────────
    # PAUSCHALEN (Tabelle)
    # ─────────────────────────────────────
    rh_min = SETTINGS.get('reinigungHupfburg', 50)
    rh_max = SETTINGS.get('reinigungHupfburgMax', 100)
    items = [
        ('Starke Verschmutzung (Matsch/Essen/Getränke)', f'{rh_min} – {rh_max} €'),
        ('Trocknung (Hüpfburg nass durch Regen)', euro(SETTINGS.get('trocknungsgebuehr', 100))),
        ('Reinigung Partyzubehör',      euro(SETTINGS.get('reinigungPartyzubehoer', 25))),
        ('Kleine Schäden (< 5×5 cm)',   euro(SETTINGS.get('schadenKleinBis5x5', 150))),
        ('Hüpfburg irreparabel (je nach Größe)',
         f"{SETTINGS.get('schadenHupfburgIrreparabel', 1500):,} – {SETTINGS.get('schadenHupfburgIrreparabelMax', 5000):,} €".replace(',', '.')),
        ('Defektes Gebläse',            euro(SETTINGS.get('schadenGeblaese', 300))),
        ('Verspätete Rückgabe (pro h)', euro(SETTINGS.get('verspaetungProStunde', 20))),
    ]
    box_h = 8*mm + len(items) * 4.5*mm + 3*mm
    round_rect(c, MARGIN, y - box_h, INNER_W, box_h, 3*mm, HELLGRAU, LINIE, 0.5)
    c.setFillColor(DUNKEL)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(MARGIN + 4*mm, y - 6*mm, '⚠️  Pauschalen bei Mängeln')
    row_y = y - 11*mm
    c.setFont('Helvetica', 8.5)
    for label, val in items:
        c.setFillColor(GRAU)
        c.drawString(MARGIN + 5*mm, row_y, '•  ' + label)
        c.setFillColor(DUNKEL)
        c.setFont('Helvetica-Bold', 8.5)
        c.drawRightString(W - MARGIN - 4*mm, row_y, val)
        c.setFont('Helvetica', 8.5)
        row_y -= 4.5*mm
    y -= box_h + 4*mm

    # ─────────────────────────────────────
    # TIPPS (grün)
    # ─────────────────────────────────────
    tipps = [
        'Trocken & sauber zurückgeben (kein Wasser, kein Sand)',
        'Keine Schuhe, spitze Gegenstände, kein Essen/Trinken',
        'Aufsicht durch Erwachsene · keine Kinder unter 3 Jahren',
        'Bei Wind ab 39 km/h (Beaufort 6) Betrieb sofort einstellen',
    ]
    box_h = 8*mm + len(tipps) * 4*mm + 2*mm
    round_rect(c, MARGIN, y - box_h, INNER_W, box_h, 3*mm, GRUEN_HELL, GRUEN, 0.5)
    c.setFillColor(GRUEN)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(MARGIN + 4*mm, y - 6*mm, '✅  So bleibt deine Kaution unangetastet')
    row_y = y - 11*mm
    c.setFillColor(DUNKEL)
    c.setFont('Helvetica', 8.5)
    for t in tipps:
        c.drawString(MARGIN + 5*mm, row_y, '✓  ' + t)
        row_y -= 4*mm
    y -= box_h + 3*mm

    # ─────────────────────────────────────
    # KONTAKT-FOOTER
    # ─────────────────────────────────────
    c.setFillColor(DUNKEL)
    c.rect(0, 0, W, 18*mm, fill=1, stroke=0)
    c.setFillColor(ORANGE)
    c.rect(0, 17*mm, W, 1*mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont('Helvetica-Bold', 9)
    c.drawCentredString(W/2, 12.5*mm, 'Bei Fragen oder Schäden – melde dich sofort:')
    c.setFont('Helvetica-Bold', 12)
    c.setFillColor(GELB_RAND)
    c.drawCentredString(W/2, 7*mm, '📞  0151 / 28861367')
    c.setFillColor(white)
    c.setFont('Helvetica', 8)
    c.drawCentredString(W/2, 2.5*mm, 'hupfgaudi@gmail.com  ·  www.hupfgaudi-vilshofen.de')

    c.showPage()
    c.save()
    print(f'PDF erstellt: {path}')

if __name__ == '__main__':
    make_pdf(os.path.join(BASE, 'box-etikett-kaution.pdf'))
