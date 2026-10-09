"""Private, fixed local evaluator subprocess protocol. No dynamic code loading."""
import json
import os
import sys
import time

from ..evaluators.pii import LocalPIIEvaluator
from ..local_contract import CheckSpec, TextBatch, TextSegment


def main():
    print('{"ready":true}', flush=True)
    for line in sys.stdin.buffer:
        try:
            job = json.loads(line)
            fault = job.get('fault')
            if fault == 'stall':
                time.sleep(3600)
            if fault == 'crash':
                os._exit(2)
            content = TextBatch(segments=tuple(TextSegment(**s) for s in job['content']['segments']))
            findings = []
            started = time.monotonic()
            for values in job['checks']:
                check = CheckSpec(**values)
                findings.extend(f.to_wire() for f in LocalPIIEvaluator().evaluate(content, check))
            if fault == 'invalid_span' and job['checks']:
                findings.append(dict(check_id=job['checks'][0]['check_id'], segment_id=content.segments[0].segment_id,
                                     reason_code='GR-003:local_pii:email', start=0, end=len(content.segments[0].text) + 1))
            print(json.dumps({'findings': findings, 'elapsed_ms': (time.monotonic() - started) * 1000}, separators=(',', ':')), flush=True)
        except Exception:
            print('{"error":"evaluation_unavailable"}', flush=True)


if __name__ == '__main__':
    main()
