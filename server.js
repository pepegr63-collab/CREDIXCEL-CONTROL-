import express from 'express';
import cors from 'cors';
import pg from 'pg';
import jwt from 'jsonwebtoken';
import crypto from 'crypto';
import path from 'path';
import { fileURLToPath } from 'url';

const { Pool } = pg;
const app = express();
const __dirname = path.dirname(fileURLToPath(import.meta.url));

app.use(cors());
app.use(express.json());

if (!process.env.DATABASE_URL || !process.env.ADMIN_SECRET || !process.env.ADMIN_PASSWORD) {
  throw new Error('Faltan variables de entorno obligatorias.');
}

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});

const now = () => new Date().toISOString();
const id = () => crypto.randomUUID();

async function initDb() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS clients (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT,
      email TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS contracts (
      id TEXT PRIMARY KEY, client_id TEXT NOT NULL, device_id TEXT,
      total_amount DOUBLE PRECISION DEFAULT 0,
      monthly_amount DOUBLE PRECISION DEFAULT 0,
      due_day INTEGER DEFAULT 1, status TEXT DEFAULT 'ACTIVE',
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS payments (
      id TEXT PRIMARY KEY, contract_id TEXT NOT NULL,
      amount DOUBLE PRECISION NOT NULL, due_date TEXT NOT NULL,
      paid_at TEXT, status TEXT DEFAULT 'PENDING'
    );
    CREATE TABLE IF NOT EXISTS devices (
      id TEXT PRIMARY KEY, enrollment_code TEXT UNIQUE NOT NULL,
      device_token TEXT UNIQUE, device_name TEXT, android_id TEXT,
      status TEXT DEFAULT 'ACTIVE', message TEXT,
      uninstall_blocked INTEGER DEFAULT 0, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS audit (
      id TEXT PRIMARY KEY, action TEXT NOT NULL, entity TEXT,
      entity_id TEXT, details TEXT, created_at TEXT NOT NULL
    );
  `);
}

const audit = (action, entity, entityId, details = '') =>
  pool.query(
    'INSERT INTO audit(id,action,entity,entity_id,details,created_at) VALUES($1,$2,$3,$4,$5,$6)',
    [id(), action, entity, entityId, details, now()]
  );

function admin(req, res, next) {
  const h = req.headers.authorization || '';
  try {
    jwt.verify(h.replace('Bearer ', ''), process.env.ADMIN_SECRET);
    next();
  } catch {
    res.status(401).json({ error: 'Unauthorized' });
  }
}

app.use(express.static(path.join(__dirname, 'public')));
app.get('/health', (req, res) => res.json({ ok: true, service: 'CREDIXCEL Control' }));

app.post('/api/admin/login', (req, res) => {
  if (req.body.username !== 'admin' || req.body.password !== process.env.ADMIN_PASSWORD) {
    return res.status(401).json({ error: 'Invalid credentials' });
  }
  res.json({ token: jwt.sign({ role: 'admin' }, process.env.ADMIN_SECRET, { expiresIn: '8h' }) });
});

app.post('/api/device/enroll', async (req, res, next) => {
  try {
    const { enrollmentCode, deviceName, androidId } = req.body || {};
    const result = await pool.query('SELECT * FROM devices WHERE enrollment_code=$1', [enrollmentCode]);
    const row = result.rows[0];
    if (!row) return res.status(404).json({ error: 'Invalid enrollment code' });
    const token = crypto.randomBytes(32).toString('hex');
    await pool.query(
      'UPDATE devices SET device_token=$1,device_name=$2,android_id=$3,updated_at=$4 WHERE id=$5',
      [token, deviceName || null, androidId || null, now(), row.id]
    );
    await audit('DEVICE_ENROLLED', 'device', row.id, deviceName || '');
    res.json({ deviceId: row.id, deviceToken: token, status: row.status, message: row.message || '', uninstallBlocked: Boolean(row.uninstall_blocked) });
  } catch (e) { next(e); }
});

app.post('/api/device/sync', async (req, res, next) => {
  try {
    const { deviceId, deviceToken } = req.body || {};
    const result = await pool.query(
      `SELECT d.*, c.status AS contract_status
       FROM devices d LEFT JOIN contracts c ON c.device_id=d.id
       WHERE d.id=$1 AND d.device_token=$2`,
      [deviceId, deviceToken]
    );
    const row = result.rows[0];
    if (!row) return res.status(401).json({ error: 'Unauthorized device' });
    await pool.query('UPDATE devices SET updated_at=$1 WHERE id=$2', [now(), deviceId]);
    res.json({ deviceId: row.id, status: row.status, message: row.message || '', uninstallBlocked: Boolean(row.uninstall_blocked), contractStatus: row.contract_status || 'ACTIVE', updatedAt: row.updated_at });
  } catch (e) { next(e); }
});

app.get('/api/admin/dashboard', admin, async (req, res, next) => {
  try {
    const q = await pool.query(`
      SELECT
        (SELECT COUNT(*) FROM clients) AS clients,
        (SELECT COUNT(*) FROM devices) AS devices,
        (SELECT COUNT(*) FROM devices WHERE status='ACTIVE') AS active,
        (SELECT COUNT(*) FROM devices WHERE status='PAYMENT_DUE') AS due,
        (SELECT COUNT(*) FROM devices WHERE status='LOCKED') AS locked,
        (SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='PENDING') AS pending
    `);
    const r = q.rows[0];
    res.json(Object.fromEntries(Object.entries(r).map(([k,v]) => [k, Number(v)])));
  } catch (e) { next(e); }
});

app.get('/api/admin/devices', admin, async (req, res, next) => {
  try {
    const r = await pool.query(`
      SELECT d.*, c.name AS client_name, c.id AS client_id,
      ct.id AS contract_id, ct.status AS contract_status
      FROM devices d LEFT JOIN contracts ct ON ct.device_id=d.id
      LEFT JOIN clients c ON c.id=ct.client_id ORDER BY d.updated_at DESC
    `);
    res.json(r.rows);
  } catch (e) { next(e); }
});

app.post('/api/admin/devices', admin, async (req, res, next) => {
  try {
    const deviceId = id(), code = crypto.randomBytes(5).toString('hex').toUpperCase();
    await pool.query('INSERT INTO devices(id,enrollment_code,updated_at) VALUES($1,$2,$3)', [deviceId, code, now()]);
    await audit('DEVICE_CREATED', 'device', deviceId, code);
    res.json({ id: deviceId, enrollmentCode: code });
  } catch (e) { next(e); }
});

app.post('/api/admin/devices/:id/state', admin, async (req, res, next) => {
  try {
    const { status, message = '' } = req.body;
    if (!['ACTIVE', 'LOCKED', 'PAYMENT_DUE'].includes(status)) return res.status(400).json({ error: 'Invalid state' });
    const r = await pool.query('UPDATE devices SET status=$1,message=$2,updated_at=$3 WHERE id=$4', [status, message, now(), req.params.id]);
    await audit('DEVICE_STATE_CHANGED', 'device', req.params.id, `${status}: ${message}`);
    res.json({ updated: r.rowCount });
  } catch (e) { next(e); }
});

app.post('/api/admin/devices/:id/uninstall-policy', admin, async (req, res, next) => {
  try {
    const blocked = Boolean(req.body.blocked);
    const r = await pool.query('UPDATE devices SET uninstall_blocked=$1,updated_at=$2 WHERE id=$3', [blocked ? 1 : 0, now(), req.params.id]);
    await audit(blocked ? 'UNINSTALL_BLOCKED' : 'UNINSTALL_ALLOWED', 'device', req.params.id);
    res.json({ updated: r.rowCount, blocked });
  } catch (e) { next(e); }
});

app.get('/api/admin/clients', admin, async (req, res, next) => {
  try { res.json((await pool.query('SELECT * FROM clients ORDER BY created_at DESC')).rows); }
  catch (e) { next(e); }
});

app.post('/api/admin/clients', admin, async (req, res, next) => {
  try {
    const { name, phone = '', email = '' } = req.body;
    if (!name) return res.status(400).json({ error: 'Name required' });
    const clientId = id();
    await pool.query('INSERT INTO clients(id,name,phone,email,created_at) VALUES($1,$2,$3,$4,$5)', [clientId, name, phone, email, now()]);
    await audit('CLIENT_CREATED', 'client', clientId, name);
    res.json({ id: clientId });
  } catch (e) { next(e); }
});

app.get('/api/admin/contracts', admin, async (req, res, next) => {
  try {
    res.json((await pool.query(`
      SELECT ct.*, c.name AS client_name, d.device_name
      FROM contracts ct JOIN clients c ON c.id=ct.client_id
      LEFT JOIN devices d ON d.id=ct.device_id ORDER BY ct.created_at DESC
    `)).rows);
  } catch (e) { next(e); }
});

app.post('/api/admin/contracts', admin, async (req, res, next) => {
  try {
    const { clientId, deviceId = null, totalAmount = 0, monthlyAmount = 0, dueDay = 1 } = req.body;
    if (!clientId) return res.status(400).json({ error: 'clientId required' });
    const contractId = id();
    await pool.query(
      'INSERT INTO contracts(id,client_id,device_id,total_amount,monthly_amount,due_day,status,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)',
      [contractId, clientId, deviceId, totalAmount, monthlyAmount, dueDay, 'ACTIVE', now()]
    );
    await audit('CONTRACT_CREATED', 'contract', contractId);
    res.json({ id: contractId });
  } catch (e) { next(e); }
});

app.get('/api/admin/payments', admin, async (req, res, next) => {
  try {
    res.json((await pool.query(`
      SELECT p.*, ct.client_id, c.name AS client_name FROM payments p
      JOIN contracts ct ON ct.id=p.contract_id JOIN clients c ON c.id=ct.client_id
      ORDER BY p.due_date DESC
    `)).rows);
  } catch (e) { next(e); }
});

app.post('/api/admin/payments', admin, async (req, res, next) => {
  try {
    const { contractId, amount, dueDate } = req.body;
    if (!contractId || !amount || !dueDate) return res.status(400).json({ error: 'contractId, amount and dueDate required' });
    const paymentId = id();
    await pool.query('INSERT INTO payments(id,contract_id,amount,due_date,status) VALUES($1,$2,$3,$4,$5)', [paymentId, contractId, amount, dueDate, 'PENDING']);
    await audit('PAYMENT_CREATED', 'payment', paymentId);
    res.json({ id: paymentId });
  } catch (e) { next(e); }
});

app.post('/api/admin/payments/:id/pay', admin, async (req, res, next) => {
  try {
    const r = await pool.query("UPDATE payments SET status='PAID',paid_at=$1 WHERE id=$2", [now(), req.params.id]);
    await audit('PAYMENT_PAID', 'payment', req.params.id);
    res.json({ updated: r.rowCount });
  } catch (e) { next(e); }
});

app.get('/api/admin/audit', admin, async (req, res, next) => {
  try { res.json((await pool.query('SELECT * FROM audit ORDER BY created_at DESC LIMIT 200')).rows); }
  catch (e) { next(e); }
});

app.use((err, req, res, next) => {
  console.error('Request failed:', err.message);
  res.status(500).json({ error: 'Internal server error' });
});

const port = process.env.PORT || 3000;
initDb()
  .then(() => app.listen(port, () => console.log(`CREDIXCEL Control API running on :${port}`)))
  .catch(err => {
    console.error('Database initialization failed:', err.message);
    process.exit(1);
  });

