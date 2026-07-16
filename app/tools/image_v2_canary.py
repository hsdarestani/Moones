from __future__ import annotations
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.services import image_pipeline_v2 as v2

FIXTURES = [
    'عکس بده ممه هاتو ببینم', 'عکس بده کصتو ببینم', 'عکس بده لخت باشی توش',
    'یه عکس روی مبل بده', 'دوباره بفرست', 'یکی دیگه مثل قبلی', 'این بار تو کافه',
] * 15

def run_canary(requests=None):
    requests = list(requests or FIXTURES)[:]
    while len(requests) < 100: requests.extend(FIXTURES)
    report={'count':0,'parser_coverage':[],'content_mode_mismatches':0,'generic_fallback_rate':0,'invariant_failures':[],'identity_fingerprint_changes':0,'legacy_v2_semantic_diffs':[]}
    fallback=0
    for raw in requests[:100]:
        intent=v2.parse_image_intent(v2.normalize_request_v2(raw))
        report['count'] += 1
        report['parser_coverage'].append(intent.parse_coverage.__dict__)
        fallback += int(intent.parse_coverage.fallback_required)
        if intent.adult_intent and intent.content_classification == v2.ContentClassification.NORMAL:
            report['content_mode_mismatches'] += 1
    report['generic_fallback_rate']=fallback / max(report['count'],1)
    return report

if __name__ == '__main__':
    print(json.dumps(run_canary(), ensure_ascii=False, indent=2))
