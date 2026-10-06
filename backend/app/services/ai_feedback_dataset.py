"""Human-confirmed pairs for expert review; never automatically train on these labels."""
from ..models import InspectionRecord, Attachment
from ..utils import loads_safe, fmt


def feedback_pairs(db, project_id):
    records = db.query(InspectionRecord).filter(
        InspectionRecord.project_id == project_id,
        InspectionRecord.human_verdict.in_(['normal', 'abnormal']),
        InspectionRecord.ai_mode == 'real',
    ).order_by(InspectionRecord.id)
    for record in records:
        ai = loads_safe(record.ai_result) or {}
        initial = ai.get('preliminary', ai)
        prediction = initial.get('has_defect')
        human = record.human_verdict == 'abnormal'
        photos = db.query(Attachment).filter_by(project_id=project_id, biz_type='record',
            biz_id=record.id, deleted_at=None).order_by(Attachment.id).all()
        yield {
            'record_id': record.id, 'project_id': project_id,
            'item_id': record.item_id, 'item_snapshot': record.item_snapshot,
            'photo_hashes': [p.sha256 for p in photos],
            'initial_ai': initial, 'ai_evidence': ai,
            'human_verdict': record.human_verdict, 'human_note': record.note,
            'confirmed_by': record.inspector_id, 'record_created_at': fmt(record.created_at),
            'disagreement': prediction != human if type(prediction) is bool else None,
            'review_status': 'pending_expert_review',
            'eligible_for_training': False,
        }
