# Design an API for uploading and processing a document.
#  a document can be of certain types, such as PDF, Word, or image files.
#  the API should be able to process the document and extract text from it.
#  a POST 201 (completed) or 202 response for user being made aware that process has started
#  record log in DB documentation tracking location to S3 bucket for record management

import uuid
from datetime import datetime, timezone

import boto3
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Column, String, Boolean, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
import logging
db = SQLAlchemy()


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------

class IDMixin:
    """Adds a UUID primary key."""
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)


class CreatedByMixin:
    """Tracks creation and last-update timestamps plus the actor."""
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    created_by = Column(String(255), nullable=True)


class DeletedMixin:
    """Soft-delete support."""
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(255), nullable=True)

    def soft_delete(self, actor: str | None = None):
        self.is_deleted = True
        self.deleted_at = datetime.now(timezone.utc)
        self.deleted_by = actor


class ArchivedMixin(IDMixin, CreatedByMixin, DeletedMixin):
    """Composes IDMixin + CreatedByMixin + DeletedMixin into one base."""
    is_archived = Column(Boolean, default=False, nullable=False)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by = Column(String(255), nullable=True)

    def archive(self, actor: str | None = None):
        self.is_archived = True
        self.archived_at = datetime.now(timezone.utc)
        self.archived_by = actor


# ---------------------------------------------------------------------------
# Domain enums / value objects
# ---------------------------------------------------------------------------

class DocumentType:
    PDF = 'pdf'
    WORD = 'word'
    IMAGE = 'image'
    DOCX = 'docx'

    ALL = {PDF, WORD, IMAGE, DOCX}


class UploadStatuses:
    cancelled = "cancelled"
    completed = "completed"
    pending = "pending"
    failure = "failure"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class HoyDocumentProcessingLogs(ArchivedMixin, db.Model):
    """Persists a processing-log entry for a document, stored under a
    deterministic S3 path per tenant."""

    __tablename__ = 'hoy_document_processing_logs'

    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    document_type = Column(String(20), nullable=False)
    storage_path = Column(Text, nullable=False)
    status = Column(String(50), nullable=False, default='pending')
    error_message = Column(Text, nullable=True)

    def __init__(self, tenant_id, document_id, document_type):
        self.tenant_id = tenant_id
        self.document_id = document_id
        self.document_type = document_type
        self.storage_path = f'{tenant_id}/processing_logs/{document_id}'

    def __repr__(self):
        return f'<HoyDocumentProcessingLogs doc={self.document_id} status={self.status}>'


class HoyDocumentUpload(ArchivedMixin, db.Model):
    __tablename__ = 'hoy_document_uploads'

    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    document_type = Column(String(20), nullable=False)
    storage_path = Column(Text, nullable=False)
    processing_log = Column()

    _STORAGE_PATH_PATTERN = '{tenant_id}/documents/{document_id}'

    def __init__(self, tenant_id, document_id, document_type):
        self.validate_document_type(document_type)
        self.tenant_id = tenant_id
        self.document_id = document_id
        self.document_type = document_type
        self.storage_path = self._STORAGE_PATH_PATTERN.format(
            tenant_id=tenant_id, document_id=document_id
        )

    @staticmethod
    def validate_document_type(document_type: str) -> None:
        if document_type not in DocumentType.ALL:
            raise ValueError(f'Invalid document type: {document_type!r}. Expected one of {DocumentType.ALL}')


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

class S3:
    def __init__(self, bucket_name):
        self.bucket_name = bucket_name
        self.s3_client = boto3.client('s3')

    def upload_file(self, file_name, object_key):
        self.s3_client.upload_file(file_name, self.bucket_name, object_key)

    def multipart_upload(self, file_name, object_key):
        pass

    def generate_presigned_url(self, expiration=3600):
        return self.s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': self.bucket_name, 'Key': self.object_key},
            ExpiresIn=expiration,
        )

    def delete_object(self, object_key):
        self.s3_client.delete_object(Bucket=self.bucket_name, Key=object_key)

    def complete_upload(self, object_key):
        return self.s3_client.complete_multipart_upload(Bucket=self.bucket_name, Key=object_key)


def create_record(local_path, tenant_id, document_id):

    s3 = S3()
    try:
        s3.upload_file(local_path, f'{tenant_id}/documents/{document_id}')
        HoyDocumentProcessingLogs.create(tenant_id=tenant_id,
                                         document_id=document_id,
                                         document_type='pdf',
                                         storage_path=f'{tenant_id}/documents/{document_id}',
                                         status='completed')
    except Exception as exc:
        logging.error(f'Error uploading file to S3', exc_info=exc)
        raise exc

    

if __name__ == '__main__':
    create_record('test.pdf', '12345678-1234-1234-1234-123456789012', '12345678-1234-1234-1234-123456789012')

