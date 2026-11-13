"""enquiries, client free trial, session revocation

Revision ID: 0005
Revises: 0004
"""

from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('enquiries',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('topic', sa.String(length=16), nullable=True),
    sa.Column('client_id', sa.String(length=64), nullable=True),
    sa.Column('person_name', sa.String(length=128), nullable=False),
    sa.Column('phone_number', sa.String(length=20), nullable=True),
    sa.Column('whatsapp_number', sa.String(length=20), nullable=True),
    sa.Column('business_name', sa.String(length=128), nullable=True),
    sa.Column('business_type', sa.String(length=64), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_enquiries_client_id'), 'enquiries', ['client_id'], unique=False)
    op.create_index(op.f('ix_enquiries_status'), 'enquiries', ['status'], unique=False)
    op.add_column('clients', sa.Column('is_trial', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('sessions_valid_after', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'sessions_valid_after')
    op.drop_column('clients', 'is_trial')
    op.drop_index(op.f('ix_enquiries_status'), table_name='enquiries')
    op.drop_index(op.f('ix_enquiries_client_id'), table_name='enquiries')
    op.drop_table('enquiries')
