"""Run from backend: python -m scripts.evaluate_ai report.json. Does not call AI or approve reports."""
import argparse
import json
from app.services.ai_acceptance import validate_report, pipeline_fingerprint
from app.config import settings

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('report', nargs='?')
    parser.add_argument('--fingerprint', action='store_true')
    args = parser.parse_args()
    if args.fingerprint:
        print(pipeline_fingerprint(settings.AI_REVIEW_THRESHOLD))
    elif args.report:
        print(json.dumps(validate_report(args.report, settings.AI_REVIEW_THRESHOLD), ensure_ascii=False, indent=2))
    else:
        parser.error('provide a report or --fingerprint')
