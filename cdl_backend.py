"""API de pedidos de Café de Ley: SQLite local o Postgres en Vercel."""
import argparse
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import unquote
from wsgiref.simple_server import make_server
from cdl_storage import DATA, DATABASE_URL, IS_VERCEL, StorageUnavailable, connect, initialize

ROOT = Path(__file__).resolve().parent
CATALOG = json.loads((ROOT / 'cdl_catalog.json').read_text())
ORIGINS = {x.strip().rstrip('/') for x in os.environ.get('CAFE_ALLOWED_ORIGINS', 'https://menu-interactivo-cdl.vercel.app,http://127.0.0.1:8765,http://localhost:8765').split(',') if x.strip()}
STATES = {'received': {'preparing', 'cancelled'}, 'preparing': {'ready', 'cancelled'}, 'ready': {'delivered', 'cancelled'}, 'delivered': set(), 'cancelled': set()}
DRINKS = {'cappuccino': 'Capuchino', 'latte': 'Latte', 'flat-white': 'Flat white'}
MILKS = {'regular': 'Regular', 'lactose-free': 'Deslactosada', 'oat': 'De avena'}
METHODS = {'filter': 'Filtrado', 'espresso': 'Espresso', 'milk': 'Espresso Milk'}

class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + digest


def configured_hash():
    value = os.environ.get('CAFE_TEAM_PASSWORD_HASH', '')
    if not value and not IS_VERCEL and (DATA / 'team-key.json').is_file():
        value = json.loads((DATA / 'team-key.json').read_text()).get('hash', '')
    return value


def session_digest(token):
    # Cambiar la clave del equipo invalida también las sesiones anteriores.
    return hmac.new(configured_hash().encode(), token.encode(), hashlib.sha256).hexdigest()


