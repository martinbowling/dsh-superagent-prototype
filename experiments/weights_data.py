"""Private deterministic ISBN checksum datasets; never imported by the arena tool.

Labels concern checksum arithmetic, not real book registration/existence.
"""
import hashlib
import json
import random

def valid(text):
    return len(text) == 13 and text.isascii() and text.isdigit() and sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(text)) % 10 == 0

def complete(prefix):
    return prefix + str((-sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(prefix))) % 10)

def dataset(seed, count, family='random', excluded=None):
    rng = random.Random(seed); rows = []; seen = set(excluded or ())
    if count % 2: raise ValueError('Balanced count must be even')
    while len(rows) < count:
        if family == 'random': middle = ''.join(str(rng.randrange(10)) for _ in range(9))
        elif family == 'runs':
            a, b, c = rng.sample('0123456789', 3); left, right = sorted(rng.sample(range(1, 9), 2))
            middle = a * left + b * (right - left) + c * (9 - right)
        elif family == 'extremes': middle = ''.join(rng.choice('012789') for _ in range(9))
        else: raise ValueError('Unknown family')
        text = complete(rng.choice(('978', '979')) + middle)
        bad = text[:-1] + str((int(text[-1]) + rng.randrange(1, 10)) % 10)
        if text in seen or bad in seen: continue
        seen.update((text, bad))
        rows.extend([(text, 1, family), (bad, 0, family)])
    rng.shuffle(rows)
    assert all(valid(s) == bool(y) for s, y, _ in rows)
    return rows

def digest(rows):
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()

def make_splits(seed=20261103):
    train = dataset(seed, 8192)
    # Families of pairs share the first 12 digits; splits exclude exact strings.
    # Also exclude their 12-digit stem, preventing label-pair leakage.
    stems = {s[:12] for s, _, _ in train}
    test = []; excluded = {s for s, _, _ in train}
    for i, (family, count) in enumerate((('random', 10000), ('extremes', 4000), ('runs', 2000))):
        candidates = dataset(seed + 100 + i, count * 2, family, excluded)
        selected = []
        for j in range(0, len(candidates)):
            s, y, f = candidates[j]
            if s[:12] not in stems: selected.append((s, y, f))
        # Pairwise balancing again after stem exclusion; never use predictions.
        positives = [r for r in selected if r[1] == 1][:count // 2]
        negatives = [r for r in selected if r[1] == 0][:count // 2]
        if len(positives) != count // 2 or len(negatives) != count // 2: raise ValueError('Insufficient disjoint rows')
        selected = positives + negatives; random.Random(seed + i).shuffle(selected)
        test += selected; excluded.update(s for s, _, _ in selected)
    assert not ({s[:12] for s, _, _ in test} & stems)
    return train, test
