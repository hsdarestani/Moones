#!/usr/bin/env python
from __future__ import annotations
import os
from decimal import Decimal, ROUND_CEILING
from sqlalchemy import create_engine, text
DENOM=Decimal(100)
def ceil_coin(v):
    if v is None or Decimal(str(v)) <= 0: return 0
    return int((Decimal(str(v))/DENOM).to_integral_value(rounding=ROUND_CEILING))
def main():
    url=os.environ.get('DATABASE_URL','sqlite:///./app.db')
    safe=url.split('@')[-1] if '@' in url else url.split('://')[0]+'://…'
    engine=create_engine(url)
    with engine.connect() as c:
        
        names={r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()} if engine.dialect.name == 'sqlite' else {r[0] for r in c.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).all()}
        if 'wallets' not in names:
            print(f'database={safe}')
            print('legacy_wallets=0')
            print('warning=no wallets table found; run against a migrated fixture or production replica')
            return
        rows=c.execute(text('SELECT balance_coins,total_added_coins,total_spent_coins FROM wallets WHERE COALESCE(currency_version,1)<2')).mappings().all()
        before=sum(r.balance_coins or 0 for r in rows); after=sum(ceil_coin(r.balance_coins) for r in rows)
        print(f'database={safe}')
        print(f'legacy_wallets={len(rows)}')
        print(f'legacy_balance_toman_total={before}')
        print(f'converted_balance_coins_total={after}')
        print(f'conversion=ceil(legacy_toman / 100); example 590000 -> {ceil_coin(590000)} coins; 590001 -> {ceil_coin(590001)} coins')
if __name__=='__main__': main()
