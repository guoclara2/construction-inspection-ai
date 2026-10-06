"""Local administrator export; run from backend, explicitly select one project."""
import argparse
import json
from pathlib import Path
from app.database import SessionLocal
from app.services.ai_feedback_dataset import feedback_pairs


def main():
    parser = argparse.ArgumentParser(description='导出 AI 初判与人工确认配对，供专家复核；不自动训练或上传')
    parser.add_argument('--project-id', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    count = 0
    with SessionLocal() as db, args.output.open('x', encoding='utf-8') as output:
        for row in feedback_pairs(db, args.project_id):
            output.write(json.dumps(row, ensure_ascii=False) + '\n')
            count += 1
    print(json.dumps({'pairs': count, 'expert_review_required': True}))


if __name__ == '__main__':
    main()
