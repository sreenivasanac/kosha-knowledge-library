"""initial schema

Revision ID: 0001
Revises: 
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType


class Ltree(UserDefinedType):
    """Local copy so this migration does not depend on the models module."""

    cache_ok = True

    def get_col_spec(self, **kw):
        return "LTREE"


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extensions: vectors, fuzzy match, tag paths.
    for ext in ("vector", "pg_trgm", "ltree"):
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
    op.execute("CREATE SEQUENCE items_seq")
    op.execute("CREATE SEQUENCE tags_id_seq")
    op.create_table('users',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('api_tokens',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_api_tokens_user_id'), 'api_tokens', ['user_id'], unique=False)
    op.create_table('captures',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('payload_hash', sa.Text(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('parse_error', sa.Text(), nullable=True),
    sa.Column('items_new', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('items_updated', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('items_unchanged', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'payload_hash')
    )
    op.create_index(op.f('ix_captures_user_id'), 'captures', ['user_id'], unique=False)
    op.create_table('collections',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('source_id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'source', 'source_id')
    )
    op.create_index(op.f('ix_collections_user_id'), 'collections', ['user_id'], unique=False)
    op.create_table('deleted_items',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('source_id', sa.Text(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'source', 'source_id')
    )
    op.create_index(op.f('ix_deleted_items_user_id'), 'deleted_items', ['user_id'], unique=False)
    op.create_table('items',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('seq', sa.BigInteger(), server_default=sa.text("nextval('items_seq')"), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('source_id', sa.Text(), nullable=False),
    sa.Column('is_saved', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('saved_order', sa.BigInteger(), nullable=True),
    sa.Column('saved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('saved_at_est', sa.DateTime(timezone=True), nullable=True),
    sa.Column('captured_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('content_types', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('author_name', sa.Text(), nullable=True),
    sa.Column('author_handle', sa.Text(), nullable=True),
    sa.Column('author_avatar', sa.Text(), nullable=True),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lang', sa.Text(), nullable=True),
    sa.Column('likes', sa.BigInteger(), nullable=True),
    sa.Column('reposts', sa.BigInteger(), nullable=True),
    sa.Column('replies', sa.BigInteger(), nullable=True),
    sa.Column('views', sa.BigInteger(), nullable=True),
    sa.Column('duration_s', sa.Integer(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('summary_method', sa.Text(), nullable=True),
    sa.Column('summary_status', sa.Text(), server_default='none', nullable=False),
    sa.Column('starred', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('content_hash', sa.Text(), nullable=True),
    sa.Column('unavailable_reason', sa.Text(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('tsv', postgresql.TSVECTOR(), sa.Computed("\nsetweight(to_tsvector('simple', coalesce(title, '')), 'A') ||\nsetweight(to_tsvector('simple', coalesce(author_name, '') || ' ' || coalesce(author_handle, '')), 'A') ||\nsetweight(to_tsvector(CASE WHEN lang = 'en' THEN 'english'::regconfig ELSE 'simple'::regconfig END,\n                      coalesce(body, '')), 'B') ||\nsetweight(to_tsvector('simple', coalesce(summary, '')), 'D')\n", persisted=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('seq'),
    sa.UniqueConstraint('user_id', 'source', 'source_id')
    )
    op.create_index('ix_items_body_trgm', 'items', ['body'], unique=False, postgresql_using='gin', postgresql_ops={'body': 'gin_trgm_ops'})
    op.create_index('ix_items_order', 'items', ['user_id', sa.literal_column('saved_order DESC NULLS FIRST'), sa.literal_column('seq DESC')], unique=False)
    op.create_index('ix_items_title_trgm', 'items', ['title'], unique=False, postgresql_using='gin', postgresql_ops={'title': 'gin_trgm_ops'})
    op.create_index('ix_items_tsv', 'items', ['tsv'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_items_user_id'), 'items', ['user_id'], unique=False)
    op.create_table('jev_questions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('question_hash', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'question_hash')
    )
    op.create_index(op.f('ix_jev_questions_user_id'), 'jev_questions', ['user_id'], unique=False)
    op.create_table('keybindings',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('keys', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'action')
    )
    op.create_table('label_runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('strategy', sa.Text(), nullable=False),
    sa.Column('namespace', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='running', nullable=False),
    sa.Column('coverage', sa.Float(), nullable=True),
    sa.Column('spend_usd', sa.Numeric(precision=12, scale=6), server_default=sa.text('0'), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_label_runs_user_id'), 'label_runs', ['user_id'], unique=False)
    op.create_table('lists',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_lists_user_id'), 'lists', ['user_id'], unique=False)
    op.create_table('result_sets',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('query', sa.Text(), nullable=True),
    sa.Column('item_ids', postgresql.ARRAY(sa.BigInteger()), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'name')
    )
    op.create_index(op.f('ix_result_sets_user_id'), 'result_sets', ['user_id'], unique=False)
    op.create_table('saved_views',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('query', sa.Text(), server_default='', nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_saved_views_user_id'), 'saved_views', ['user_id'], unique=False)
    op.create_table('settings',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'key')
    )
    op.create_table('spend_ledger',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('provider', sa.Text(), nullable=False),
    sa.Column('operation', sa.Text(), nullable=False),
    sa.Column('units', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('amount_usd', sa.Numeric(precision=12, scale=6), nullable=False),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_spend_ledger_user_id'), 'spend_ledger', ['user_id'], unique=False)
    op.create_table('sync_state',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'source')
    )
    op.create_table('tags',
    sa.Column('id', sa.BigInteger(), server_default=sa.text("nextval('tags_id_seq')"), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('path', Ltree(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('slug', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default='approved', nullable=False),
    sa.Column('origin', sa.Text(), server_default='human', nullable=False),
    sa.Column('namespace', sa.Text(), server_default='main', nullable=False),
    sa.Column('color', sa.Text(), nullable=True),
    sa.Column('sort', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['tags.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'namespace', 'parent_id', 'slug', postgresql_nulls_not_distinct=True)
    )
    op.create_index(op.f('ix_tags_parent_id'), 'tags', ['parent_id'], unique=False)
    op.create_index('ix_tags_path', 'tags', ['path'], unique=False, postgresql_using='gist')
    op.create_index(op.f('ix_tags_user_id'), 'tags', ['user_id'], unique=False)
    op.create_table('undo_batches',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_undo_batches_user_id'), 'undo_batches', ['user_id'], unique=False)
    op.create_table('item_collections',
    sa.Column('item_id', sa.BigInteger(), nullable=False),
    sa.Column('collection_id', sa.BigInteger(), nullable=False),
    sa.Column('position', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['collection_id'], ['collections.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('item_id', 'collection_id')
    )
    op.create_index(op.f('ix_item_collections_collection_id'), 'item_collections', ['collection_id'], unique=False)
    op.create_table('item_embeddings',
    sa.Column('item_id', sa.BigInteger(), nullable=False),
    sa.Column('model', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.Text(), nullable=False),
    sa.Column('vector', HALFVEC(dim=1024), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('item_id', 'model')
    )
    op.create_table('item_links',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('item_id', sa.BigInteger(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('domain', sa.Text(), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('image_url', sa.Text(), nullable=True),
    sa.Column('position', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('item_id', 'url')
    )
    op.create_index(op.f('ix_item_links_domain'), 'item_links', ['domain'], unique=False)
    op.create_index(op.f('ix_item_links_item_id'), 'item_links', ['item_id'], unique=False)
    op.create_table('item_media',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('item_id', sa.BigInteger(), nullable=False),
    sa.Column('source_key', sa.Text(), nullable=False),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('mime_type', sa.Text(), nullable=True),
    sa.Column('position', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('original_url', sa.Text(), nullable=True),
    sa.Column('preview_url', sa.Text(), nullable=True),
    sa.Column('variants', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('local_path', sa.Text(), nullable=True),
    sa.Column('local_preview_path', sa.Text(), nullable=True),
    sa.Column('bytes', sa.BigInteger(), nullable=True),
    sa.Column('duration_ms', sa.BigInteger(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('alt_text', sa.Text(), nullable=True),
    sa.Column('extract_text', sa.Text(), nullable=True),
    sa.Column('extract_kind', sa.Text(), nullable=True),
    sa.Column('extract_format', sa.Text(), nullable=True),
    sa.Column('extract_status', sa.Text(), server_default='none', nullable=False),
    sa.Column('extract_model', sa.Text(), nullable=True),
    sa.Column('extract_lang', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('item_id', 'source_key')
    )
    op.create_index(op.f('ix_item_media_item_id'), 'item_media', ['item_id'], unique=False)
    op.create_table('item_relations',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('from_item_id', sa.BigInteger(), nullable=False),
    sa.Column('relation', sa.Text(), nullable=False),
    sa.Column('to_item_id', sa.BigInteger(), nullable=True),
    sa.Column('to_source_id', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=True),
    sa.Column('target_state', sa.Text(), server_default='missing', nullable=False),
    sa.ForeignKeyConstraint(['from_item_id'], ['items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_item_id'], ['items.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('from_item_id', 'relation', 'to_source_id')
    )
    op.create_index(op.f('ix_item_relations_from_item_id'), 'item_relations', ['from_item_id'], unique=False)
    op.create_index(op.f('ix_item_relations_to_item_id'), 'item_relations', ['to_item_id'], unique=False)
    op.create_index(op.f('ix_item_relations_to_source_id'), 'item_relations', ['to_source_id'], unique=False)
    op.create_table('item_tags',
    sa.Column('item_id', sa.BigInteger(), nullable=False),
    sa.Column('tag_id', sa.BigInteger(), nullable=False),
    sa.Column('namespace', sa.Text(), server_default='main', nullable=False),
    sa.Column('role', sa.Text(), server_default='other', nullable=False),
    sa.Column('attached_by', sa.Text(), server_default='human', nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('removed_by_user', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('item_id', 'tag_id')
    )
    op.create_index('ix_item_tags_tag_item', 'item_tags', ['tag_id', 'item_id'], unique=False)
    op.create_index('uq_item_tags_primary', 'item_tags', ['item_id', 'namespace'], unique=True, postgresql_where=sa.text("role = 'primary' AND NOT removed_by_user"))
    op.create_index('uq_item_tags_secondary', 'item_tags', ['item_id', 'namespace'], unique=True, postgresql_where=sa.text("role = 'secondary' AND NOT removed_by_user"))
    op.create_table('jev_answers',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('question_id', sa.BigInteger(), nullable=False),
    sa.Column('item_id', sa.BigInteger(), nullable=False),
    sa.Column('summary_hash', sa.Text(), nullable=False),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('answer', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id'], ['jev_questions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('question_id', 'item_id', 'summary_hash')
    )
    op.create_index(op.f('ix_jev_answers_item_id'), 'jev_answers', ['item_id'], unique=False)
    op.create_table('list_items',
    sa.Column('list_id', sa.BigInteger(), nullable=False),
    sa.Column('item_id', sa.BigInteger(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=True),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['list_id'], ['lists.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('list_id', 'item_id')
    )


def downgrade() -> None:
    op.drop_table('list_items')
    op.drop_index(op.f('ix_jev_answers_item_id'), table_name='jev_answers')
    op.drop_table('jev_answers')
    op.drop_index('uq_item_tags_secondary', table_name='item_tags', postgresql_where=sa.text("role = 'secondary' AND NOT removed_by_user"))
    op.drop_index('uq_item_tags_primary', table_name='item_tags', postgresql_where=sa.text("role = 'primary' AND NOT removed_by_user"))
    op.drop_index('ix_item_tags_tag_item', table_name='item_tags')
    op.drop_table('item_tags')
    op.drop_index(op.f('ix_item_relations_to_source_id'), table_name='item_relations')
    op.drop_index(op.f('ix_item_relations_to_item_id'), table_name='item_relations')
    op.drop_index(op.f('ix_item_relations_from_item_id'), table_name='item_relations')
    op.drop_table('item_relations')
    op.drop_index(op.f('ix_item_media_item_id'), table_name='item_media')
    op.drop_table('item_media')
    op.drop_index(op.f('ix_item_links_item_id'), table_name='item_links')
    op.drop_index(op.f('ix_item_links_domain'), table_name='item_links')
    op.drop_table('item_links')
    op.drop_table('item_embeddings')
    op.drop_index(op.f('ix_item_collections_collection_id'), table_name='item_collections')
    op.drop_table('item_collections')
    op.drop_index(op.f('ix_undo_batches_user_id'), table_name='undo_batches')
    op.drop_table('undo_batches')
    op.drop_index(op.f('ix_tags_user_id'), table_name='tags')
    op.drop_index('ix_tags_path', table_name='tags', postgresql_using='gist')
    op.drop_index(op.f('ix_tags_parent_id'), table_name='tags')
    op.drop_table('tags')
    op.drop_table('sync_state')
    op.drop_index(op.f('ix_spend_ledger_user_id'), table_name='spend_ledger')
    op.drop_table('spend_ledger')
    op.drop_table('settings')
    op.drop_index(op.f('ix_saved_views_user_id'), table_name='saved_views')
    op.drop_table('saved_views')
    op.drop_index(op.f('ix_result_sets_user_id'), table_name='result_sets')
    op.drop_table('result_sets')
    op.drop_index(op.f('ix_lists_user_id'), table_name='lists')
    op.drop_table('lists')
    op.drop_index(op.f('ix_label_runs_user_id'), table_name='label_runs')
    op.drop_table('label_runs')
    op.drop_table('keybindings')
    op.drop_index(op.f('ix_jev_questions_user_id'), table_name='jev_questions')
    op.drop_table('jev_questions')
    op.drop_index(op.f('ix_items_user_id'), table_name='items')
    op.drop_index('ix_items_tsv', table_name='items', postgresql_using='gin')
    op.drop_index('ix_items_title_trgm', table_name='items', postgresql_using='gin', postgresql_ops={'title': 'gin_trgm_ops'})
    op.drop_index('ix_items_order', table_name='items')
    op.drop_index('ix_items_body_trgm', table_name='items', postgresql_using='gin', postgresql_ops={'body': 'gin_trgm_ops'})
    op.drop_table('items')
    op.drop_index(op.f('ix_deleted_items_user_id'), table_name='deleted_items')
    op.drop_table('deleted_items')
    op.drop_index(op.f('ix_collections_user_id'), table_name='collections')
    op.drop_table('collections')
    op.drop_index(op.f('ix_captures_user_id'), table_name='captures')
    op.drop_table('captures')
    op.drop_index(op.f('ix_api_tokens_user_id'), table_name='api_tokens')
    op.drop_table('api_tokens')
    op.drop_table('users')
    op.execute("DROP SEQUENCE items_seq")
    op.execute("DROP SEQUENCE tags_id_seq")
