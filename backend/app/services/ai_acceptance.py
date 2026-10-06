"""Offline acceptance metrics. Only independently labelled real-model cases qualify."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def evaluate(cases):
    if not cases:
        raise ValueError("评测集为空")
    if len({c['id'] for c in cases}) != len(cases) or len({c.get('image_sha256') for c in cases}) != len(cases):
        raise ValueError("样本编号重复")
    counts = dict(tp=0, tn=0, fp=0, fn=0, unknown=0, positives=0, negatives=0,
                  severe_total=0, severe_detected=0)
    for c in cases:
        if c.get('mode') != 'real' or not c.get('reviewer') or type(c.get('label')) is not bool:
            raise ValueError("每条样本必须为 real、有专业复核人和布尔真值 label")
        if c.get('prediction') not in ('normal', 'abnormal', 'unknown'):
            raise ValueError("prediction 只能为 normal/abnormal/unknown")
        if not isinstance(c.get('image_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', c['image_sha256']):
            raise ValueError("必须记录原图 SHA256")
        positive = c['label']
        predicted = c['prediction']
        counts['positives' if positive else 'negatives'] += 1
        if predicted == 'unknown':
            counts['unknown'] += 1
        if positive:
            counts['tp' if predicted == 'abnormal' else 'fn'] += 1
        elif predicted == 'abnormal':
            counts['fp'] += 1
        elif predicted == 'normal':
            counts['tn'] += 1
        if c.get('severe') and positive:
            counts['severe_total'] += 1
            counts['severe_detected'] += predicted == 'abnormal'
    def ratio(n, d):
        return n / d if d else None
    return dict(counts, samples=len(cases), recall=ratio(counts['tp'], counts['positives']),
                false_positive_rate=ratio(counts['fp'], counts['negatives']),
                unknown_rate=counts['unknown']/len(cases),
                severe_recall=ratio(counts['severe_detected'], counts['severe_total']))


def pipeline_fingerprint(review_threshold=.6):
    """Portable version identity for all prompt/validation code and decision policy."""
    digest = hashlib.sha256()
    for name in ('analyze.py', 'ai_client.py', 'recommend.py', 'ai_acceptance.py'):
        digest.update(name.encode())
        digest.update(Path(__file__).with_name(name).read_text(encoding='utf-8-sig').replace('\r\n', '\n').encode())
    digest.update(json.dumps({'review_threshold': review_threshold}, sort_keys=True).encode())
    return digest.hexdigest()


def _read_valid_report(path, review_threshold=.6):
    if not path:
        raise ValueError("未指定 AI_EVALUATION_REPORT；可保持 AI_PRODUCTION_ENABLED=false 使用人工流程")
    report = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    from ..config import settings
    vision_model = settings.AI_VISION_MODEL
    if report.get('model') != vision_model or not report.get('approved_by') or not report.get('scope'):
        raise ValueError("需当前模型、明确适用范围及专业负责人验收签署")
    scope = report['scope']
    if not isinstance(scope, dict):
        raise ValueError('scope必须明确列出project_ids、item_versions和purposes')
    projects, versions, purposes = (scope.get(k) for k in ('project_ids', 'item_versions', 'purposes'))
    if (not isinstance(projects, list) or not projects or any(type(p) is not int or p < 1 for p in projects)
            or not isinstance(versions, list) or not versions
            or any(not isinstance(v, dict) or any(type(v.get(k)) is not int or v[k] < 1
                   for k in ('item_id', 'version')) for v in versions)
            or not isinstance(purposes, list) or not purposes
            or any(p not in ('analyze', 'recommend') for p in purposes)):
        raise ValueError('评测适用范围无效，不允许通配项目或未指定标准版本')
    if 'recommend' in purposes and report.get('text_model') != settings.AI_TEXT_MODEL:
        raise ValueError('推荐功能须单独声明已验收的text_model')
    pipeline_hash = pipeline_fingerprint(review_threshold)
    if report.get('pipeline_sha256') != pipeline_hash:
        raise ValueError("分析代码或阈值已变化或缺少 pipeline_sha256，请重新评测")
    expires = datetime.fromisoformat(report['expires_at'])
    if expires.tzinfo is None:
        raise ValueError('评测有效期必须包含时区')
    expires = expires.astimezone(timezone.utc)
    if expires <= datetime.now(timezone.utc):
        raise ValueError("评测报告已过期")
    metrics = evaluate(report['cases'])
    # Proposed minimum gate; site owner must additionally approve scope and adequacy.
    if (metrics['samples'] < 100 or metrics['positives'] < 30 or metrics['negatives'] < 30
            or metrics['severe_total'] < 20 or metrics['recall'] < .9
            or metrics['severe_recall'] < .95 or metrics['false_positive_rate'] > .1
            or metrics['unknown_rate'] > .2):
        raise ValueError("未达到项目设定的最低评测门槛；不得用模拟样本代替现场验收")
    return report, metrics


def validate_report(path, review_threshold=.6):
    return _read_valid_report(path, review_threshold)[1]


def ensure_runtime_acceptance(project_id, purpose, *, item_id=None, version=None):
    """Read each time: revocation/expiry/config changes take effect without restart."""
    from ..config import settings
    report, _ = _read_valid_report(settings.AI_EVALUATION_REPORT, settings.AI_REVIEW_THRESHOLD)
    scope = report['scope']
    if project_id not in scope['project_ids'] or purpose not in scope['purposes']:
        raise ValueError('项目或调用用途超出AI验收范围')
    if item_id is not None or version is not None:
        if {'item_id': item_id, 'version': version} not in scope['item_versions']:
            raise ValueError('检查标准版本超出AI验收范围')
    return scope
