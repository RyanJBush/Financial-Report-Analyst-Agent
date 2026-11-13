"""Evaluate frozen question cases. This file may read gold; production answers may not."""
import hashlib
import json
from pathlib import Path

from finreport.answers import answer_question, load_verified_table, validate_answer
from finreport.narrative import render, scripted, validate_narrative

ROOT = Path(__file__).resolve().parents[1]


def main():
    body = (ROOT/'evaluation/answer_cases.json').read_bytes()
    expected_sha = (ROOT/'evaluation/answer_cases.sha256').read_text().split()[0]
    assert hashlib.sha256(body).hexdigest() == expected_sha, 'evaluation cases changed'
    cases = json.loads(body)
    table = load_verified_table(ROOT)
    rows = []
    for case in cases:
        a = answer_question(case['question'],table)
        n = scripted(a)
        actual = {'status':a['status'],'values':[c['value'] for c in a['claims']],
                  'periods':[c['period'] for c in a['claims']],
                  'currencies':[c['currency'] for c in a['claims']],
                  'scopes':[c['scope'] for c in a['claims']],
                  'evidence_ids':a['evidence_ids'],
                  'absolute_change':a['calculation']['absolute_change'] if a['calculation'] else None,
                  'percent_change':a['calculation']['percent_change'] if a['calculation'] else None}
        passed = actual == case['expected'] and validate_answer(a,table) and validate_narrative(n,a)
        rows.append({'id':case['id'],'question':case['question'],'correct':passed,'actual':actual,'summary':render(a,n)})
    report = {'classification':'post-implementation question evaluation; exposed after first execution; not an independent accuracy estimate',
              'fixture_sha256':expected_sha,'correct':sum(r['correct'] for r in rows),'total':len(rows),'cases':rows}
    (ROOT/'output/answer_evaluation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f"Question cases: {report['correct']}/{report['total']}")
    raise SystemExit(0 if report['correct'] == report['total'] else 1)


if __name__ == '__main__': main()
