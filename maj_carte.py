#!/usr/bin/env python3
"""
maj_carte.py : met à jour la carte (carte.json + mosaique.png) à partir d'une capture d'écran.

Usage :
  python maj_carte.py --depot DEPOT --image capture.png --nom "Map_Olaf" --date 2026-11-19 [--date-estimee]
                      [--etiquette "Nom de zone=x_px,y_px" ...]

Principe :
 1. estime la taille des hexagones de la capture (autocorrélation) et la ramène à l'échelle de référence ;
 2. la recale sur la mosaïque existante (vote de plusieurs zones, puis affinage échelle + décalage) ;
 3. lit la couleur de chaque case (grille « doubled » : +2 en y par case vers le bas) ;
 4. fusionne : case nouvelle -> ajoutée ; identique -> confirmée ; différente -> la plus récente gagne,
    l'ancienne valeur est gardée dans l'historique de la case ;
 5. met à jour la mosaïque (pixels des cases gagnantes) et écrit un rapport.
Si le recalage est douteux, rien n'est modifié.
"""
import argparse, json, os, sys, math
import numpy as np, cv2

S3 = math.sqrt(3)


def _jd(o):
    return o.item() if hasattr(o, 'item') else str(o)
UI_BGR = np.array([60, 47, 42])
BG_BGR = np.array([247, 226, 196], np.uint8)     # fond uni de la mosaïque
NEW_TERRAIN_MIN = 15       # nombre minimal de cases pour créer un nouveau terrain (sinon : bruit de bord)
NEW_TERRAIN_DIST = 24.0   # au-delà, la couleur n'est ni un terrain connu ni sa version hachurée
SAME_TERRAIN = 24.0       # tolérance de comparaison de couleurs pour le recalage


# ---------------------------------------------------------------- utilitaires image
def read_bgr(path):
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if im is None:
        raise SystemExit("image illisible : " + path)
    if im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
    return im[:, :, :3].copy()


def estimate_hex_height(im):
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32)
    H, W = g.shape
    vals = []
    for y in range(0, H - 120, 100):
        for x in range(0, W - 160, 120):
            w = g[y:y + 120, x:x + 160]
            if w.std() < 8:
                continue
            hp = w - cv2.GaussianBlur(w, (0, 0), 2.5)
            F = np.fft.fft2(hp)
            ac = np.fft.ifft2(F * np.conj(F)).real
            ac /= ac[0, 0]
            p = ac[:, 0][:60]
            best = None
            for i in range(6, 31):
                if p[i] > p[i - 1] and p[i] >= p[i + 1] and (best is None or p[i] > p[best]):
                    best = i
            if best and p[best] > 0.12:
                a, b, c = p[best - 1], p[best], p[best + 1]
                vals.append(best + 0.5 * (a - c) / (a - 2 * b + c))
    if len(vals) < 5:
        raise SystemExit("impossible d'estimer la taille des hexagones (trop peu de zones avec grille)")
    vals = np.array(vals)
    hist, edges = np.histogram(vals, bins=np.arange(5, 32, 0.5))
    mode = edges[np.argmax(hist)] + 0.25
    keep = vals[np.abs(vals - mode) < 0.15 * mode]
    return float(np.median(keep)), len(keep), len(vals)


def ui_mask(im):
    """Boutons / pastilles de l'interface (bleu nuit uniforme) : rectangles englobants agrandis."""
    d = (np.abs(im.astype(int) - UI_BGR).max(axis=2) <= 10).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(d, connectivity=8)
    out = np.zeros(d.shape, np.uint8)
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a > 350 and w > 25 and h > 15:
            cv2.rectangle(out, (x - 10, y - 10), (x + w + 10, y + h + 10), 1, -1)
    return out.astype(bool)


