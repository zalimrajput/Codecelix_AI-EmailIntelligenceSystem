from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class EmailStatus(str, Enum):
    UNREAD = "UNREAD"
    READ = "READ"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"


class ActionStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ReplyTone(str, Enum):
    PROFESSIONAL = "PROFESSIONAL"
    FRIENDLY = "FRIENDLY"
    SHORT = "SHORT"
    DETAILED = "DETAILED"
    APOLOGETIC = "APOLOGETIC"
    FORMAL = "FORMAL"


class EmailCreate(BaseModel):
    sender_email: EmailStr | None = None
    sender_name: str | None = Field(default=None, max_length=255)
    receiver_email: EmailStr | None = None
    receiver_name: str | None = Field(default=None, max_length=255)
    cc: str | None = None
    bcc: str | None = None
    subject: str = Field(min_length=1, max_length=998)
    body_text: str = ""
    body_html: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None
    email_date: datetime | None = None
    status: EmailStatus = EmailStatus.UNREAD
    is_starred: bool = False
    source_type: str = "MANUAL"
    raw_email: str | None = None


class EmailUpdate(BaseModel):
    status: EmailStatus | None = None
    is_starred: bool | None = None
    is_archived: bool | None = None
    is_deleted: bool | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=998)
    body_text: str | None = None


class ReplyRequest(BaseModel):
    tone: ReplyTone = ReplyTone.PROFESSIONAL


class ReplyUpdate(BaseModel):
    edited_reply: str = Field(min_length=1)


class AssistantRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


class ActionItemUpdate(BaseModel):
    status: ActionStatus | None = None
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    due_date: datetime | None = None


class DeadlineUpdate(BaseModel):
    is_completed: bool


class NotificationPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urgent_email: bool = True
    customer_complaint: bool = True
    reply_required: bool = True
    upcoming_deadline: bool = True
    action_item: bool = True
    ai_processing_completed: bool = False
    email_notifications: bool = False
    in_app_notifications: bool = True


class GmailExchange(BaseModel):
    code: str
    state: str


class EmailImportResult(BaseModel):
    imported: int
    failed: int
    errors: list[str]
    emails: list[dict[str, Any]]
