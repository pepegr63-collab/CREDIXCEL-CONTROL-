import express from 'express';
import cors from 'cors';
import Database from 'better-sqlite3';
import jwt from 'jsonwebtoken';
import crypto from 'crypto';
import path from 'path';
import { fileURLToPath } from 'url';

const app = express();
app.use(cors());
app.use(express.json());
const db = new Database(process.env.DB_PATH || 'credixcel.db');
const now = () => new Date().toISOString();
const __dirname = path.dirname(fileURLToPath(import.meta.url));

db.exec(`
CREATE TABLE IF NOT EXISTS clients(id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT, email TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contracts(id TEXT PRIMARY KEY, client_id TEXT NOT NULL, device_id TEXT, total_amount REAL DEFAULT 0, monthly_amount REAL DEFAULT 0, due_day INTEGER DEFAULT 1, status TEXT DEFAULT 'ACTIVE', created_at TEXT NOT NULL, FOREIGN KEY(client_id) REFERENCES clients(id));
CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, amount REAL NOT NULL, due_date TEXT NOT NULL, paid_at TEXT, status TEXT DEFAULT 'PENDING', FOREIGN KEY(contract_id) REFERENCES contracts(id));
CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, enrollment_code TEXT UNIQUE NOT NULL, device_token TEXT UNIQUE, device_name TEXT, android_id TEXT, status TEXT DEFAULT 'ACTIVE', message TEXT, uninstall_blocked INTEGER DEFAULT 0, updated_at TEXT);
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, action TEXT NOT NULL, entity TEXT, entity_id TEXT, details TEXT, created_at TEXT NOT NULL);
`);

const ADMIN_SECRET = process.env.ADMIN_SECRET || 'CHANGE_ME_IN_PRODUCTION';
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || 'CHANGE_ME_IN_PRODUCTION';
const audit = (action, entity, entityId, details='') => db.prepare('INSERT INTO audit(id,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)').run(crypto.randomUUID(), action, entity, entityId, details, now());
function admin(req,res,next){ const h=req.headers.authorization||''; try { jwt.verify(h.replace('Bearer ',''), ADMIN_SECRET); next(); } catch { res.status(401).json({error:'Unauthorized'}); } }

app.use(express.static(path.join(__dirname,'../public')));
app.get('/health',(req,res)=>res.json({ok:true,service:'CREDIXCEL Control'}));
app.post('/api/admin/login',(req,res)=>{ if(req.body.username!=='admin'||req.body.password!==ADMIN_PASSWORD) return res.status(401).json({error:'Invalid credentials'}); res.json({token:jwt.sign({role:'admin'},ADMIN_SECRET,{expiresIn:'8h'})}); });

app.post('/api/device/enroll',(req,res)=>{
 const {enrollmentCode,deviceName,androidId}=req.body||{};
 const row=db.prepare('SELECT * FROM devices WHERE enrollment_code=?').get(enrollmentCode);
 if(!row) return res.status(404).json({error:'Invalid enrollment code'});
 const token=crypto.randomBytes(32).toString('hex');
 db.prepare('UPDATE devices SET device_token=?,device_name=?,android_id=?,updated_at=? WHERE id=?').run(token,deviceName,androidId,now(),row.id);
 audit('DEVICE_ENROLLED','device',row.id,deviceName||'');
 res.json({deviceId:row.id,deviceToken:token,status:row.status,message:row.message||'',uninstallBlocked:Boolean(row.uninstall_blocked)});
});

app.post('/api/device/sync',(req,res)=>{
 const {deviceId,deviceToken}=req.body||{};
 const row=db.prepare('SELECT d.*, c.status contract_status FROM devices d LEFT JOIN contracts c ON c.device_id=d.id WHERE d.id=? AND d.device_token=?').get(deviceId,deviceToken);
 if(!row) return res.status(401).json({error:'Unauthorized device'});
 db.prepare('UPDATE devices SET updated_at=? WHERE id=?').run(now(),deviceId);
 res.json({deviceId:row.id,status:row.status,message:row.message||'',uninstallBlocked:Boolean(row.uninstall_blocked),contractStatus:row.contract_status||'ACTIVE',updatedAt:row.updated_at});
});

