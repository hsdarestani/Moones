"""coin usage billing

Revision ID: 0030_coin_usage_billing
Revises: 0029_time_aware_roleplay
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from decimal import Decimal, ROUND_CEILING
from datetime import datetime

revision="0030_coin_usage_billing"
down_revision="0029_time_aware_roleplay"
branch_labels=None
depends_on=None
DENOM=Decimal(100); SIGNUP=200
# Deterministic migration constants: legacy wallet amounts are Toman-denominated and
# are converted with ceil(amount / 100). SIGNUP is a one-time 200 coin grant.
# Active paid subscriptions are preserved until their existing expires_at; no
# prorated subscription coin credit is issued, so converted_subscription_value=0.

def ceil_coin(v):
    if v is None or Decimal(str(v)) <= 0: return 0
    return int((Decimal(str(v))/DENOM).to_integral_value(rounding=ROUND_CEILING))
def tables(): return set(sa.inspect(op.get_bind()).get_table_names())
def cols(t): return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(t)} if t in tables() else set()
def add(t,c):
    if c.name not in cols(t):
        with op.batch_alter_table(t) as b: b.add_column(c)

def upgrade():
    bind=op.get_bind(); ts=tables()
    for name in ['currency_version','low_balance_notified_level']:
        pass
    add('wallets', sa.Column('currency_version', sa.Integer(), nullable=False, server_default='1'))
    add('wallets', sa.Column('last_recharged_at', sa.DateTime(), nullable=True))
    add('wallet_transactions', sa.Column('unit', sa.String(32), nullable=False, server_default='legacy_toman'))
    add('wallet_transactions', sa.Column('idempotency_key', sa.String(255), nullable=True))
    add('wallet_transactions', sa.Column('usage_charge_id', sa.Integer(), nullable=True))
    add('wallet_transactions', sa.Column('correlation_id', sa.String(255), nullable=True))
    add('users', sa.Column('welcome_coins_granted_at', sa.DateTime(), nullable=True))
    add('users', sa.Column('welcome_coins_amount', sa.Integer(), nullable=True))
    add('users', sa.Column('low_balance_notified_level', sa.Integer(), nullable=True))
    add('addon_products', sa.Column('price_coins', sa.Integer(), nullable=False, server_default='0'))
    add('user_addons', sa.Column('price_paid_coins', sa.Integer(), nullable=True))
    add('payment_receipts', sa.Column('approved_coins', sa.Integer(), nullable=True))
    for col in ['usage_charge_id','charged_coins','exchange_rate_toman','profit_margin_percent','pricing_registry_version','correlation_id']:
        typ = sa.Integer() if col in ['usage_charge_id','charged_coins'] else sa.Numeric() if col in ['exchange_rate_toman','profit_margin_percent'] else sa.Text()
        nullable = col != 'charged_coins'; default = '0' if col == 'charged_coins' else None
        add('ai_usage_events', sa.Column(col, typ, nullable=nullable, server_default=default))
    if 'usage_charges' not in ts:
        op.create_table('usage_charges', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('idempotency_key',sa.String(255),nullable=False,unique=True), sa.Column('correlation_id',sa.String(255)), sa.Column('user_id',sa.Integer(),nullable=False), sa.Column('wallet_id',sa.Integer(),nullable=False), sa.Column('usage_event_id',sa.Integer()), sa.Column('feature',sa.String(64),nullable=False), sa.Column('provider',sa.String(64),nullable=False), sa.Column('model',sa.String(128),nullable=False), sa.Column('status',sa.String(32),nullable=False,server_default='reserved'), sa.Column('reserved_coins',sa.BigInteger(),nullable=False,server_default='0'), sa.Column('charged_coins',sa.BigInteger(),nullable=False,server_default='0'), sa.Column('refunded_coins',sa.BigInteger(),nullable=False,server_default='0'), sa.Column('estimated_cost_usd',sa.Numeric(18,8),nullable=False,server_default='0'), sa.Column('actual_cost_usd',sa.Numeric(18,8),nullable=False,server_default='0'), sa.Column('exchange_rate_toman',sa.Numeric(18,4),nullable=False,server_default='60000'), sa.Column('profit_margin_percent',sa.Numeric(8,2),nullable=False,server_default='100'), sa.Column('toman_per_coin',sa.Integer(),nullable=False,server_default='100'), sa.Column('pricing_snapshot_json',sa.JSON()), sa.Column('request_metadata_json',sa.JSON()), sa.Column('error',sa.Text()), sa.Column('created_at',sa.DateTime(),nullable=False,server_default=sa.func.now()), sa.Column('settled_at',sa.DateTime()), sa.Column('refunded_at',sa.DateTime()))
        op.create_index('ix_usage_charges_correlation_id','usage_charges',['correlation_id'])
    if 'wallet_currency_migrations' not in ts:
        op.create_table('wallet_currency_migrations', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('wallet_id',sa.Integer(),nullable=False,unique=True), sa.Column('previous_balance',sa.BigInteger(),nullable=False,server_default='0'), sa.Column('previous_total_added',sa.BigInteger(),nullable=False,server_default='0'), sa.Column('previous_total_spent',sa.BigInteger(),nullable=False,server_default='0'), sa.Column('converted_balance',sa.BigInteger(),nullable=False,server_default='0'), sa.Column('converted_total_added',sa.BigInteger(),nullable=False,server_default='0'), sa.Column('converted_total_spent',sa.BigInteger(),nullable=False,server_default='0'), sa.Column('conversion_denominator',sa.Integer(),nullable=False,server_default='100'), sa.Column('converted_subscription_value',sa.BigInteger(),nullable=False,server_default='0'), sa.Column('migration_version',sa.String(64),nullable=False,server_default=revision), sa.Column('created_at',sa.DateTime(),nullable=False,server_default=sa.func.now()))
    if 'legacy_subscription_preservations' not in ts:
        op.create_table('legacy_subscription_preservations',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('subscription_id', sa.Integer(), nullable=False, unique=True),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('plan', sa.String(64), nullable=False),
            sa.Column('status', sa.String(32), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=True),
            sa.Column('preservation_policy', sa.String(64), nullable=False, server_default='preserve_until_expiry'),
            sa.Column('converted_subscription_value', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('migration_version', sa.String(64), nullable=False, server_default=revision),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()))
    bind.execute(text("UPDATE wallet_transactions SET unit='legacy_toman' WHERE unit IS NULL OR unit=''"))
    wallets=bind.execute(text("SELECT id,user_id,balance_coins,total_added_coins,total_spent_coins FROM wallets WHERE currency_version < 2")).mappings().all()
    for w in wallets:
        cb,ca,cs=ceil_coin(w.balance_coins),ceil_coin(w.total_added_coins),ceil_coin(w.total_spent_coins)
        bind.execute(text("INSERT INTO wallet_currency_migrations (wallet_id,previous_balance,previous_total_added,previous_total_spent,converted_balance,converted_total_added,converted_total_spent,conversion_denominator,migration_version) SELECT :wid,:pb,:pa,:ps,:cb,:ca,:cs,100,:ver WHERE NOT EXISTS (SELECT 1 FROM wallet_currency_migrations WHERE wallet_id=:wid)"), dict(wid=w.id,pb=w.balance_coins or 0,pa=w.total_added_coins or 0,ps=w.total_spent_coins or 0,cb=cb,ca=ca,cs=cs,ver=revision))
        bind.execute(text("UPDATE wallets SET balance_coins=:cb,total_added_coins=:ca,total_spent_coins=:cs,currency_version=2 WHERE id=:wid AND currency_version<2"), dict(cb=cb,ca=ca,cs=cs,wid=w.id))
    if 'addon_products' in ts: bind.execute(text("UPDATE addon_products SET price_coins = CAST((price_toman + 99) / 100 AS INTEGER) WHERE price_coins=0 AND price_toman>0"))
    users=bind.execute(text("SELECT id FROM users")).mappings().all() if 'users' in ts else []
    for u in users:
        exists=bind.execute(text("SELECT 1 FROM wallet_transactions WHERE user_id=:uid AND reason='signup_welcome_credit'"), {'uid':u.id}).first()
        if exists: continue
        wid=bind.execute(text("SELECT id FROM wallets WHERE user_id=:uid"), {'uid':u.id}).scalar()
        if wid is None:
            bind.execute(text("INSERT INTO wallets (user_id,balance_coins,total_added_coins,total_spent_coins,currency_version,created_at,updated_at) VALUES (:uid,0,0,0,2,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), {'uid':u.id}); wid=bind.execute(text("SELECT id FROM wallets WHERE user_id=:uid"), {'uid':u.id}).scalar()
        bind.execute(text("UPDATE wallets SET balance_coins=balance_coins+:a,total_added_coins=total_added_coins+:a,last_recharged_at=CURRENT_TIMESTAMP WHERE id=:wid"), {'a':SIGNUP,'wid':wid})
        bind.execute(text("INSERT INTO wallet_transactions (user_id,wallet_id,type,amount_coins,balance_after,reason,unit,idempotency_key,created_at) VALUES (:uid,:wid,'credit',:a,(SELECT balance_coins FROM wallets WHERE id=:wid),'signup_welcome_credit','coin',:k,CURRENT_TIMESTAMP)"), {'uid':u.id,'wid':wid,'a':SIGNUP,'k':f'welcome:{u.id}'})
        bind.execute(text("UPDATE users SET welcome_coins_granted_at=CURRENT_TIMESTAMP,welcome_coins_amount=:a WHERE id=:uid"), {'a':SIGNUP,'uid':u.id})
    if 'subscriptions' in ts and 'plan' in cols('subscriptions'):
        # Preserve active paid subscriptions until their existing expiry. This is
        # idempotently audited instead of changing paid statuses to converted_to_coins.
        sub_cols=cols('subscriptions')
        exp=', expires_at' if 'expires_at' in sub_cols else ', NULL AS expires_at'
        paid=bind.execute(text(f"SELECT id,user_id,COALESCE(plan,'unknown') AS plan,status {exp} FROM subscriptions WHERE status IN ('active','trialing') AND COALESCE(plan,'free') <> 'free'")).mappings().all()
        for sub in paid:
            bind.execute(text("INSERT INTO legacy_subscription_preservations (subscription_id,user_id,plan,status,expires_at,preservation_policy,converted_subscription_value,migration_version) SELECT :sid,:uid,:plan,:status,:expires,'preserve_until_expiry',0,:ver WHERE NOT EXISTS (SELECT 1 FROM legacy_subscription_preservations WHERE subscription_id=:sid)"), dict(sid=sub.id,uid=sub.user_id,plan=sub.plan,status=sub.status,expires=sub.expires_at,ver=revision))
        bind.execute(text("UPDATE subscriptions SET status='deprecated_coin_economy' WHERE status IN ('active','trialing') AND COALESCE(plan,'free')='free'"))

def downgrade():
    pass
