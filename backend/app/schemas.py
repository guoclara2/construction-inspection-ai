# Pydantic 请求模型：入参校验
from pydantic import BaseModel, Field, field_validator
from typing import Literal

class SiteLocation(BaseModel):
    building: str = Field(default="", max_length=100)
    floor: str = Field(default="", max_length=50)
    axis: str = Field(default="", max_length=100)
    chainage: str = Field(default="", max_length=100)
    equipment: str = Field(default="", max_length=100)
    measurement: str = Field(default="", max_length=500)
    annotation: str = Field(default="", max_length=500)
    custom: str = Field(default="", max_length=1000)


class RecordCreate(BaseModel):
    """巡查记录创建：照片为先上传的附件 id（先传后绑，P04 §2.5.1）"""
    item_id: int
    attachment_id: int
    attachment_ids: list[int] = Field(default_factory=list, max_length=8)
    request_key: str | None = Field(default=None, min_length=8, max_length=64)
    location: SiteLocation = Field(default_factory=SiteLocation)
    task_id: int | None = None
    note: str | None = None

    @field_validator("note")
    @classmethod
    def note_len(cls, v: str | None):
        if v and len(v) > 500:
            raise ValueError("备注最长 500 字")
        return v


class OrderDraftIn(BaseModel):
    """工单草稿：巡查员确认时可编辑三段"""
    assignee_id: int
    sla_hours: int | None = None
    problem_location: str | None = None
    rectify_requirement: str | None = None
    basis: str | None = None


class RecordConfirm(BaseModel):
    """人工确认结论"""
    human_verdict: str
    note: str | None = None

    @field_validator("note")
    @classmethod
    def validate_note(cls, value):
        if value is not None and len(value) > 500:
            raise ValueError("备注最长500字")
        return value

    order_draft: OrderDraftIn | None = None

    @field_validator("human_verdict")
    @classmethod
    def valid_verdict(cls, v: str):
        if v not in ("normal", "abnormal"):
            raise ValueError("human_verdict 只能为 normal/abnormal")
        return v


class CloudUploadIn(BaseModel):
    """云托管对象存储上传（微信云托管部署形态）：小程序 wx.cloud.uploadFile 直传得 fileID 后，
    经 callContainer 提交业务字段；与 multipart 通道字段一一对应。"""
    file_id: str = Field(min_length=10, max_length=300)
    file_name: str | None = Field(default=None, max_length=200)
    biz_type: str
    source: str = "unknown"
    client_lat: float | None = None
    client_lng: float | None = None
    accuracy: float | None = None
    shot_at: str | None = None
    coordinate_system: str = "unknown"
    upload_key: str | None = None