def text(value, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise APIError(400, 'Revisa los datos del formulario.')
    return value.strip()


def canonical_items(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 30:
        raise APIError(400, 'Agrega entre 1 y 30 bebidas diferentes.')
    result, count = [], 0
    for item in items:
        if not isinstance(item, dict):
            raise APIError(400, 'Bebida no válida.')
        entry = CATALOG['items'].get(item.get('id')) if isinstance(item.get('id'), str) else None
        quantity = item.get('quantity')
        if not entry or type(quantity) is not int or not 1 <= quantity <= 20:
            raise APIError(400, 'Revisa la bebida y su cantidad.')
        price, details = entry['price'], []
        clean = {'id': item['id'], 'name': entry['name'], 'quantity': quantity, 'category': entry['category']}
        if entry['category'] == 'hot':
            method = item.get('method')
            if not isinstance(method, str) or method not in METHODS:
                raise APIError(400, 'Elige el método de preparación.')
            clean['method'] = method
            price += CATALOG['methods'][method]
            details.append(METHODS[method])
            if method == 'milk':
                drink, milk = item.get('drink'), item.get('milk')
                if not isinstance(drink, str) or not isinstance(milk, str) or drink not in DRINKS or milk not in MILKS:
                    raise APIError(400, 'Elige la bebida y el tipo de leche.')
                clean.update(drink=drink, milk=milk)
                details += [DRINKS[drink], 'Leche ' + MILKS[milk]]
        elif entry['category'] == 'thermal':
            details.append('Experiencia térmica')
        clean.update(unit_price=price, details=' · '.join(details))
        result.append(clean)
        count += quantity
    if count > 50:
        raise APIError(400, 'El máximo es de 50 bebidas por pedido.')
    return result


def order_dict(row):
    value = dict(row)
    value['items'] = json.loads(value['items'])
    value.pop('idempotency_key', None)
    value.pop('fingerprint', None)
    return value


def load_order(db, identifier, allow_code=False):
    query = 'SELECT * FROM orders WHERE token=?' + (' OR code=?' if allow_code else '')
    row = db.execute(query, (identifier, identifier.upper()) if allow_code else (identifier,)).fetchone()
    if not row:
        raise APIError(404, 'No encontramos ese pedido. Revisa el QR o el código.')
    return row


def limited(bucket, maximum, seconds):
    now = int(time.time())
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM limits WHERE expires<?', (now,))
        row = db.execute('SELECT * FROM limits WHERE bucket=?', (bucket,)).fetchone()
        if row and row['count'] >= maximum:
            raise APIError(429, 'Demasiados intentos. Espera unos minutos e inténtalo otra vez.')
        db.execute('INSERT INTO limits VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=limits.count+1', (bucket, now+seconds))


def authorize(environ, db):
    header = environ.get('HTTP_AUTHORIZATION', '')
    token = header.removeprefix('Bearer ')
    if not header.startswith('Bearer ') or len(token) > 128:
        raise APIError(401, 'Entra a Team Work para continuar.')
    digest = session_digest(token)
    session = db.execute('SELECT * FROM sessions WHERE digest=? AND expires>?', (digest, int(time.time()))).fetchone()
    if not session:
        raise APIError(401, 'Tu sesión terminó. Vuelve a entrar a Team Work.')
    return digest


def route(method, path, body, env):
    initialize()
    now = int(time.time())
    # Configurar el proxy del alojamiento para conservar REMOTE_ADDR real.
    client = env.get('REMOTE_ADDR', 'unknown')
    if path == '/api/health' and method == 'GET':
        return {'ok': True, 'team_configured': bool(configured_hash()), 'database': 'postgres' if DATABASE_URL else 'sqlite'}
    if path == '/api/orders' and method == 'POST':
        limited('orders:' + client, 120, 60)
        customer, notes = text(body.get('customer', ''), 60, True), text(body.get('notes', ''), 400)
        items = canonical_items(body.get('items'))
        key = text(body.get('idempotency_key', ''), 80, True)
        if not re.fullmatch(r'[A-Za-z0-9-]{16,80}', key):
            raise APIError(400, 'Vuelve a intentar generar el pedido.')
        fingerprint = hashlib.sha256(json.dumps([customer, notes, items], sort_keys=True).encode()).hexdigest()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM orders WHERE idempotency_key=?', (key,)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise APIError(409, 'Este envío ya fue utilizado con otro pedido.')
                return {'order': order_dict(previous)}
            token = secrets.token_urlsafe(24)
            code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
            total = sum(item['unit_price'] * item['quantity'] for item in items)
            db.execute('INSERT INTO orders(token,code,customer,notes,items,total,created_at,updated_at,idempotency_key,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?)', (token,code,customer,notes,json.dumps(items,ensure_ascii=False),total,now,now,key,fingerprint))
            return {'order': order_dict(load_order(db, token))}
    if re.fullmatch(r'/api/orders/[A-Za-z0-9_-]{32}', path) and method == 'GET':
        limited('read:' + client, 240, 60)
        with connect() as db:
            return {'order': order_dict(load_order(db, path.rsplit('/',1)[1]))}
    if path == '/api/team/login' and method == 'POST':
        limited('login:' + client, 12, 600)
        stored = configured_hash()
        if not stored:
            raise APIError(503, 'Team Work todavía no tiene una clave configurada.')
        password = text(body.get('password', ''), 256, True)
        try:
            valid = hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise APIError(401, 'Clave incorrecta.')
        token = secrets.token_urlsafe(32)
        with connect() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            db.execute('INSERT INTO sessions VALUES(?,?)', (session_digest(token), now+12*3600))
            db.execute('DELETE FROM limits WHERE bucket=?', ('login:' + client,))
        return {'session': token, 'expires': now+12*3600}
    if not path.startswith('/api/team/'):
        raise APIError(404, 'No encontramos esta dirección.')
    with connect() as db:
        # Una transacción por operación coordina los celulares que usan la barra.
        if method != 'GET':
            db.execute('BEGIN IMMEDIATE')
        else:
            db.execute('BEGIN')
        session = authorize(env, db)
        if path == '/api/team/logout' and method == 'POST':
            db.execute('DELETE FROM sessions WHERE digest=?', (session,))
            return {'ok': True}
        if path == '/api/team/board' and method == 'GET':
            accounts = [dict(r) for r in db.execute("SELECT * FROM accounts WHERE state='open' ORDER BY created_at DESC")]
            orders = [order_dict(r) for r in db.execute("SELECT * FROM orders WHERE account_id IN (SELECT id FROM accounts WHERE state='open') ORDER BY created_at")]
            return {'accounts': accounts, 'orders': orders, 'server_time': now}
        if path == '/api/team/lookup' and method == 'POST':
            identifier = text(body.get('code', ''), 80, True)
            return {'order': order_dict(load_order(db, identifier, True))}
        if path == '/api/team/receive' and method == 'POST':
            order = load_order(db, text(body.get('token', ''), 80, True))
            if order['status'] != 'pending':
                return {'order': order_dict(order), 'already_received': True}
            if now - order['created_at'] > 86400:
                raise APIError(410, 'Este QR venció. Pide a la persona generar un nuevo pedido.')
            account_id = body.get('account_id')
            if account_id:
                account = db.execute("SELECT * FROM accounts WHERE id=? AND state='open'", (text(account_id,80,True),)).fetchone()
                if not account:
                    raise APIError(409, 'Esa cuenta ya no está abierta. Elige otra.')
            else:
                label = text(body.get('account_label', ''), 60, True)
                account_id = secrets.token_urlsafe(12)
                db.execute('INSERT INTO accounts(id,label,created_at) VALUES(?,?,?)', (account_id,label,now))
            db.execute("UPDATE orders SET status='received',account_id=?,updated_at=? WHERE token=?", (account_id,now,order['token']))
            return {'order': order_dict(load_order(db, order['token'])), 'already_received': False}
        match = re.fullmatch(r'/api/team/orders/([A-Za-z0-9_-]{32})/(status|account)', path)
        if match and method == 'POST':
            token, action = match.groups()
            order = load_order(db, token)
            account = db.execute('SELECT * FROM accounts WHERE id=?', (order['account_id'],)).fetchone()
            if not account or account['state'] != 'open':
                raise APIError(409, 'La cuenta está cerrada o el pedido aún no se ha recibido.')
            if action == 'status':
                status = text(body.get('status', ''), 20, True)
                if status == order['status']:
                    return {'order': order_dict(order)}
                if status not in STATES.get(order['status'], set()):
                    raise APIError(409, 'El pedido cambió en otro celular. Actualiza la cuenta.')
                expected = body.get('expected_status')
                if expected != order['status']:
                    raise APIError(409, 'El pedido cambió en otro celular. Actualiza la cuenta.')
                db.execute('UPDATE orders SET status=?,updated_at=? WHERE token=?', (status,now,token))
            else:
                target = text(body.get('account_id',''), 80, True)
                if not db.execute("SELECT id FROM accounts WHERE id=? AND state='open'", (target,)).fetchone():
                    raise APIError(409, 'La cuenta de destino ya no está abierta.')
                db.execute('UPDATE orders SET account_id=?,updated_at=? WHERE token=?', (target,now,token))
            return {'order': order_dict(load_order(db, token))}
        match = re.fullmatch(r'/api/team/accounts/([A-Za-z0-9_-]{16})/(rename|close)', path)
        if match and method == 'POST':
            identifier, action = match.groups()
            if not db.execute("SELECT id FROM accounts WHERE id=? AND state='open'", (identifier,)).fetchone():
                raise APIError(409, 'La cuenta ya está cerrada.')
            if action == 'rename':
                db.execute('UPDATE accounts SET label=? WHERE id=?', (text(body.get('label',''),60,True),identifier))
            else:
                active = db.execute("SELECT 1 FROM orders WHERE account_id=? AND status NOT IN ('delivered','cancelled')", (identifier,)).fetchone()
                if active:
                    raise APIError(409, 'Entrega o cancela los pedidos pendientes antes de cerrar la cuenta.')
                db.execute("UPDATE accounts SET state='closed' WHERE id=?", (identifier,))
            return {'ok': True}
    raise APIError(404, 'No encontramos esta acción.')


def application(env, start_response):
    origin = env.get('HTTP_ORIGIN', '').rstrip('/')
    headers = [('Content-Type','application/json; charset=utf-8'),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Vary','Origin')]
    if origin in ORIGINS:
        headers += [('Access-Control-Allow-Origin',origin),('Access-Control-Allow-Headers','Authorization, Content-Type'),('Access-Control-Allow-Methods','GET, POST, OPTIONS')]
    status = 200
    try:
        if origin and origin not in ORIGINS:
            raise APIError(403, 'Este menú todavía no está autorizado para conectarse a la barra.')
        method = env.get('REQUEST_METHOD','GET')
        if method == 'OPTIONS':
            result = {'ok': True}
        else:
            length = int(env.get('CONTENT_LENGTH') or '0')
            if length > 20000 or length < 0:
                raise APIError(413, 'El pedido es demasiado grande.')
            if method == 'POST' and 'application/json' not in env.get('CONTENT_TYPE',''):
                raise APIError(415, 'El formulario debe enviarse en JSON.')
            body = json.loads(env['wsgi.input'].read(length)) if length else {}
            if not isinstance(body, dict):
                raise APIError(400, 'Formulario no válido.')
            result = route(method, unquote(env.get('PATH_INFO','')), body, env)
    except APIError as error:
        status, result = error.status, {'error': error.message}
    except (json.JSONDecodeError, ValueError, TypeError):
        status, result = 400, {'error': 'No pudimos leer los datos enviados.'}
    except StorageUnavailable as error:
        status, result = 503, {'error': str(error)}
    except sqlite3.OperationalError:
        status, result = 503, {'error': 'La barra está ocupada. Inténtalo otra vez.'}
    except Exception:
        import logging
        logging.exception('Error interno en la API')
        status, result = 500, {'error': 'No pudimos completar la acción. Inténtalo otra vez.'}
    from http import HTTPStatus
    data = json.dumps(result,ensure_ascii=False).encode()
    start_response(f'{status} {HTTPStatus(status).phrase}', headers+[('Content-Length',str(len(data)))])
    return [data]


if not IS_VERCEL and not DATABASE_URL:
    initialize()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--setup', action='store_true', help='Configura la clave fuera del código público.')
    parser.add_argument('--hash', action='store_true', help='Imprime un hash para configurarlo como secreto en el alojamiento.')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if args.setup or args.hash:
        password = getpass.getpass('Nueva clave de Team Work (mínimo 8 caracteres): ')
        if len(password.strip()) < 8 or password != getpass.getpass('Repite la clave: '):
            parser.error('La clave debe coincidir y tener al menos 8 caracteres.')
        value = password_hash(password.strip())
        if args.hash:
            print(value)
        else:
            (DATA / 'team-key.json').write_text(json.dumps({'hash': value}))
            (DATA / 'team-key.json').chmod(0o600)
            with connect() as db:
                db.execute('DELETE FROM sessions')
            print('Clave guardada. Las sesiones anteriores se han cerrado.')
    else:
        print(f'API local: http://127.0.0.1:{args.port} (para producción usa Gunicorn y HTTPS).')
        make_server('127.0.0.1',args.port,application).serve_forever()
