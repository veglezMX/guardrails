import pytest

from guardrails.engine import _redact
from guardrails.evaluators.pii import LocalPIIEvaluator
from proof.runtime_test_support import batch, components


@pytest.mark.parametrize('text,expected', [
    ('a@b.co', [('email', 0, 6)]),
    ('(A.b+tag@Example.COM).', [('email', 1, 20)]),
    ('hello a@b.co.', [('email', 6, 12)]),
    ('文🙂 a@b.co 文', [('email', 3, 9)]),
    ("a!#$%&'*+/=?^_`{|}~-z@example.com", [('email', 0, 33)]),
    ('+12345678', [('phone', 0, 9)]),
    ('(+123456789012345)', [('phone', 1, 17)]),
    ('文 +12345678 文', [('phone', 2, 11)]),
    ('a@b.co,+12345678', [('email', 0, 6), ('phone', 7, 16)]),
    ('a' * 64 + '@example.com', [('email', 0, 76)]),
    ('a@' + 'b' * 63 + '.co', [('email', 0, 68)]),
    ('a' * 64 + '@' + 'b' * 63 + '.' + 'c' * 63 + '.' + 'd' * 61, [('email', 0, 254)]),
])
def test_exact_positive_spans(text, expected):
    check = components()[1].checks[0]
    findings = LocalPIIEvaluator().evaluate(batch(text), check)
    assert [(f.reason_code.rsplit(':', 1)[1], f.start, f.end) for f in findings] == expected
    assert all(f.segment_id == '0' and f.check_id == 'pii' for f in findings)


@pytest.mark.parametrize('text', [
    'clean 12345678', '.a@b.co', 'a..b@c.co', 'a.@b.co', 'a@@b.co', 'a@b',
    'a@-b.co', 'a@b-.co', 'a@b_c.co', 'a@b.c', 'a@b.12', 'a@b..co',
    'a@b.co..', 'a' * 65 + '@b.co', 'a@' + 'b' * 64 + '.co',
    'a' * 64 + '@' + 'b' * 63 + '.' + 'c' * 63 + '.' + 'd' * 62,
    'éa@b.co', 'a@b.coé', '"a"@b.co', 'a@[127.0.0.1]',
    '+1234567', '+1234567890123456', '+012345678', '++12345678', '1+12345678',
    '+12345678+', '+１２３４５６７８', '９+12345678', '+12345678９', '+12-345-678',
    '+12 345 678', '+1(234)5678',
])
def test_malformed_or_unsupported_tokens_never_yield_valid_substrings(text):
    assert LocalPIIEvaluator().evaluate(batch(text), components()[1].checks[0]) == ()


def test_segments_never_join_and_offsets_are_code_points():
    result = LocalPIIEvaluator().evaluate(batch('a@', 'b.co', '🙂a@b.co'), components()[1].checks[0])
    assert [(f.segment_id, f.start, f.end) for f in result] == [('2', 1, 7)]


def test_overlap_touching_and_duplicate_spans_replace_the_full_union():
    assert _redact('0123456789XYZ', ((2, 6), (4, 10), (10, 11), (2, 6))) == '01[REDACTED]YZ'


@pytest.mark.parametrize('spans', [((-1, 2),), ((2, 1),), ((0, 100),)])
def test_invalid_spans_are_never_partially_redacted(spans):
    with pytest.raises(ValueError):
        _redact('abcdef', spans)
