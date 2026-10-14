/** Real private HTTP/SQLite integration; only the read-only Supabase snapshot is
 * simulated. Everything uses a disposable studio. No worker or remote writes.
 * Run: node tests/browser-cloud-config.cjs (Playwright + Chromium required).
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const python = process.env.PLAYLIST_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const home = fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-cloud-config-data-'));
const artifacts = process.env.PLAYLIST_CLOUD_BROWSER_ARTIFACTS || fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-cloud-config-'));
const password = randomBytes(24).toString('base64url');
const invalidKey = 'sb_secret_invalid_fixture';
const privateKey = `sb_secret_valid_fixture_${randomBytes(12).toString('hex')}`;
const draftKey = 'sb_secret_unsaved_fixture';
const report = { backend: 'real private HTTP and disposable SQLite', cloud: 'simulated read-only snapshot only',
  remoteWrites: false, workerStarted: false, checks: [], pageErrors: [], externalRequests: [], screenshots: [] };
let browser, child, env, adminUrl;

const fixture = `
import json,os,sqlite3,sys,time
from pathlib import Path
import uvicorn
from playlist25 import db,cloud_sync
from playlist25.app import create_app
from playlist25.config import Settings
home=Path(os.environ['PLAYLIST25_HOME'])
cfg=Settings(root=home,data_dir=home/'existing data',admin_password=os.environ['ADMIN_PASSWORD'],
 session_secret=os.environ['SESSION_SECRET'],admin_port=int(os.environ['ADMIN_PORT']),
 guest_port=int(os.environ['GUEST_PORT']),min_free_mb=0)
if sys.argv[1]=='seed':
 home.mkdir(exist_ok=True)
 (home/'.env.local').write_text('# Existing studio must be preserved\\nADMIN_PASSWORD='+cfg.admin_password+'\\nSESSION_SECRET='+cfg.session_secret+"\\nDATA_DIR='existing data'\\nOUTPUT_FORMAT='source'\\n",encoding='utf-8')
 db.init_db(cfg)
 with db.connect(cfg) as conn:
  conn.execute("INSERT INTO guests(id,nickname,created_at,avatar_data,avatar_version) VALUES('existing-guest','Camille',?,'fixture-avatar','fixture-version')",(db.now(),))
  source=db.upsert_source(conn,dict(title='Existing music request',artist='Existing artist'))
  proposal=conn.execute("INSERT INTO proposals(source_id,guest_id,state,sort_order,created_at) VALUES(?,'existing-guest','approved',8,?)",(source,db.now())).lastrowid
  conn.execute("INSERT INTO votes(proposal_id,guest_id,created_at) VALUES(?,'existing-guest',?)",(proposal,db.now()))
 (cfg.audio_dir/'preserved.mp3').write_bytes(b'unchanged local fixture bytes')
elif sys.argv[1]=='snapshot':
 with sqlite3.connect(cfg.db_path.as_uri()+'?mode=ro',uri=True) as conn:
  conn.row_factory=sqlite3.Row
  print(json.dumps({table:[dict(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in ['sources','proposals','votes','guests','jobs','job_events','settings']},ensure_ascii=False))
else:
 def snapshot(candidate):
  with (home/'readonly-calls.jsonl').open('a',encoding='utf-8') as out:
   out.write(json.dumps(dict(url=candidate.supabase_url,valid=candidate.supabase_service_role_key==os.environ['FIXTURE_PRIVATE_KEY']))+'\\n')
  time.sleep(.35)
  if candidate.supabase_service_role_key!=os.environ['FIXTURE_PRIVATE_KEY']:
   raise cloud_sync.CloudSyncError('Supabase refuse la synchronisation. '+candidate.supabase_service_role_key)
  return dict(schema_version=1,settings={},guests=[],sources=[],proposals=[],votes=[])
 def forbidden(*args,**kwargs):
  raise AssertionError('No cloud publish, import, or network RPC is permitted in this test')
 cloud_sync.fetch_snapshot=snapshot
 cloud_sync.pull=cloud_sync.push=cloud_sync._rpc=forbidden
 uvicorn.run(create_app('admin',cfg),host='127.0.0.1',port=cfg.admin_port,access_log=False,log_level='warning')
`;

function checked(label) { report.checks.push(label); process.stdout.write(`✓ ${label}\n`); }
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function port() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const number = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return number;
}
function snapshot() { return JSON.parse(execFileSync(python, ['-c', fixture, 'snapshot'], { cwd: root, env, encoding: 'utf8' })); }
function cloudCalls() {
  const file = path.join(home, 'readonly-calls.jsonl');
  return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
}
function assertPreserved(initial) {
  assert.deepEqual(snapshot(), initial);
  assert.equal(fs.readFileSync(path.join(home, 'existing data', 'audio', 'preserved.mp3'), 'utf8'), 'unchanged local fixture bytes');
  for (const directory of ['temporary', 'exports']) assert.deepEqual(fs.readdirSync(path.join(home, 'existing data', directory)), []);
}
async function waitHealth() {
  const until = Date.now() + 20000;
  while (Date.now() < until) {
    if (child.exitCode !== null) throw new Error(`Disposable server exited: ${child.logs}`);
    try { if ((await fetch(`${adminUrl}/health`)).ok) return; } catch {}
    await pause(100);
  }
  throw new Error(`Disposable server failed to start: ${child.logs}`);
}
async function settings(page) {
  await page.locator('[data-view="settings"]').click();
  await page.locator('#cloud-config-form').waitFor({ state: 'visible' });
}
async function submit(page, expectedStatus) {
  const requested = page.waitForRequest(request => new URL(request.url()).pathname === '/api/cloud/configure');
  const response = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/cloud/configure');
  await page.locator('#cloud-config-submit').click();
  const request = await requested;
  assert.equal(await page.locator('#cloud-config-submit').isDisabled(), true);
  assert.equal(await page.locator('#cloud-project-url').isDisabled(), true);
  assert.equal(await page.locator('#cloud-private-key').isDisabled(), true);
  assert.equal(await page.locator('#cloud-sync').isDisabled(), true);
  const result = await response;
  assert.equal(result.status(), expectedStatus);
  const text = await result.text();
  for (const key of [invalidKey, privateKey, draftKey]) assert.ok(!text.includes(key), 'Private key must never appear in an API response');
  await page.waitForFunction(() => !document.querySelector('#cloud-config-submit').disabled);
  return request.postDataJSON();
}
async function noBrowserSecrets(page) {
  const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  for (const key of [invalidKey, privateKey, draftKey]) assert.ok(!stored.includes(key), 'Private key must never be stored in browser storage');
}
async function screen(page, name) {
  const target = path.join(artifacts, `${name}.png`);
  await page.screenshot({ path: target, fullPage: true }); report.screenshots.push(target);
}

async function main() {
  const adminPort = await port(), guestPort = await port();
  adminUrl = `http://127.0.0.1:${adminPort}`;
  env = { ...process.env, PLAYLIST25_HOME: home, DATA_DIR: path.join(home, 'existing data'), ADMIN_PORT: String(adminPort),
    GUEST_PORT: String(guestPort), ADMIN_PASSWORD: password, SESSION_SECRET: randomBytes(48).toString('base64url'),
    SUPABASE_URL: '', SUPABASE_SERVICE_ROLE_KEY: '', SUPABASE_ANON_KEY: '', FIXTURE_PRIVATE_KEY: privateKey };
  execFileSync(python, ['-c', fixture, 'seed'], { cwd: root, env });
  const initial = snapshot(), originalConfig = fs.readFileSync(path.join(home, '.env.local'));
  child = spawn(python, ['-c', fixture, 'serve'], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  child.logs = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { child.logs = (child.logs + chunk).slice(-5000); });
  await waitHealth();
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined),
    args: process.platform === 'linux' ? ['--no-sandbox'] : [] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route('**/*', async route => {
    if (new URL(route.request().url()).origin === adminUrl) return route.continue();
    report.externalRequests.push(route.request().url()); await route.abort('blockedbyclient');
  });
  const anonymous = await context.request.post(`${adminUrl}/api/cloud/configure`, { data: { project_url: 'https://fixture.supabase.co', key: privateKey } });
  assert.equal(anonymous.status(), 401); assert.deepEqual(cloudCalls(), []);
  checked('Connexion cloud refusée sans session privée, avant toute vérification ou écriture');
  const page = await context.newPage();
  page.on('pageerror', error => report.pageErrors.push(error.message));
  await page.goto(adminUrl); await page.locator('#admin-password').fill(password);
  await page.locator('#login-form button[type="submit"]').click(); await page.locator('#admin-layout').waitFor({ state: 'visible' });
  assert.equal((await context.request.post(`${adminUrl}/api/cloud/configure`, { data: { project_url: 'https://fixture.supabase.co', key: privateKey } })).status(), 403);
  assert.deepEqual(cloudCalls(), []); checked('La configuration privée exige aussi le jeton CSRF');
  await settings(page);
  await page.waitForFunction(() => document.querySelector('#cloud-key-help').textContent.includes('Collez'));
  assert.equal(await page.locator('#cloud-private-key').getAttribute('type'), 'password');
  assert.equal(await page.locator('#cloud-private-key').inputValue(), '');
  assert.equal(await page.locator('#cloud-sync').isDisabled(), true);
  checked('Studio initial non configuré : clé masquée et vide, collecte indisponible');

  await page.locator('#cloud-project-url').fill('https://invalid-project.supabase.co');
  await page.locator('#cloud-private-key').fill(invalidKey);
  await submit(page, 503);
  assert.match(await page.locator('#cloud-config-feedback').innerText(), /conservée/);
  assert.ok(!(await page.locator('#cloud-config-feedback').innerText()).includes(invalidKey));
  assert.ok(fs.readFileSync(path.join(home, '.env.local')).equals(originalConfig));
  assertPreserved(initial); await noBrowserSecrets(page);
  checked('Clé refusée : erreur sans écho du secret, configuration/propositions/votes/photos/audio conservés');

  await page.locator('#cloud-project-url').fill('https://valid-project.supabase.co/');
  await page.locator('#cloud-private-key').fill(privateKey);
  const submitted = await submit(page, 200);
  assert.equal(submitted.key, privateKey);
  await page.waitForFunction(() => !document.querySelector('#cloud-sync').disabled && !document.querySelector('#cloud-publish').disabled);
  assert.equal(await page.locator('#cloud-project-url').inputValue(), 'https://valid-project.supabase.co');
  assert.equal(await page.locator('#cloud-private-key').inputValue(), '');
  const saved = fs.readFileSync(path.join(home, '.env.local'), 'utf8');
  assert.ok(saved.includes(privateKey), 'Private key must be saved only in the disposable private configuration');
  for (const line of originalConfig.toString().trim().split('\n')) assert.ok(saved.includes(line), 'Existing configuration fields must remain unchanged');
  assertPreserved(initial); await noBrowserSecrets(page);
  assert.deepEqual(cloudCalls(), [{ url: 'https://invalid-project.supabase.co', valid: false }, { url: 'https://valid-project.supabase.co', valid: true }]);
  checked('Vérification seule avant écriture locale atomique ; connexion appliquée immédiatement sans redémarrage');
  checked('Boutons et champs désactivés pendant la vérification ; clé effacée après succès');

  await page.locator('#cloud-project-url').fill('https://moved-project.supabase.co');
  const reused = await submit(page, 200);
  assert.ok(!Object.hasOwn(reused, 'key'), 'An empty key field must be omitted, preserving the saved key');
  assert.ok(fs.readFileSync(path.join(home, '.env.local'), 'utf8').includes(privateKey));
  assertPreserved(initial); checked('Clé laissée vide : la clé privée enregistrée est conservée et vérifiée');
  await page.reload(); await page.locator('#admin-layout').waitFor({ state: 'visible' }); await settings(page);
  await page.waitForFunction(() => document.querySelector('#cloud-project-url').value === 'https://moved-project.supabase.co');
  assert.equal(await page.locator('#cloud-private-key').inputValue(), '');
  const status = await (await context.request.get(`${adminUrl}/api/cloud/status`)).text();
  assert.ok(status.includes('secret_present')); assert.ok(!status.includes(privateKey));
  checked('Actualisation : seule l’adresse est préremplie, statut sans secret et clé jamais renvoyée');
  await screen(page, 'cloud-config-desktop');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Settings must fit a narrow viewport');
  await screen(page, 'cloud-config-mobile'); checked('Formulaire lisible à 1440 et 390 pixels, sans débordement horizontal');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('#cloud-private-key').fill(draftKey);
  await page.locator('#logout-button').click(); await page.locator('#login-screen').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#cloud-private-key').inputValue(), '');
  await noBrowserSecrets(page); assertPreserved(initial);
  checked('Déconnexion : clé en cours de saisie effacée, aucun secret dans localStorage/sessionStorage');
  assert.deepEqual(report.pageErrors, []); assert.deepEqual(report.externalRequests, []);
  assert.equal(cloudCalls().length, 3);
  checked('Aucune connexion externe, publication cloud, import, tâche audio ou exception JavaScript');
}

(async () => {
  try { await main(); report.success = true; }
  catch (error) { report.success = false; report.error = error.stack || String(error); process.exitCode = 1; process.stderr.write(`${report.error}\n`); }
  finally {
    if (browser) await browser.close().catch(() => {});
    if (child && child.exitCode === null && child.signalCode === null) {
      const exited = new Promise(resolve => child.once('exit', resolve));
      let timer; child.kill('SIGTERM');
      await Promise.race([exited, new Promise(resolve => { timer = setTimeout(resolve, 5000); })]); clearTimeout(timer);
      if (child.exitCode === null && child.signalCode === null) { child.kill('SIGKILL'); await exited; }
    }
    fs.mkdirSync(artifacts, { recursive: true });
    fs.writeFileSync(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2));
    fs.rmSync(home, { recursive: true, force: true });
    process.stdout.write(`Rapport navigateur : ${path.join(artifacts, 'report.json')}\n`);
  }
})();
