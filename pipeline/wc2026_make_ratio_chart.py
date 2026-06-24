"""
wc2026_make_ratio_chart.py
Produces wc2026_export_ratio.png from wc2026_map_data.json.

Usage:
    python3 wc2026_make_ratio_chart.py [--no-curacao]   (default: Curaçao excluded)
    python3 wc2026_make_ratio_chart.py --with-curacao

The --no-curacao flag (default) removes Curaçao as a destination from
all counts, matching the original pre-Curaçao scope of the chart.
"""
import json, argparse, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np

ROOT = Path(__file__).parent.parent / "data"

# ── Config ────────────────────────────────────────────────────────────────────
OUTPUT      = ROOT / 'wc2026_export_ratio.png'
MIN_RATIO   = 0.05          # hide countries below this threshold
TOP_N       = 20            # max bars to show
BG_COLOR    = '#f5f2ec'     # map background
BAR_COLOR   = '#9b59b6'     # base purple
HIGH_COLOR  = '#7b2d8b'     # darker for top 3
LOW_COLOR   = '#ddb8ea'     # lighter for lower ranks
TEXT_COLOR  = '#1a1a18'
MUTED_COLOR = '#888'
DPI         = 150
WIDTH_IN    = 960 / DPI
HEIGHT_IN   = 900 / DPI

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('--with-curacao', action='store_true',
                    help='Include Curaçao as an export destination')
args = parser.parse_args()
exclude_curacao = not args.with_curacao

# ── Data ──────────────────────────────────────────────────────────────────────
with open(ROOT / 'wc2026_map_data.json', encoding='utf-8') as f:
    app = json.load(f)

CURA_NATION = 'Curaçao'

rows = []
for e in app['data']:
    country = e['country']
    pop     = app['pop'].get(country)
    if not pop:
        continue

    count = e['count']
    if exclude_curacao:
        # Subtract players exported to Curaçao
        cura_exports = sum(c for n, c in e['nations'] if n == CURA_NATION)
        count -= cura_exports

    if count <= 0:
        continue

    ratio = count / pop
    if ratio >= MIN_RATIO:
        rows.append({'country': country, 'count': count, 'pop': pop, 'ratio': ratio})

rows.sort(key=lambda r: r['ratio'])
rows = rows[-TOP_N:]          # keep top N (sorted ascending for horizontal bar)

# ── Colors ────────────────────────────────────────────────────────────────────
n = len(rows)
# gradient from LOW_COLOR (bottom) to HIGH_COLOR (top)
def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16)/255 for i in (0, 2, 4))

lo = np.array(hex_to_rgb(LOW_COLOR))
hi = np.array(hex_to_rgb(HIGH_COLOR))
colors = [tuple(lo + (hi - lo) * i / (n - 1)) for i in range(n)]

# ── Plot ──────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(WIDTH_IN, HEIGHT_IN), dpi=DPI)
fig.patch.set_facecolor(BG_COLOR)
ax.set_facecolor(BG_COLOR)

y_pos = np.arange(n)
bars  = ax.barh(y_pos, [r['ratio'] for r in rows], color=colors,
                height=0.65, left=0)

# Country labels (left of bar)
ax.set_yticks(y_pos)
ax.set_yticklabels([r['country'] for r in rows],
                   fontsize=10, color=TEXT_COLOR)

# Value labels (right of bar)
for i, r in enumerate(rows):
    ratio_str = f"{r['ratio']:.2f}" if r['ratio'] >= 0.10 else f"{r['ratio']:.3f}"
    ax.text(r['ratio'] + 0.01, i,
            f" {ratio_str}  ({r['count']} / {r['pop']:.1f}M)",
            va='center', ha='left', fontsize=8.5, color=MUTED_COLOR)

# Axes styling
ax.spines[['top', 'right', 'left', 'bottom']].set_visible(False)
ax.tick_params(left=False, bottom=False)
ax.set_xticks([])
ax.xaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f'{x:.1f}'))

# Title & subtitle
curacao_note = ' (Curaçao exclu)' if exclude_curacao else ''
fig.text(0.5, 0.97, 'Mondial 2026 — joueurs exportés / million d\'hab.',
         ha='center', fontsize=13, fontweight='bold', color=TEXT_COLOR)
fig.text(0.5, 0.94,
         f'Pays de naissance · normalisé par population{curacao_note} · source : Wikipedia',
         ha='center', fontsize=9, color=MUTED_COLOR)

plt.tight_layout(rect=[0, 0, 1, 0.93])
plt.savefig(OUTPUT, dpi=DPI, bbox_inches='tight', facecolor=BG_COLOR)
plt.close()
print(f"Saved {OUTPUT}  ({n} countries, Curaçao {'excluded' if exclude_curacao else 'included'})")