app.get('/api/admin/dashboard',admin,(req,res)=>{
 const clients=db.prepare('SELECT COUNT(*) c FROM clients').get().c;
 const devices=db.prepare('SELECT COUNT(*) c FROM devices').get().c;
 const active=db.prepare("SELECT COUNT(*) c FROM devices WHERE status='ACTIVE'").get().c;
 const due=db.prepare("SELECT COUNT(*) c FROM devices WHERE status='PAYMENT_DUE'").get().c;
 const locked=db.prepare("SELECT COUNT(*) c FROM devices WHERE status='LOCKED'").get().c;
 const pending=db.prepare("SELECT COALESCE(SUM(amount),0) total FROM payments WHERE status='PENDING'").get().total;
 res.json({clients,devices,active,due,locked,pending});
});
app.get('/api/admin/devices',admin,(req,res)=>res.json(db.prepare(`SELECT d.*, c.name client_name, c.id client_id, ct.id contract_id, ct.status contract_status FROM devices d LEFT JOIN contracts ct ON ct.device_id=d.id LEFT JOIN clients c ON c.id=ct.client_id ORDER BY d.updated_at DESC`).all()));
app.post('/api/admin/devices',admin,(req,res)=>{ const id=crypto.randomUUID(), code=crypto.randomBytes(5).toString('hex').toUpperCase(); db.prepare('INSERT INTO devices(id,enrollment_code,updated_at) VALUES(?,?,?)').run(id,code,now()); audit('DEVICE_CREATED','device',id,code); res.json({id,enrollmentCode:code}); });
app.post('/api/admin/devices/:id/state',admin,(req,res)=>{ const {status,message=''}=req.body; if(!['ACTIVE','LOCKED','PAYMENT_DUE'].includes(status)) return res.status(400).json({error:'Invalid state'}); const info=db.prepare('UPDATE devices SET status=?,message=?,updated_at=? WHERE id=?').run(status,message,now(),req.params.id); audit('DEVICE_STATE_CHANGED','device',req.params.id,`${status}: ${message}`); res.json({updated:info.changes}); });
app.post('/api/admin/devices/:id/uninstall-policy',admin,(req,res)=>{ const blocked=Boolean(req.body.blocked); const info=db.prepare('UPDATE devices SET uninstall_blocked=?,updated_at=? WHERE id=?').run(blocked?1:0,now(),req.params.id); audit(blocked?'UNINSTALL_BLOCKED':'UNINSTALL_ALLOWED','device',req.params.id); res.json({updated:info.changes,blocked}); });

app.get('/api/admin/clients',admin,(req,res)=>res.json(db.prepare('SELECT * FROM clients ORDER BY created_at DESC').all()));
app.post('/api/admin/clients',admin,(req,res)=>{ const {name,phone='',email=''}=req.body; if(!name) return res.status(400).json({error:'Name required'}); const id=crypto.randomUUID(); db.prepare('INSERT INTO clients(id,name,phone,email,created_at) VALUES(?,?,?,?,?)').run(id,name,phone,email,now()); audit('CLIENT_CREATED','client',id,name); res.json({id}); });

app.get('/api/admin/contracts',admin,(req,res)=>res.json(db.prepare(`SELECT ct.*, c.name client_name, d.device_name FROM contracts ct JOIN clients c ON c.id=ct.client_id LEFT JOIN devices d ON d.id=ct.device_id ORDER BY ct.created_at DESC`).all()));
app.post('/api/admin/contracts',admin,(req,res)=>{ const {clientId,deviceId=null,totalAmount=0,monthlyAmount=0,dueDay=1}=req.body; if(!clientId) return res.status(400).json({error:'clientId required'}); const id=crypto.randomUUID(); db.prepare('INSERT INTO contracts(id,client_id,device_id,total_amount,monthly_amount,due_day,status,created_at) VALUES(?,?,?,?,?,?,?,?)').run(id,clientId,deviceId,totalAmount,monthlyAmount,dueDay,'ACTIVE',now()); audit('CONTRACT_CREATED','contract',id); res.json({id}); });

app.get('/api/admin/payments',admin,(req,res)=>res.json(db.prepare(`SELECT p.*, ct.client_id, c.name client_name FROM payments p JOIN contracts ct ON ct.id=p.contract_id JOIN clients c ON c.id=ct.client_id ORDER BY p.due_date DESC`).all()));
app.post('/api/admin/payments',admin,(req,res)=>{ const {contractId,amount,dueDate}=req.body; if(!contractId||!amount||!dueDate) return res.status(400).json({error:'contractId, amount and dueDate required'}); const id=crypto.randomUUID(); db.prepare('INSERT INTO payments(id,contract_id,amount,due_date,status) VALUES(?,?,?,?,?)').run(id,contractId,amount,dueDate,'PENDING'); audit('PAYMENT_CREATED','payment',id); res.json({id}); });
app.post('/api/admin/payments/:id/pay',admin,(req,res)=>{ const info=db.prepare("UPDATE payments SET status='PAID',paid_at=? WHERE id=?").run(now(),req.params.id); audit('PAYMENT_PAID','payment',req.params.id); res.json({updated:info.changes}); });

app.get('/api/admin/audit',admin,(req,res)=>res.json(db.prepare('SELECT * FROM audit ORDER BY created_at DESC LIMIT 200').all()));

const port=process.env.PORT||3000;
app.listen(port,()=>console.log(`CREDIXCEL Control API running on :${port}`));
