import os
import sqlite3
import subprocess
import sys


def _run_dry(db_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
    return subprocess.run([sys.executable, "scripts/dry_run_coin_currency_migration.py"], env=env, text=True, capture_output=True, check=False)


def test_dry_run_pre_0030_schema_treats_all_wallets_as_legacy(tmp_path):
    db_path = tmp_path / "pre0030.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE wallets (id INTEGER PRIMARY KEY, balance_coins INTEGER, total_added_coins INTEGER, total_spent_coins INTEGER)")
    con.execute("INSERT INTO wallets VALUES (1, 590001, 100, -5)")
    con.commit(); con.close()

    res = _run_dry(db_path)

    assert res.returncode == 0
    assert "schema_note=wallets.currency_version missing; treating all wallets as legacy" in res.stdout
    assert "wallet_count=1" in res.stdout
    assert "legacy_wallets=1" in res.stdout
    assert "converted_balance_coins_total=5901" in res.stdout
    assert "negative_total_spent_count=1" in res.stdout


def test_dry_run_post_0030_schema_filters_currency_version(tmp_path):
    db_path = tmp_path / "post0030.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE wallets (id INTEGER PRIMARY KEY, balance_coins INTEGER, total_added_coins INTEGER, total_spent_coins INTEGER, currency_version INTEGER NOT NULL)")
    con.execute("INSERT INTO wallets VALUES (1, 101, 101, 0, 1)")
    con.execute("INSERT INTO wallets VALUES (2, 999999, 999999, 0, 2)")
    con.commit(); con.close()

    res = _run_dry(db_path)

    assert res.returncode == 0
    assert "wallet_count=2" in res.stdout
    assert "legacy_wallets=1" in res.stdout
    assert "converted_balance_coins_total=2" in res.stdout


def test_dry_run_reports_paid_subscription_warning(tmp_path):
    db_path = tmp_path / "subs.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE wallets (id INTEGER PRIMARY KEY, balance_coins INTEGER, total_added_coins INTEGER, total_spent_coins INTEGER)")
    con.execute("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, plan TEXT, status TEXT, expires_at TEXT)")
    con.execute("INSERT INTO subscriptions VALUES (1, 'plus', 'active', '2026-08-01')")
    con.commit(); con.close()

    res = _run_dry(db_path)

    assert res.returncode == 0
    assert "active_paid_subscriptions_by_plan_and_expiry:" in res.stdout
    assert "plan=plus expiry=2026-08-01 count=1" in res.stdout
    assert "WARNING active_paid_subscriptions_would_be_affected=1" in res.stdout
