"""accounts: roles, invites and reset links, instance settings, demo query vectors

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column('users', sa.Column('name', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('role', sa.Text(), server_default='user', nullable=False))
    op.add_column('users', sa.Column('disabled', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('users', sa.Column('last_login_at', TZ, nullable=True))
    op.add_column('users', sa.Column('onboarding_completed', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('users', sa.Column('onboarding', postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False))
    # Until now there was one account, the admin, and it is already in use.
    op.execute("UPDATE users SET role = 'admin', onboarding_completed = true")
    op.create_table(
        'auth_links',
        sa.Column('id', sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('token_hash', sa.Text(), nullable=False, unique=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('created_by', sa.BigInteger(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', TZ, server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', TZ, nullable=False),
        sa.Column('used_at', TZ, nullable=True),
    )
    op.create_table(
        'instance_settings',
        sa.Column('key', sa.Text(), primary_key=True),
        sa.Column('value', postgresql.JSONB(), nullable=True),
    )
    op.create_table(
        'demo_query_vectors',
        sa.Column('query', sa.Text(), primary_key=True),
        sa.Column('model', sa.Text(), primary_key=True),
        sa.Column('vector', HALFVEC(1024), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('demo_query_vectors')
    op.drop_table('instance_settings')
    op.drop_table('auth_links')
    for column in ('onboarding', 'onboarding_completed', 'last_login_at', 'disabled', 'role', 'name'):
        op.drop_column('users', column)
