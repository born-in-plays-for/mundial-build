"""
Compares old vs new wc2026_elo_rank.json and prints a git commit message
with a human-readable summary of ranking and points changes.

Usage:
    MSG=$(python3 pipeline/elo_diff_summary.py)           # normal: HEAD vs working tree
    python3 pipeline/elo_diff_summary.py --old-ref 57bcc02^  # simulate a past commit
    git commit -m "$MSG"
"""
import argparse
import json
import subprocess


def load_old(ref='HEAD'):
    try:
        raw = subprocess.check_output(
            ['git', 'show', f'{ref}:wc2026_elo_rank.json'],
            stderr=subprocess.DEVNULL,
        )
        return json.loads(raw)
    except Exception:
        return None


def main(old_ref='HEAD'):
    with open('wc2026_elo_rank.json', encoding='utf-8') as f:
        new = json.load(f)

    date = new.get('updated', '')
    subject = f"data: update Elo rankings {date}"

    old = load_old(old_ref)
    if old is None:
        print(subject)
        return

    old_by_id = {r['id']: r for r in old.get('rankings', []) if r.get('id') is not None}
    new_by_id = {r['id']: r for r in new.get('rankings', []) if r.get('id') is not None}

    changes = []
    for cid, nr in new_by_id.items():
        or_ = old_by_id.get(cid)
        if or_ is None:
            continue
        old_pts = or_.get('pts')
        new_pts = nr.get('pts')
        pts_delta = (new_pts or 0) - (old_pts or 0)
        if pts_delta != 0:
            old_rank = or_.get('rank')
            new_rank = nr.get('rank')
            changes.append({
                'name':       nr['name'],
                'old_rank':   old_rank,
                'new_rank':   new_rank,
                'rank_delta': (old_rank or 0) - (new_rank or 0),
                'pts_delta':  pts_delta,
            })

    # Entries that appeared or disappeared
    added   = [new_by_id[i]['name'] for i in new_by_id if i not in old_by_id]
    removed = [old_by_id[i]['name'] for i in old_by_id if i not in new_by_id]

    if not changes and not added and not removed:
        print(subject)
        return

    # Sort by absolute pts change descending, then rank change
    changes.sort(key=lambda c: (-abs(c['pts_delta']), -abs(c['rank_delta'])))

    lines = [subject, '']

    if changes:
        w = max(len(c['name']) for c in changes)
        lines.append('Points changes:')
        for c in changes:
            arrow = '↑' if c['rank_delta'] > 0 else ('↓' if c['rank_delta'] < 0 else '=')
            rank_str = f"#{c['old_rank']}→#{c['new_rank']} ({arrow}{abs(c['rank_delta'])})" if c['rank_delta'] else f"#{c['new_rank']}"
            lines.append(f"  {c['name']:<{w}}  {rank_str:<22}  {c['pts_delta']:+d} pts")

    if added:
        lines.append('')
        lines.append('Added:   ' + ', '.join(added))
    if removed:
        lines.append('Removed: ' + ', '.join(removed))

    print('\n'.join(lines))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--old-ref', default='HEAD', help='git ref for the old version (default: HEAD)')
    args = parser.parse_args()
    main(args.old_ref)
