# -*- coding: utf-8 -*-
import json, re, itertools
items = [json.loads(l) for l in open('data/benchmark_xingce_blind_v2/test/xingce_blind_v2.jsonl', encoding='utf-8')]
proofs = {p['id']: p for p in json.load(open('data/benchmark_xingce_blind_v2/test/proofs.json', encoding='utf-8'))}

NUMRE = re.compile(r'(\d+(?:\.\d+)?)')

def nums(s):
    return {m.group(1) for m in NUMRE.finditer(s)}

flag = []
for it in items:
    pr = proofs[it['id']]
    face = set(nums(it['stem']))
    for o in it['options']:
        face |= nums(o['text'])
    if it.get('material'):
        face |= nums(json.dumps(it['material'], ensure_ascii=False))
    if it.get('regions'):
        face |= nums(json.dumps(it['regions'], ensure_ascii=False))
    fv = [float(x) for x in face]
    derived = set(face)
    for a in fv:
        derived.add('%g' % a); derived.add(str(int(a)) if abs(a - int(a)) < 1e-9 else '')
    for a, b in itertools.permutations(fv, 2):
        vals = (a + b, a - b, b - a, a * b, (a / b if b else 0), a * (1 + b / 100), a * (1 - b / 100))
        for v in vals:
            if v > 0:
                derived.add('%g' % round(v, 4)); derived.add('%g' % round(v, 2))
                derived.add(str(int(v)) if abs(v - int(v)) < 1e-9 else '')
    text = ' '.join(str(pr.get(k, '')) for k in ('conditions', 'solution_path', 'recompute'))
    stale = []
    for n in sorted(nums(text) - derived):
        ip = n.split('.')[0]
        if len(ip) >= 3 and not (2010 <= float(n) <= 2030):
            stale.append(n)
    if stale:
        flag.append((it['id'], stale[:6]))
print('stale 题数:', len(flag))
for x in flag:
    print('  ', x)