def land_core(im, valid):
    """Masque des îles (cases), sans le ciel ni l'interface. im : BGR à l'échelle de référence."""
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    h, s, v = [hsv[..., i] for i in range(3)]
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, np.ones((5, 5), np.uint8))
    nonsky = ~((h > 95) & (h < 110) & (s < 110) & (v > 180))
    seed = (nonsky | (bh > 10)) & valid
    seed[:3, :] = False
    core = cv2.morphologyEx(seed.astype(np.uint8), cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
    big = np.zeros(n, bool)
    big[1:] = stats[1:, cv2.CC_STAT_AREA] > 1500
    keep = big[lab]
    # trous < 4000 px remplis
    inv = (~keep).astype(np.uint8)
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    fill = np.zeros(n2, bool)
    for i in range(1, n2):
        x, y, w, hh, a = st2[i]
        touches = x == 0 or y == 0 or x + w >= keep.shape[1] or y + hh >= keep.shape[0]
        fill[i] = (a < 4000) and not touches
    keep = keep | fill[lab2]
    keep = cv2.erode(keep.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2).astype(bool)
    return keep & valid


# ---------------------------------------------------------------- dépôt (état)
class Depot:
    def __init__(self, path):
        self.path = path
        self.j = json.load(open(os.path.join(path, 'carte.json'), encoding='utf-8'))
        m = cv2.imread(os.path.join(path, 'mosaique.png'), cv2.IMREAD_UNCHANGED)
        self.mos = m[:, :, :3].copy()
        self.alpha = m[:, :, 3].copy()
        L = self.j['lattice']
        self.ox, self.oy, self.sy = L['ox'], L['oy'], L['sy']
        self.sx = S3 * self.sy
        self.R = 2 * self.sy / S3
        mo = self.j['mosaique']
        self.mx0, self.my0 = mo['x'], mo['y']
        self.cells = {(c[0], c[1]): c for c in self.j['cells']}

    @property
    def h_ref(self):
        return 2 * self.sy

    def center(self, x, y):
        return self.ox + self.sx * x, self.oy + self.sy * y

    def save(self):
        self.j['cells'] = sorted(self.cells.values(), key=lambda c: (c[1], c[0]))
        mo = self.j['mosaique']
        mo['w'], mo['h'] = int(self.mos.shape[1]), int(self.mos.shape[0])
        json.dump(self.j, open(os.path.join(self.path, 'carte.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, separators=(',', ':'), default=_jd)
        rgba = np.dstack([self.mos, self.alpha])
        cv2.imwrite(os.path.join(self.path, 'mosaique.png'), rgba)


# ---------------------------------------------------------------- recalage
def composite_ref(dep):
    ref = dep.mos.copy()
    ref[dep.alpha == 0] = BG_BGR
    return ref


def coarse_vote(new_s, land_s, ref, ref_alpha, log):
    """Vote de plusieurs fenêtres de la capture (déjà à l'échelle) cherchées dans la mosaïque."""
    ys, xs = np.where(ref_alpha > 0)
    if len(ys) == 0:
        raise SystemExit("mosaïque vide")
    m = 220
    cx0, cy0 = max(xs.min() - m, 0), max(ys.min() - m, 0)
    cx1, cy1 = min(xs.max() + m, ref.shape[1]), min(ys.max() + m, ref.shape[0])
    sub = ref[cy0:cy1, cx0:cx1]
    refb = cv2.GaussianBlur(sub, (0, 0), 1.5)
    refh = cv2.resize(refb, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA).astype(np.float32)
    newb = cv2.GaussianBlur(new_s, (0, 0), 1.5)
    newh = cv2.resize(newb, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA).astype(np.float32)
    landh = cv2.resize(land_s.astype(np.uint8), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_NEAREST)
    P = 80
    cand = []
    for y in range(0, newh.shape[0] - P, 50):
        for x in range(0, newh.shape[1] - P, 50):
            mk = landh[y:y + P, x:x + P]
            if mk.mean() < 0.75:
                continue
            t = newh[y:y + P, x:x + P]
            sd = float(t[mk > 0].std(axis=0).mean())
            if sd < 22:
                continue
            cand.append((sd, x, y))
    cand.sort(reverse=True)
    votes = []
    for sd, x, y in cand[:14]:
        t = newh[y:y + P, x:x + P]
        mk = np.repeat(landh[y:y + P, x:x + P][..., None].astype(np.float32), 3, axis=2)
        res = cv2.matchTemplate(refh, t, cv2.TM_SQDIFF, mask=mk)
        mn, _, loc, _ = cv2.minMaxLoc(res)
        score = mn / (mk.sum() + 1e-9)
        # translation (coord. tableau mosaïque) : position dans réf - position dans capture
        tx = loc[0] * 2 + cx0 - x * 2
        ty = loc[1] * 2 + cy0 - y * 2
        votes.append((score, tx, ty, x * 2 + P, y * 2 + P))
    if not votes:
        raise SystemExit("recalage impossible : aucune zone exploitable dans la capture")
    good = [v for v in votes if v[0] < 900]
    log['votes'] = [(round(v[0]), v[1], v[2]) for v in votes]
    if len(good) < 2:
        raise SystemExit("recalage douteux : moins de 2 zones concordantes (meilleurs scores : %s)" %
                         [round(v[0]) for v in votes[:5]])
    best = None
    for g1 in good:
        grp = [g for g in good if abs(g[1] - g1[1]) <= 4 and abs(g[2] - g1[2]) <= 4]
        if best is None or len(grp) > len(best):
            best = grp
    if len(best) < 2:
        raise SystemExit("recalage douteux : les zones ne s'accordent pas sur un même décalage")
    tx = float(np.median([g[1] for g in best])); ty = float(np.median([g[2] for g in best]))
    uc = float(np.mean([g[3] for g in best])); vc = float(np.mean([g[4] for g in best]))
    log['accord'] = '%d zones sur %d' % (len(best), len(votes))
    return tx, ty, uc, vc


def warp_to(img, s, a, b, W, H, interp=None):
    M = np.array([[s, 0, a], [0, s, b]], np.float32)
    if interp is None:
        interp = cv2.INTER_CUBIC if s > 1 else cv2.INTER_LINEAR
    return cv2.warpAffine(img, M, (W, H), flags=interp, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def pixel_agreement(dep, img, land0, s, a, b):
    """Contrôle indépendant : part des pixels (îles communes) dont la couleur concorde avec la mosaïque."""
    H, W = dep.mos.shape[:2]
    refb = cv2.GaussianBlur(composite_ref(dep), (0, 0), 1.5).astype(np.float32)
    imgb = cv2.GaussianBlur(img, (0, 0), 1.5)
    wi = warp_to(imgb, s, a, b, W, H).astype(np.float32)
    wl = warp_to(land0.astype(np.uint8), s, a, b, W, H, cv2.INTER_NEAREST) > 0
    ov = wl & (dep.alpha > 0)
    n = int(ov.sum())
    if n < 500:
        return 0, 0.0, 0.0
    d = np.abs(wi[ov] - refb[ov]).mean(axis=1)
    return n, float((d < 30).mean()), float(d.mean())


# ---------------------------------------------------------------- grille propre à la capture
def _lattice_points(shape, sy, ox, oy, margin=10):
    """Centres (ox,oy,sy) de la grille doubled couvrant l'image : i, j (i+j pair), px, py."""
    H, W = shape
    sx = S3 * sy
    i0, i1 = int(math.floor((-margin - ox) / sx)) - 1, int(math.ceil((W + margin - ox) / sx)) + 1
    j0, j1 = int(math.floor((-margin - oy) / sy)) - 1, int(math.ceil((H + margin - oy) / sy)) + 1
    I, J = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing='ij')
    m = ((I + J) % 2 == 0)
    I, J = I[m], J[m]
    return I, J, ox + sx * I, oy + sy * J


def fit_lattice(img_s, keep, sy0, log):
    """Pas (sy) et phase de la grille, mesurés sur les traits sombres entre hexagones. img_s : à l'échelle ~ référence."""
    g = cv2.cvtColor(img_s, cv2.COLOR_BGR2GRAY).astype(np.float32)
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    bh = np.minimum(bh, 40)
    H, W = g.shape
    keep_e = cv2.erode(keep.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=6) > 0
    mids = np.array([[0, 1], [0, -1], [S3 / 2, .5], [S3 / 2, -.5], [-S3 / 2, .5], [-S3 / 2, -.5]])
    ins = 0.55 * 2 / S3
    inner = np.array([[ins * math.cos(math.pi / 3 * k), ins * math.sin(math.pi / 3 * k)] for k in range(6)])

    def score(sy, ox, oy):
        I, J, px, py = _lattice_points((H, W), sy, ox, oy)
        ix = np.clip(np.round(px).astype(int), 0, W - 1); iy = np.clip(np.round(py).astype(int), 0, H - 1)
        ok = keep_e[iy, ix] & (px > 6) & (py > 6) & (px < W - 7) & (py < H - 7)
        if ok.sum() < 200:
            return -1e9, 0
        px, py = px[ok], py[ok]
        def samp(offs):
            tot = 0.0
            for dx, dy in offs:
                xs = (px + dx * sy).astype(np.float32); ys = (py + dy * sy).astype(np.float32)
                tot += cv2.remap(bh, xs.reshape(-1, 1), ys.reshape(-1, 1), cv2.INTER_LINEAR).mean()
            return tot / len(offs)
        return samp(mids) - samp(inner), int(ok.sum())

    sx_ = S3 * sy0
    best = (-1e9, sy0, 0, 0)
    fgrid = np.arange(0.984, 1.0221, 0.002)
    for f in fgrid:
        sy = sy0 * f
        for ox in np.arange(0, S3 * sy, 1.0):
            for oy in np.arange(0, 2 * sy, 1.0):
                sc, n = score(sy, ox, oy)
                if sc > best[0]:
                    best = (sc, sy, ox, oy)
    sc, sy, ox, oy = best
    if abs(sy / sy0 - fgrid[0]) < 1e-9 or abs(sy / sy0 - fgrid[-1]) < 1e-9:
        log['avertissement'] = 'pas de grille en bord de plage de recherche'
    for step, span, fs in ((0.4, 1.2, (0.9990, 0.9995, 1.0, 1.0005, 1.0010)), (0.15, 0.4, (0.9997, 1.0, 1.0003))):
        base = (sy, ox, oy)
        for f in fs:
            s2 = base[0] * f
            for dx in np.arange(-span, span + 1e-6, step):
                for dy in np.arange(-span, span + 1e-6, step):
                    sc2, n = score(s2, base[1] + dx, base[2] + dy)
                    if sc2 > best[0]:
                        best = (sc2, s2, base[1] + dx, base[2] + dy)
        sc, sy, ox, oy = best
    log['grille_image'] = {'contraste_traits': round(float(sc), 2), 'sy': round(float(sy), 4)}
    return sy, ox, oy, sc


def image_cells(img_s, keep, sy, ox, oy):
    """Couleur (RGB) de chaque case de la grille de l'image : liste (i, j, rgb)."""
    H, W = keep.shape
    I, J, px, py = _lattice_points((H, W), sy, ox, oy)
    yy, xx = np.mgrid[-7:8, -7:8]
    r = np.hypot(xx, yy); ann = (r >= 4.0) & (r <= 6.2)
    out = []
    for i, j, x, y in zip(I, J, px, py):
        ix, iy = int(round(x)), int(round(y))
        if ix < 8 or iy < 8 or ix >= W - 8 or iy >= H - 8 or not keep[iy, ix]:
            continue
        patch = img_s[iy - 7:iy + 8, ix - 7:ix + 8].astype(float)
        out.append((int(i), int(j), np.median(patch[ann], axis=0)[::-1]))
    return out


def match_offset(dep, cells_img, log):
    """Décalage entier (dx, dy) : cellule image (i,j) -> cellule monde (i+dx, j+dy), cherché sur toute la carte.
    Comparaison par terrain (le hachurage est ignoré) : corrélation de grilles de terrains."""
    img_t = []
    for i, j, rgb in cells_img:
        t, vis, d = classify(dep, rgb)
        if d <= NEW_TERRAIN_DIST:
            img_t.append((i, j, t))
    if len(img_t) < 100:
        raise SystemExit("recalage impossible : trop peu de cases reconnues dans la capture")
    K = len(dep.j['terrains'])
    wx = np.array([k[0] for k in dep.cells]); wy = np.array([k[1] for k in dep.cells])
    ix_ = np.array([c[0] for c in img_t]); iy_ = np.array([c[1] for c in img_t])
    Wd, Hd = int(ix_.max() - ix_.min() + 1), int(iy_.max() - iy_.min() + 1)
    px, py = Wd + 2, Hd + 2
    wx0, wy0 = int(wx.min()) - px, int(wy.min()) - py
    GW, GH = int(wx.max() - wx.min() + 1) + 2 * px, int(wy.max() - wy.min() + 1) + 2 * py
    Wt = np.zeros((K, GH, GW), np.float32); Wm = np.zeros((GH, GW), np.float32)
    for (x, y), c in dep.cells.items():
        Wt[c[2], y - wy0, x - wx0] = 1; Wm[y - wy0, x - wx0] = 1
    It = np.zeros((K, Hd, Wd), np.float32); Im = np.zeros((Hd, Wd), np.float32)
    imin, jmin = int(ix_.min()), int(iy_.min())
    for i, j, t in img_t:
        It[t, j - jmin, i - imin] = 1; Im[j - jmin, i - imin] = 1
    M = np.zeros((GH - Hd + 1, GW - Wd + 1), np.float32)
    for t in range(K):
        if It[t].any():
            M += cv2.matchTemplate(Wt[t], It[t], cv2.TM_CCORR)
    C = cv2.matchTemplate(Wm, Im, cv2.TM_CCORR)
    S = M - 0.5 * (C - M)
    yy, xx = np.mgrid[0:S.shape[0], 0:S.shape[1]]
    dxs = wx0 + xx - imin; dys = wy0 + yy - jmin
    S[(C < 100) | (((dxs + dys) % 2) != 0)] = -1e9
    order = np.argsort(S.ravel())[::-1]
    b = order[0]
    by, bx = divmod(int(b), S.shape[1])
    best = (int(round(M[by, bx])), int(round(C[by, bx])), int(dxs[by, bx]), int(dys[by, bx]))
    second = (0, 0, 0, 0)
    for o in order[1:400]:
        y2, x2 = divmod(int(o), S.shape[1])
        if abs(int(dxs[y2, x2]) - best[2]) + abs(int(dys[y2, x2]) - best[3]) > 3 and S[y2, x2] > -1e8:
            second = (int(round(M[y2, x2])), int(round(C[y2, x2])), int(dxs[y2, x2]), int(dys[y2, x2])); break
    log['decalage_cases'] = {'dx': best[2], 'dy': best[3], 'cases_comparees': best[1], 'identiques': best[0],
                             'part': round(best[0] / max(best[1], 1), 3), 'second_meilleur': second[0]}
    return best, second


# ---------------------------------------------------------------- extraction
def extract_cells(dep, img, s, a, b, ui, log):
    H, W = dep.mos.shape[:2]
    warped = warp_to(img, s, a, b, W, H)
    src_valid = np.ones(img.shape[:2], np.uint8)
    src_valid[ui] = 0
    valid = warp_to(src_valid, s, a, b, W, H, cv2.INTER_NEAREST) > 0
    valid = cv2.erode(valid.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=int(round(10 * s)) + 2).astype(bool)
    keep = land_core(warped, valid)
    yy, xx = np.mgrid[-7:8, -7:8]
    ann = (np.hypot(xx, yy) >= 4.0) & (np.hypot(xx, yy) <= 6.2)
    ys, xs = np.where(keep)
    out = []
    xmin = int(np.floor((xs.min() + dep.mx0 - dep.ox) / dep.sx)) - 1
    xmax = int(np.ceil((xs.max() + dep.mx0 - dep.ox) / dep.sx)) + 1
    ymin = int(np.floor((ys.min() + dep.my0 - dep.oy) / dep.sy)) - 1
    ymax = int(np.ceil((ys.max() + dep.my0 - dep.oy) / dep.sy)) + 1
    for x in range(xmin, xmax + 1):
        for y in range(ymin, ymax + 1):
            if (x + y) % 2:
                continue
            cx, cy = dep.center(x, y)
            ix, iy = int(round(cx - dep.mx0)), int(round(cy - dep.my0))
            if ix < 8 or iy < 8 or ix >= W - 8 or iy >= H - 8 or not keep[iy, ix]:
                continue
            patch = warped[iy - 7:iy + 8, ix - 7:ix + 8].astype(float)
            out.append((x, y, np.median(patch[ann], axis=0)[::-1]))   # RGB (médiane : robuste aux icônes)
    return warped, out


def terrain_models(dep):
    T = np.array([t['rgb'] for t in dep.j['terrains']], float)
    A = dep.j['hachure']['alpha']
    C = np.array(dep.j['hachure']['couleur'], float)
    return T, (1 - A) * T + A * C


def classify(dep, rgb):
    """(terrain, hachuree 0/1, distance) : couleur pleine ou version hachurée la plus proche."""
    T, Hh = terrain_models(dep)
    d0 = np.linalg.norm(T - rgb, axis=1); d1 = np.linalg.norm(Hh - rgb, axis=1)
    i0, i1 = int(d0.argmin()), int(d1.argmin())
    return (i0, 0, float(d0[i0])) if d0[i0] <= d1[i1] else (i1, 1, float(d1[i1]))


def classify_all(dep, obs, log):
    """Classe les cases ; les couleurs inconnues récurrentes (>= 4 cases) deviennent de nouveaux terrains."""
    res, unknown = [], []
    for x, y, rgb in obs:
        t, vis, d = classify(dep, rgb)
        if d <= NEW_TERRAIN_DIST:
            res.append((x, y, t, vis))
        else:
            unknown.append((x, y, rgb))
    added = []
    groups = []
    for x, y, rgb in unknown:
        for g in groups:
            if np.linalg.norm(g['c'] - rgb) < 16:
                g['m'].append((x, y, rgb)); g['c'] = np.mean([r for _, _, r in g['m']], axis=0); break
        else:
            groups.append({'c': rgb.copy(), 'm': [(x, y, rgb)]})
    skipped = 0
    for g in groups:
        if len(g['m']) >= NEW_TERRAIN_MIN:
            dep.j['terrains'].append({'rgb': [int(round(v)) for v in g['c']], 'nom': None})
            added.append([int(round(v)) for v in g['c']])
            t = len(dep.j['terrains']) - 1
            for x, y, _ in g['m']:
                res.append((x, y, t, 0))
        else:
            skipped += len(g['m'])
    log['nouveaux_terrains'] = added
    log['cases_non_reconnues'] = skipped
    return res


def src_date(dep, idx):
    return dep.j['sources'][idx].get('date') or '0000-00-00'


def merge(dep, obs, src_idx, log):
    cls = classify_all(dep, obs, log)
    my_date = src_date(dep, src_idx)
    st = dict(nouvelles=0, confirmees=0, modifiees=0, ignorees_plus_anciennes=0)
    won = []
    for x, y, t, vis in cls:
        rec = dep.cells.get((x, y))
        if rec is None:
            dep.cells[(x, y)] = [x, y, t, src_idx, vis]
            st['nouvelles'] += 1; won.append((x, y)); continue
        old_t, old_src = rec[2], rec[3]
        newer = my_date >= src_date(dep, old_src)
        if old_t == t:
            st['confirmees'] += 1
            if newer:
                rec[3], rec[4] = src_idx, vis
                won.append((x, y))
        elif newer:
            hist = (rec[5] if len(rec) > 5 else []) + [[old_t, old_src]]
            dep.cells[(x, y)] = [x, y, t, src_idx, vis, hist]
            st['modifiees'] += 1; won.append((x, y))
        else:
            st['ignorees_plus_anciennes'] += 1
    log['cases'] = st
    return won


def paste_mosaic(dep, warped, won):
    H, W = dep.mos.shape[:2]
    mask = np.zeros((H, W), np.uint8)
    R = dep.R * 1.03
    for x, y in won:
        cx, cy = dep.center(x, y)
        cx -= dep.mx0; cy -= dep.my0
        pts = np.array([[(cx + R * math.cos(math.pi / 3 * k)) * 16, (cy + R * math.sin(math.pi / 3 * k)) * 16]
                        for k in range(6)], np.int32)
        cv2.fillPoly(mask, [pts], 1, lineType=cv2.LINE_8, shift=4)
    m = mask > 0
    dep.mos[m] = warped[m]
    dep.alpha[m] = 255


# ---------------------------------------------------------------- programme principal
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--depot', required=True)
    ap.add_argument('--image', required=True)
    ap.add_argument('--nom', required=True)
    ap.add_argument('--date', required=True, help='AAAA-MM-JJ')
    ap.add_argument('--date-estimee', action='store_true')
    ap.add_argument('--etiquette', action='append', default=[], help='"Nom=x_px,y_px" (position dans la capture)')
    ap.add_argument('--simulation', action='store_true', help='ne rien écrire')
    args = ap.parse_args()

    dep = Depot(args.depot)
    img = read_bgr(args.image)
    log = {'image': os.path.basename(args.image), 'nom': args.nom, 'date': args.date, 'taille': list(img.shape[1::-1])}
    h, nwin, ntot = estimate_hex_height(img)
    s0 = dep.h_ref / h
    log.update({'hex_hauteur_px': round(h, 2), 'echelle_initiale': round(s0, 4), 'fenetres_grille': '%d/%d' % (nwin, ntot)})

    ui = ui_mask(img)
    new_s = cv2.resize(img, None, fx=s0, fy=s0, interpolation=cv2.INTER_AREA if s0 < 1 else cv2.INTER_CUBIC)
    ui_s = cv2.resize(ui.astype(np.uint8), (new_s.shape[1], new_s.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
    land_s = land_core(new_s, ~ui_s)
    land0 = cv2.resize(land_s.astype(np.uint8), (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
    # grille propre à la capture, puis décalage entier de cases
    sy_i, ox_i, oy_i, contrast = fit_lattice(new_s, land_s, dep.sy, log)
    cells_img = image_cells(new_s, land_s, sy_i, ox_i, oy_i)
    log['cases_image'] = len(cells_img)
    s_ws = dep.sy / sy_i                      # new_s -> monde
    best, second = match_offset(dep, cells_img, log)
    dxc, dyc = best[2], best[3]
    a_s = dep.ox + dep.sx * dxc - s_ws * ox_i - dep.mx0
    b_s = dep.oy + dep.sy * dyc - s_ws * oy_i - dep.my0
    s = s0 * s_ws; a = a_s; b = b_s
    n, agree, dmean = pixel_agreement(dep, img, land0, s, a, b)
    log.update({'echelle_finale': round(s, 5), 'pixels_en_commun': n, 'part_concordante': round(agree, 3)})
    part = best[0] / max(best[1], 1)
    if best[1] < 120 or part < 0.6 or best[0] < 1.25 * max(second[0], 1):
        log['statut'] = 'REFUSE'
        print(json.dumps(log, ensure_ascii=False, indent=1, default=_jd))
        raise SystemExit("recalage refusé : %d cases comparées, %.0f %% identiques, concordance pixels %.0f %%" %
                         (best[1], part * 100, agree * 100))

    # source
    srcs = dep.j['sources']
    srcs.append({'nom': args.nom, 'date': args.date, 'date_estimee': bool(args.date_estimee),
                 'fichier': os.path.basename(args.image)})
    src_idx = len(srcs) - 1
    warped, obs = extract_cells(dep, img, s, a, b, ui, log)
    log['cases_lues'] = len(obs)
    won = merge(dep, obs, src_idx, log)
    paste_mosaic(dep, warped, won)

    # étiquettes : position px dans la capture -> coordonnées de case
    for e in args.etiquette:
        nom, pos = e.rsplit('=', 1)
        u, v = [float(t) for t in pos.split(',')]
        X, Y = s * u + a + dep.mx0, s * v + b + dep.my0
        lx, ly = round((X - dep.ox) / dep.sx, 2), round((Y - dep.oy) / dep.sy, 2)
        labs = [l for l in dep.j['labels'] if l['t'] == nom]
        if labs:
            labs[0]['x'], labs[0]['y'] = lx, ly
        else:
            dep.j['labels'].append({'t': nom, 'x': lx, 'y': ly})
    log['statut'] = 'OK'
    log['transformation'] = {'s': round(s, 5), 'a': round(a, 2), 'b': round(b, 2)}
    dep.j.setdefault('journal', []).append(log)
    if not args.simulation:
        dep.save()
    print(json.dumps(log, ensure_ascii=False, indent=1, default=_jd))


if __name__ == '__main__':
    main()
