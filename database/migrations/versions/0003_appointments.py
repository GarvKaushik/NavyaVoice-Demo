"""appointments: demo bookings keep their client, campaign and time, and generated slots are saved too

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('demo_bookings', sa.Column('client_id', sa.String(length=64), nullable=True))
    op.add_column('demo_bookings', sa.Column('campaign_id', sa.String(length=64), nullable=True))
    op.add_column('demo_bookings', sa.Column('starts_at', sa.DateTime(), nullable=True))
    op.add_column('demo_bookings', sa.Column('duration_minutes', sa.Integer(), nullable=True))
    op.execute("""
        UPDATE demo_bookings SET client_id = demo_slots.client_id, starts_at = demo_slots.starts_at,
                                 duration_minutes = demo_slots.duration_minutes
        FROM demo_slots WHERE demo_slots.id = demo_bookings.slot_id
    """)
    op.execute("""
        UPDATE demo_bookings SET campaign_id = leads.campaign_id
        FROM leads WHERE leads.id = demo_bookings.lead_id
    """)
    op.alter_column('demo_bookings', 'client_id', nullable=False)
    op.alter_column('demo_bookings', 'starts_at', nullable=False)
    op.alter_column('demo_bookings', 'slot_id', nullable=True)
    op.create_index(op.f('ix_demo_bookings_client_id'), 'demo_bookings', ['client_id'], unique=False)
    op.create_index(op.f('ix_demo_bookings_campaign_id'), 'demo_bookings', ['campaign_id'], unique=False)
    op.create_index(op.f('ix_demo_bookings_starts_at'), 'demo_bookings', ['starts_at'], unique=False)
    op.create_foreign_key('fk_demo_bookings_client', 'demo_bookings', 'clients', ['client_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_demo_bookings_client', 'demo_bookings', type_='foreignkey')
    op.drop_index(op.f('ix_demo_bookings_starts_at'), table_name='demo_bookings')
    op.drop_index(op.f('ix_demo_bookings_campaign_id'), table_name='demo_bookings')
    op.drop_index(op.f('ix_demo_bookings_client_id'), table_name='demo_bookings')
    op.execute("DELETE FROM demo_bookings WHERE slot_id IS NULL")
    op.alter_column('demo_bookings', 'slot_id', nullable=False)
    op.drop_column('demo_bookings', 'duration_minutes')
    op.drop_column('demo_bookings', 'starts_at')
    op.drop_column('demo_bookings', 'campaign_id')
    op.drop_column('demo_bookings', 'client_id')
