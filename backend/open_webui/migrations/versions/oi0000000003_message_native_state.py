"""Keep native replay state and finish reasons in normalized chat-message reads."""

import json

import sqlalchemy as sa
from alembic import op

revision = 'oi0000000003'
down_revision = 'oi0000000002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_message', sa.Column('oi_state', sa.JSON(), nullable=True))
    chat = sa.table('chat', sa.column('id', sa.Text()), sa.column('chat', sa.JSON()))
    messages = sa.table('chat_message', sa.column('id', sa.Text()), sa.column('oi_state', sa.JSON()))
    bind = op.get_bind()
    for chat_id, body in bind.execute(sa.select(chat.c.id, chat.c.chat)):
        if isinstance(body, str):
            body = json.loads(body)
        for message_id, message in ((body or {}).get('history') or {}).get('messages', {}).items():
            state = {
                key: message[key] for key in ('oi_native', 'oi_native_invalidated', 'finish_reason') if key in message
            }
            if state:
                bind.execute(
                    sa.update(messages)
                    .where(messages.c.id == f'{chat_id}-{message_id}')
                    .where(messages.c.oi_state.is_(None))
                    .values(oi_state=state)
                )


def downgrade():
    raise RuntimeError('Keep native replay state; restore a compatible OI build instead')
