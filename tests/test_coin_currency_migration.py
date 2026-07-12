from decimal import Decimal, ROUND_CEILING
from app.services.wallet_service import grant_signup_welcome_credit
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models import User, Wallet, WalletTransaction
from app.models.settings import AppSetting

def cc(v): return int((Decimal(v)/Decimal(100)).to_integral_value(rounding=ROUND_CEILING))
def test_legacy_590000_converts_to_5900(): assert cc(590000) == 5900
def test_legacy_ceiling_preserves_value(): assert cc(590001) == 5901

def test_welcome_200_once_and_units():
    e=create_engine('sqlite:///:memory:'); Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, AppSetting.__table__]); db=sessionmaker(bind=e)()
    u=User(telegram_id=9); db.add(u); db.flush(); db.add(Wallet(user_id=u.id,balance_coins=0)); db.flush()
    grant_signup_welcome_credit(db,u); grant_signup_welcome_credit(db,u)
    assert u.wallet.balance_coins == 200
    tx=db.query(WalletTransaction).filter_by(reason='signup_welcome_credit').all()
    assert len(tx)==1 and tx[0].unit == 'coin'
