"""pipeline: Jev answers attached to a result set

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('result_sets', sa.Column('answers', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('result_sets', 'answers')
