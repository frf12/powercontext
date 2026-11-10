"""Recompute the README's numeric claims from the bundled evidence; no model calls."""
import json
from pathlib import Path

DATA = json.loads(Path(__file__).with_name('results.json').read_text())
ROWS = DATA['initial_and_merged_121']
MROWS = DATA['mcs_common24']
IDS = set(DATA['common24_ids'])
FIELDS = ('input_tokens', 'cached_input_tokens', 'uncached_input_tokens', 'output_tokens', 'total_tokens')
assert len(ROWS) == len({r['case'] for r in ROWS}) == 121
assert len(MROWS) == len({r['case'] for r in MROWS}) == len(IDS) == 24
assert {r['case'] for r in MROWS} == IDS


def check(r):
    t = r['tokens']
    assert r['valid'] and isinstance(r['resolved'], bool)
    assert all(isinstance(t[k], int) and t[k] >= 0 for k in FIELDS)
    assert t['total_tokens'] == t['input_tokens'] + t['output_tokens']
    assert t['uncached_input_tokens'] == t['input_tokens'] - t['cached_input_tokens']


for row in ROWS:
    for arm, stages in row['arms'].items():
        for stage, value in stages.items():
            for r in value if stage == 'additional_attempts' else [value]:
                check(r)
        if 'merged' in stages:
            retries = stages['additional_attempts']
            expected = next((r for r in retries if r['resolved']), retries[-1] if retries else stages['initial'])
            assert expected['outcome_sha256'] == stages['merged']['outcome_sha256']
for row in MROWS:
    for r in row['arms'].values():
        check(r)
    original = next(r for r in ROWS if r['case'] == row['case'])
    assert original['instance_id'] == row['instance_id']
    assert original['arms']['gateway_off']['initial']['tokens'] == row['arms']['gateway_off']['tokens']
    assert original['arms']['gateway_off']['initial']['outcome_sha256'] == row['arms']['gateway_off']['outcome_sha256']


def aggregate(records):
    return {'cases': len(records), 'passed': sum(r['resolved'] for r in records),
            **{k: sum(r['tokens'][k] for r in records) for k in FIELDS}}


summaries = {}
for cohort, ids, stage, arms in [
    ('common24_initial', IDS, 'initial', ['gateway_off', 'S', 'CS', 'MCS']),
    ('common24_merged', IDS, 'merged', ['gateway_off', 'S', 'CS', 'MCS']),
    ('full121_initial', {r['case'] for r in ROWS}, 'initial', ['gateway_off', 'native_off', 'C', 'S', 'CS']),
    ('full121_merged', {r['case'] for r in ROWS}, 'merged', ['gateway_off', 'S', 'CS']),
]:
    sums = {}
    for arm in arms:
        records = [r['arms']['MCS'] for r in MROWS] if arm == 'MCS' else [r['arms'][arm][stage] for r in ROWS if r['case'] in ids]
        sums[arm] = aggregate(records)
    baseline = sums['gateway_off']['total_tokens']
    for r in sums.values():
        r['reduction_percent'] = 100 * (baseline - r['total_tokens']) / baseline
    summaries[cohort] = sums

assert [summaries['common24_initial'][a]['total_tokens'] for a in ['gateway_off', 'S', 'CS', 'MCS']] == [26410221, 19247973, 17680166, 15766986]
assert [summaries['common24_initial'][a]['passed'] for a in ['gateway_off', 'S', 'CS', 'MCS']] == [16, 15, 15, 16]
assert summaries['full121_initial']['CS']['total_tokens'] == 90544517
assert summaries['full121_merged']['CS']['total_tokens'] == 90153970
extra = DATA['mcs_extra']
assert extra['memory_generation_input'] + extra['memory_generation_output'] == 2041992
assert extra['memory_observation']['observed_primary_memory_content_sends'] == 0
# Check the advertised paired maxima, not maxima taken independently per arm.
for arm, expected_case in [('S', 450), ('CS', 302)]:
    best = max(ROWS, key=lambda r: 1 - r['arms'][arm]['initial']['tokens']['total_tokens'] / r['arms']['gateway_off']['initial']['tokens']['total_tokens'])
    assert best['case'] == expected_case
    assert best['arms'][arm]['initial']['resolved'] and best['arms']['gateway_off']['initial']['resolved']
for arm, expected_case in [('S', 202), ('CS', 464), ('MCS', 202)]:
    totals = {r['case']: r['arms'] for r in MROWS} if arm == 'MCS' else {r['case']: {a: r['arms'][a]['initial'] for a in [arm, 'gateway_off']} for r in ROWS if r['case'] in IDS}
    best = max(totals, key=lambda i: 1 - totals[i][arm]['tokens']['total_tokens'] / totals[i]['gateway_off']['tokens']['total_tokens'])
    assert best == expected_case
for name, arms in summaries.items():
    print('\n' + name)
    for arm, r in arms.items():
        print(f"{arm:12} {r['total_tokens']:>12,} tokens  {r['reduction_percent']:>7.2f}% fewer  {r['passed']}/{r['cases']} passed")
print('\nVerified: case alignment, token arithmetic, record selection and README totals. No inference or grading performed.')

# Recompute the explicitly modeled cache-cost estimate, without altering missing observations.
estimate = json.loads(Path(__file__).with_name("cost-estimate.json").read_text())
ratio = estimate["observed_memory_cached"] / estimate["observed_memory_input"]
cache = estimate["observed_memory_cached"] + estimate["missing_cache_input"] * ratio
memory_points = ((estimate["memory_input"] - cache) * 100 + cache * 10 + estimate["memory_output"] * 500) / 1_000_000
saving = (1 - (estimate["pc_solving_points"] + memory_points) / estimate["off_solving_points"]) * 100
assert round(saving, 2) == 15.42
assert round(ratio * 100, 3) == 96.994
print(f"Estimated memory generation: {memory_points:.5f} points; estimated saving: {saving:.2f}% (not an observed bill).")
