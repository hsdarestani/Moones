#!/usr/bin/env python
from __future__ import annotations
import os
from decimal import Decimal, ROUND_CEILING
from sqlalchemy import create_engine, inspect, text
DENOM=Decimal(100)
def ceil_coin(v):
    if v is None or Decimal(str(v)) <= 0: return 0
    return int((Decimal(str(v))/DENOM).to_integral_value(rounding=ROUND_CEILING))
def _table_names(conn):
    return set(inspect(conn).get_table_names())
def _cols(conn, table):
    return {c['name'] for c in inspect(conn).get_columns(table)} if table in _table_names(conn) else set()
def main():
    url=os.environ.get('DATABASE_URL','sqlite:///./app.db')
    safe=url.split('@')[-1] if '@' in url else url.split('://')[0]+'://…'
    engine=create_engine(url)
    with engine.connect() as c:
        names=_table_names(c)
        print(f'database={safe}')
        if 'wallets' not in names:
            print('wallet_count=0')
            print('legacy_wallets=0')
            print('warning=no wallets table found; dry run is read-only and schema may be pre-migration')
            return 0
        wcols=_cols(c,'wallets')
        has_version='currency_version' in wcols
        predicate='WHERE currency_version < 2' if has_version else ''
        if not has_version:
            print('schema_note=wallets.currency_version missing; treating all wallets as legacy')
        rows=c.execute(text(f'SELECT balance_coins,total_added_coins,total_spent_coins FROM wallets {predicate}')).mappings().all()
        all_count=c.execute(text('SELECT COUNT(*) FROM wallets')).scalar() or 0
        sums={
          'balance': sum(r.balance_coins or 0 for r in rows),
          'added': sum(r.total_added_coins or 0 for r in rows),
          'spent': sum(r.total_spent_coins or 0 for r in rows),
        }
        converted={k: sum(ceil_coin(getattr(r, {'balance':'balance_coins','added':'total_added_coins','spent':'total_spent_coins'}[k])) for r in rows) for k in sums}
        neg={
          'balance': sum(1 for r in rows if (r.balance_coins or 0) < 0),
          'added': sum(1 for r in rows if (r.total_added_coins or 0) < 0),
          'spent': sum(1 for r in rows if (r.total_spent_coins or 0) < 0),
        }
        print(f'wallet_count={all_count}')
        print(f'legacy_wallets={len(rows)}')
        print(f'legacy_balance_toman_total={sums["balance"]}')
        print(f'legacy_total_added_toman_total={sums["added"]}')
        print(f'legacy_total_spent_toman_total={sums["spent"]}')
        print(f'converted_balance_coins_total={converted["balance"]}')
        print(f'converted_total_added_coins_total={converted["added"]}')
        print(f'converted_total_spent_coins_total={converted["spent"]}')
        print(f'negative_balance_count={neg["balance"]}')
        print(f'negative_total_added_count={neg["added"]}')
        print(f'negative_total_spent_count={neg["spent"]}')
        affected=0
        if 'subscriptions' in names:
            scols=_cols(c,'subscriptions')
            plan_col='plan' if 'plan' in scols else 'plan_code' if 'plan_code' in scols else None
            expires_col='expires_at' if 'expires_at' in scols else 'current_period_end' if 'current_period_end' in scols else None
            if plan_col and 'status' in scols:
                expiry=f"COALESCE(CAST({expires_col} AS TEXT), 'lifetime')" if expires_col else "'unknown'"
                q=text(f"SELECT COALESCE({plan_col},'unknown') plan, {expiry} expiry, COUNT(*) count FROM subscriptions WHERE status IN ('active','trialing') AND COALESCE({plan_col},'free') <> 'free' GROUP BY COALESCE({plan_col},'unknown'), {expiry} ORDER BY 1,2")
                subs=c.execute(q).mappings().all()
                print('active_paid_subscriptions_by_plan_and_expiry:')
                for s in subs:
                    affected += s.count
                    print(f'  plan={s.plan} expiry={s.expiry} count={s.count}')
        if affected:
            print(f'WARNING active_paid_subscriptions_would_be_affected={affected}; migration must preserve paid value until expiry or credit prorated coins')
        else:
            print('active_paid_subscriptions_would_be_affected=0')
        print('conversion=ceil(legacy_toman / 100); example 590000 -> %s coins; 590001 -> %s coins' % (ceil_coin(590000), ceil_coin(590001)))
    return 0
if __name__=='__main__': raise SystemExit(main())
