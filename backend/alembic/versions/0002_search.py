"""search: maintained text-search columns, snapshot scores, search history

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `tsv` was generated from the row alone; search also needs quoted text,
    # link titles and media extracts, so the columns are now written by
    # `kosha reindex` / ingest (kosha.search.index). Run `kosha reindex` once
    # after this migration.
    op.drop_index('ix_items_tsv', table_name='items')
    op.drop_column('items', 'tsv')
    op.add_column('items', sa.Column('search_text', sa.Text(), nullable=True))
    op.add_column('items', sa.Column('tsv', postgresql.TSVECTOR(), nullable=True))
    op.add_column('items', sa.Column('tsv_extra', postgresql.TSVECTOR(), nullable=True))
    op.create_index('ix_items_tsv', 'items', ['tsv'], postgresql_using='gin')
    op.create_index('ix_items_tsv_extra', 'items', ['tsv_extra'], postgresql_using='gin')
    # The trigram index moves from body and title to the combined search text.
    op.drop_index('ix_items_body_trgm', table_name='items')
    op.drop_index('ix_items_title_trgm', table_name='items')
    op.create_index('ix_items_search_text_trgm', 'items', ['search_text'], postgresql_using='gin',
                    postgresql_ops={'search_text': 'gin_trgm_ops'})
    op.add_column('result_sets', sa.Column('scores', postgresql.ARRAY(sa.Float()), nullable=True))
    op.create_table('search_history',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('query', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'query')
    )
    op.create_index(op.f('ix_search_history_user_id'), 'search_history', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_table('search_history')
    op.drop_column('result_sets', 'scores')
    op.drop_index('ix_items_search_text_trgm', table_name='items')
    op.drop_index('ix_items_tsv_extra', table_name='items')
    op.drop_index('ix_items_tsv', table_name='items')
    for column in ('tsv_extra', 'tsv', 'search_text'):
        op.drop_column('items', column)
    op.execute("""ALTER TABLE items ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(author_name, '') || ' ' || coalesce(author_handle, '')), 'A') ||
        setweight(to_tsvector(CASE WHEN lang = 'en' THEN 'english'::regconfig ELSE 'simple'::regconfig END,
                              coalesce(body, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(summary, '')), 'D')) STORED""")
    op.create_index('ix_items_tsv', 'items', ['tsv'], postgresql_using='gin')
    op.create_index('ix_items_body_trgm', 'items', ['body'], postgresql_using='gin', postgresql_ops={'body': 'gin_trgm_ops'})
    op.create_index('ix_items_title_trgm', 'items', ['title'], postgresql_using='gin', postgresql_ops={'title': 'gin_trgm_ops'})
