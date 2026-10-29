"""Additive OI persistence. Monetary values are exact decimal strings on all dialects."""

from sqlalchemy import BigInteger, Boolean, Column, Integer, JSON, Text, UniqueConstraint

from open_webui.internal.db import Base


class Connection(Base):
    __tablename__ = 'oi_connection'
    id = Column(Text, primary_key=True)
    owner_id = Column(Text, nullable=False, index=True)
    name = Column(Text, nullable=False)
    template = Column(Text, nullable=False)
    protocol = Column(Text, nullable=False)
    transport = Column(Text, nullable=False, default='server', server_default='server')
    base_url = Column(Text, nullable=False)
    credentials = Column(Text, nullable=False, default='')
    settings = Column(JSON, nullable=False, default=dict)
    shared = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)
    legacy_key = Column(Text, unique=True, nullable=True)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class ModelBinding(Base):
    __tablename__ = 'oi_model_binding'
    id = Column(Text, primary_key=True)
    connection_id = Column(Text, nullable=False, index=True)
    upstream_id = Column(Text, nullable=False)
    protocol = Column(Text, nullable=False)
    protocol_override = Column(Text, nullable=True)
    name = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    available = Column(Boolean, nullable=False, default=True)
    capabilities = Column(JSON, nullable=False, default=dict)
    pricing = Column(JSON, nullable=False, default=dict)
    params = Column(JSON, nullable=False, default=dict)
    aliases = Column(JSON, nullable=False, default=list)
    updated_at = Column(BigInteger, nullable=False)


class ConnectionMigration(Base):
    __tablename__ = 'oi_connection_migration'
    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    scope = Column(Text, nullable=False)
    result = Column(JSON, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class ChatDraft(Base):
    __tablename__ = 'oi_chat_draft'
    user_id = Column(Text, primary_key=True)
    chat_id = Column(Text, primary_key=True)
    revision = Column(Integer, nullable=False, default=0)
    payload = Column(JSON, nullable=False, default=dict)
    mutation_id = Column(Text, nullable=True)
    updated_at = Column(BigInteger, nullable=False)


class ChatLedger(Base):
    __tablename__ = 'oi_chat_ledger'
    chat_id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False, index=True)
    sequence = Column(Integer, nullable=False, default=0)
    inherited_snapshot_id = Column(Text, nullable=True)


class Generation(Base):
    __tablename__ = 'oi_generation'
    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False, index=True)
    chat_id = Column(Text, nullable=False, index=True)
    message_id = Column(Text, nullable=True, index=True)
    request_id = Column(Text, nullable=False)
    kind = Column(Text, nullable=False, default='assistant')
    status = Column(Text, nullable=False, default='pending')
    created_at = Column(BigInteger, nullable=False)
    completed_at = Column(BigInteger, nullable=True)
    __table_args__ = (UniqueConstraint('user_id', 'request_id', name='uq_oi_generation_request'),)


class ModelCall(Base):
    __tablename__ = 'oi_model_call'
    id = Column(Text, primary_key=True)
    generation_id = Column(Text, nullable=False, index=True)
    chat_id = Column(Text, nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    binding_id = Column(Text, nullable=True)
    connection_id = Column(Text, nullable=True)
    model_id = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='pending')
    usage = Column(JSON, nullable=False, default=dict)
    actual_usd = Column(Text, nullable=True)
    estimated_usd = Column(Text, nullable=True)
    pricing = Column(JSON, nullable=False, default=dict)
    source = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    completed_at = Column(BigInteger, nullable=True)
    __table_args__ = (UniqueConstraint('chat_id', 'sequence', name='uq_oi_call_sequence'),)


class CostSnapshot(Base):
    __tablename__ = 'oi_cost_snapshot'
    id = Column(Text, primary_key=True)
    chat_id = Column(Text, nullable=False, index=True)
    message_id = Column(Text, nullable=True, index=True)
    generation_id = Column(Text, nullable=True, unique=True)
    max_sequence = Column(Integer, nullable=False)
    inherited_snapshot_id = Column(Text, nullable=True)
    baseline_ids = Column(JSON, nullable=False, default=list)
    totals = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class ImportCostBaseline(Base):
    __tablename__ = 'oi_import_cost_baseline'
    id = Column(Text, primary_key=True)
    chat_id = Column(Text, nullable=False, index=True)
    batch_id = Column(Text, nullable=False)
    actual_usd = Column(Text, nullable=True)
    estimated_usd = Column(Text, nullable=True)
    note = Column(Text, nullable=False, default='')
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    __table_args__ = (UniqueConstraint('chat_id', 'batch_id', name='uq_oi_import_batch'),)


class ChatAnnotation(Base):
    __tablename__ = 'oi_chat_annotation'
    user_id = Column(Text, primary_key=True)
    chat_id = Column(Text, primary_key=True)
    revision = Column(Integer, nullable=False, default=0)
    data = Column(JSON, nullable=False, default=dict)
    updated_at = Column(BigInteger, nullable=False)


class MessageRevision(Base):
    __tablename__ = 'oi_message_revision'
    id = Column(Text, primary_key=True)
    chat_id = Column(Text, nullable=False, index=True)
    message_id = Column(Text, nullable=False, index=True)
    user_id = Column(Text, nullable=False)
    content = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class UserStateLock(Base):
    __tablename__ = 'oi_user_state_lock'
    user_id = Column(Text, primary_key=True)
    revision = Column(Integer, nullable=False, default=0)


class ImportReceipt(Base):
    __tablename__ = 'oi_import_receipt'
    user_id = Column(Text, primary_key=True)
    import_id = Column(Text, primary_key=True)
    digest = Column(Text, nullable=False)
    chat_ids = Column(JSON, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class ArchiveOrigin(Base):
    __tablename__ = 'oi_archive_origin'
    user_id = Column(Text, primary_key=True)
    kind = Column(Text, primary_key=True)
    local_id = Column(Text, primary_key=True)
    source_id = Column(Text, nullable=False)
    __table_args__ = (UniqueConstraint('user_id', 'kind', 'source_id', name='uq_oi_archive_origin'),)
