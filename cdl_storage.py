"""SQLite para desarrollo; Postgres compartido para Vercel, sin archivos temporales."""
import os
from pathlib import Path
import sqlite3
import threading

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('CAFE_DATA_DIR', ROOT / 'data'))
DATABASE_URL = os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL', '')
IS_VERCEL = os.environ.get('VERCEL') == '1'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,label TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'open',created_at BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS orders(token TEXT PRIMARY KEY,code TEXT UNIQUE NOT NULL,customer TEXT NOT NULL,notes TEXT NOT NULL,items TEXT NOT NULL,total INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',account_id TEXT REFERENCES accounts(id),created_at BIGINT NOT NULL,updated_at BIGINT NOT NULL,idempotency_key TEXT UNIQUE NOT NULL,fingerprint TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY,expires BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS limits(bucket TEXT PRIMARY KEY,count INTEGER NOT NULL,expires BIGINT NOT NULL);
CREATE INDEX IF NOT EXISTS orders_account ON orders(account_id);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status,created_at);
'''
_ready = False
_schema_lock = threading.Lock()


class StorageUnavailable(Exception):
    pass


class PostgresConnection:
    """Adapta los parámetros SQL y los límites de transacción usados por la API."""
    def __init__(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
            self.driver = psycopg
            self.connection = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10, prepare_threshold=None)
        except Exception as error:
            raise StorageUnavailable('No pudimos conectar la base de datos de barra.') from error

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()

    def execute(self, sql, params=()):
        try:
            if sql.strip().upper() == 'BEGIN IMMEDIATE':
                # Un bloqueo de transacción compartido coordina las escrituras de
                # todas las funciones/celulares; se libera en commit o rollback.
                return self.connection.execute('SELECT pg_advisory_xact_lock(719432607)')
            if sql.strip().upper() == 'BEGIN':
                return self.connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            return self.connection.execute(sql.replace('?', '%s'), params)
        except self.driver.OperationalError as error:
            raise StorageUnavailable('La base de datos de barra está ocupada. Inténtalo otra vez.') from error


def connect():
    if DATABASE_URL:
        return PostgresConnection()
    if IS_VERCEL:
        raise StorageUnavailable('Falta conectar Postgres al proyecto de Vercel.')
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(DATA / 'orders.sqlite3', timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def initialize():
    global _ready
    if _ready:
        return
    with _schema_lock:
        if _ready:
            return
        with connect() as db:
            if DATABASE_URL:
                db.execute('BEGIN IMMEDIATE')
                for statement in SCHEMA.split(';'):
                    if statement.strip():
                        db.execute(statement)
            else:
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript(SCHEMA)
        _ready = True
