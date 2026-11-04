"""Data-only sparse declarations of one fixed 99,969-parameter ReLU network.

No expression evaluation, executable generator, external lookup, or label oracle
is accepted. Digit coefficients expand into ordinary one-hot input weights.
"""
import hashlib
import json
import math

SHAPES = ((512, 130), (64, 512), (1, 64))
PARAMETERS = sum(o * i + o for o, i in SHAPES)

def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e6:
        raise ValueError('Weights must be finite numbers with magnitude <= 1e6')
    return float(value)

def index(value, size):
    if type(value) is not int or not 0 <= value < size:
        raise ValueError('Neuron/weight index out of bounds')
    return value

def pairs(value, size):
    if not isinstance(value, list) or len(value) > size:
        raise ValueError('Expected bounded sparse [index, weight] pairs')
    result = {}; 
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('Expected [index, weight] pair')
        key = index(pair[0], size)
        if key in result: raise ValueError('Duplicate weight index')
        result[key] = number(pair[1])
    return result

def unique_object(items):
    result = {}
    for key, value in items:
        if key in result: raise ValueError('Duplicate JSON key')
        result[key] = value
    return result

def parse(text):
    if not isinstance(text, str) or len(text.encode()) > 64000:
        raise ValueError('Weight file must be UTF-8 JSON <= 64000 bytes')
    value = json.loads(text, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or set(value) != {'schema', 'first', 'second', 'output', 'notes'} or type(value['schema']) is not int or value['schema'] != 1:
        raise ValueError('Expected schema=1, first, second, output, notes only')
    if not isinstance(value['notes'], str) or len(value['notes']) > 8000:
        raise ValueError('Notes must be a string <=8000 characters')
    layers = []
    for name, (width, inputs) in zip(('first', 'second'), SHAPES):
        rows = value[name]
        if not isinstance(rows, list) or len(rows) > width: raise ValueError('Too many neurons')
        layer = {}
        for row in rows:
            if not isinstance(row, dict): raise ValueError('Expected neuron object')
            form = 'digit_coefficients' if name == 'first' and 'digit_coefficients' in row else 'weights'
            if set(row) != {'index', 'bias', form}: raise ValueError('Unexpected neuron fields')
            k = index(row['index'], width)
            if k in layer: raise ValueError('Duplicate neuron index')
            if form == 'digit_coefficients':
                coeff = row[form]
                if not isinstance(coeff, list) or len(coeff) != 13: raise ValueError('Need 13 digit coefficients')
                weights = {p * 10 + d: number(c) * d for p, c in enumerate(coeff) for d in range(10)}
                for w in weights.values(): number(w)
            else: weights = pairs(row[form], inputs)
            layer[k] = (weights, number(row['bias']))
        layers.append(layer)
    out = value['output']
    if not isinstance(out, dict) or set(out) != {'weights', 'bias'}: raise ValueError('Unexpected output fields')
    layers.append({0: (pairs(out['weights'], 64), number(out['bias']))})
    return layers

def stats(layers):
    return {'allocated_parameters': PARAMETERS, 'nonzero_parameters': sum(sum(v != 0 for v in w.values()) + (b != 0) for layer in layers for w, b in layer.values())}

def logits(layers, digits):
    # Trusted evaluator; raw digits -> one hot -> three fixed affine layers.
    if len(digits) != 13 or any(c not in '0123456789' for c in digits): raise ValueError('Expected 13 ASCII digits')
    x = {p * 10 + int(c): 1.0 for p, c in enumerate(digits)}
    for n, layer in enumerate(layers):
        x = {k: max(0., sum(w * x.get(j, 0.) for j, w in weights.items()) + bias) if n < 2 else sum(w * x.get(j, 0.) for j, w in weights.items()) + bias for k, (weights, bias) in layer.items()}
    return x.get(0, 0.)

def structural_result(text):
    try:
        layers = parse(text)
        # No labeled examples or correctness feedback. Smoke checks only finite inference.
        for s in ('0' * 13, '9' * 13):
            if not math.isfinite(logits(layers, s)): raise ValueError('Nonfinite inference')
        return {'passed': True, 'passed_cases': 1, 'total_cases': 1,
                'feedback': 'Schema/finite-inference validation only. No accuracy evaluated. Report ready; final weights freeze when the run ends.',
                'sha256': hashlib.sha256(text.encode()).hexdigest(), **stats(layers)}
    except (ValueError, TypeError, KeyError, OverflowError) as e:
        return {'passed': False, 'passed_cases': 0, 'total_cases': 1, 'error': str(e)[:500], 'feedback': 'Schema validation only; no labeled examples evaluated'}
