#!/usr/bin/env python3
"""Traite toutes les captures déposées dans captures/nouvelles/ (de la plus ancienne à la plus récente),
met à jour carte/ puis régénère la page dans docs/. Les images traitées vont dans captures/archive/,
celles dont le recalage est refusé vont dans captures/refusees/."""
import json, os, re, shutil, subprocess, sys, datetime
import numpy as np, cv2
from PIL import Image

ICI = os.path.dirname(os.path.abspath(__file__))
NOUV, ARCH, REFU = [os.path.join(ICI, 'captures', d) for d in ('nouvelles', 'archive', 'refusees')]
DEPOT, SITE = os.path.join(ICI, 'carte'), os.path.join(ICI, 'docs')
EXT = ('.png', '.jpg', '.jpeg', '.webp')


def lire_nom(f):
    stem = os.path.splitext(f)[0]
    m = re.match(r'^(\d{4}-\d{2}-\d{2})[_ -]+(.+)$', stem)
    if m:
        return m.group(2).strip(), m.group(1), False
    return stem, datetime.date.today().isoformat(), True      # date inconnue -> aujourd'hui, marquée « estimée »


def construire_site():
    j = json.load(open(os.path.join(DEPOT, 'carte.json'), encoding='utf-8'))
    m = j.pop('mosaique')
    im = np.array(Image.open(os.path.join(DEPOT, 'mosaique.png')).convert('RGBA'))
    ys, xs = np.where(im[..., 3] > 0)
    x0, x1, y0, y1 = max(xs.min() - 12, 0), xs.max() + 12, max(ys.min() - 12, 0), ys.max() + 12
    crop = im[y0:y1, x0:x1].astype(float)
    fond = np.array([196, 226, 247], float)          # RGB
    a = crop[..., 3:4] / 255
    rgb = (crop[..., :3] * a + fond * (1 - a)).astype(np.uint8)
    Image.fromarray(rgb).save(os.path.join(SITE, 'capture.jpg'), quality=85)
    j['capture'] = {'x': int(x0 + m['x']), 'y': int(y0 + m['y']), 'w': int(x1 - x0), 'h': int(y1 - y0)}
    json.dump(j, open(os.path.join(SITE, 'carte.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))


def main():
    imgs = sorted(f for f in os.listdir(NOUV) if f.lower().endswith(EXT))
    if not imgs:
        print('Aucune nouvelle capture.'); return 0
    items = sorted((lire_nom(f) + (f,) for f in imgs), key=lambda t: (t[1], t[3]))
    ok = 0
    for nom, date, est, f in items:
        src = os.path.join(NOUV, f)
        cmd = [sys.executable, '-I', os.path.join(ICI, 'maj_carte.py'), '--depot', DEPOT, '--image', src, '--nom', nom, '--date', date]
        if est:
            cmd.append('--date-estimee')
        r = subprocess.run(cmd, capture_output=True, text=True)
        try:
            log = json.loads(r.stdout[r.stdout.index('{'):r.stdout.rindex('}') + 1])
        except Exception:
            log = {}
        if r.returncode == 0 and log.get('statut') == 'OK':
            c = log['cases']; d = log['decalage_cases']
            print('OK      %s (%s, %s%s) : %d identiques sur %d comparées, %d nouvelles, %d confirmées, %d modifiées, %d non reconnues'
                  % (f, nom, date, ' estimée' if est else '', d['identiques'], d['cases_comparees'], c['nouvelles'], c['confirmees'], c['modifiees'], log.get('cases_non_reconnues', 0)))
            for t in log.get('nouveaux_terrains', []):
                print('        nouveau terrain détecté :', t)
            shutil.move(src, os.path.join(ARCH, f)); ok += 1
        else:
            print('REFUSÉ  %s : %s' % (f, (r.stderr.strip().splitlines() or ['erreur inconnue'])[-1]))
            shutil.move(src, os.path.join(REFU, f))
    if ok:
        construire_site()
        print('Page mise à jour (%d capture(s) intégrée(s)).' % ok)
    return 0


if __name__ == '__main__':
    sys.exit(main())
