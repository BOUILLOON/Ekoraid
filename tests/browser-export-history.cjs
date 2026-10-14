/** Real private API/SQLite/ZIP browser integration using generated audio only.
 * Interface detection is simulated for portable QR creation; no Wi-Fi device,
 * cloud call, worker or new export is started. Run with Node + Playwright.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const { randomBytes, createHash } = require('node:crypto');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const python = process.env.PLAYLIST_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const home = fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-history-data-'));
const artifacts = process.env.PLAYLIST_HISTORY_BROWSER_ARTIFACTS || fs.mkdtempSync(path.join(os.tmpdir(), 'playlist25-history-browser-'));
const password = randomBytes(24).toString('base64url');
const report = { backend: 'real HTTP, disposable SQLite and closed ZIP fixtures', audio: 'generated PCM WAV',
  transferInterfaces: 'simulated private interface enumeration only', physicalWifiTested: false,
  cloudRequests: false, workerStarted: false, checks: [], pageErrors: [], externalRequests: [], screenshots: [], exportPosts: 0 };
let browser, child, env, adminUrl;

const fixture = `
import io,json,math,os,sqlite3,struct,sys,wave,zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
import uvicorn
from playlist25 import db,cloud_sync,transfers
from playlist25.app import create_app
from playlist25.config import Settings
home=Path(os.environ['PLAYLIST25_HOME'])
cfg=Settings(root=home,data_dir=home/'data',admin_password=os.environ['ADMIN_PASSWORD'],session_secret=os.environ['SESSION_SECRET'],
 admin_port=int(os.environ['ADMIN_PORT']),guest_port=int(os.environ['GUEST_PORT']),min_free_mb=0)
if sys.argv[1]=='seed':
 db.init_db(cfg)
 wavbytes=io.BytesIO()
 with wave.open(wavbytes,'wb') as wav:
  wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(8000)
  wav.writeframes(b''.join(struct.pack('<h',int(1000*math.sin(2*math.pi*440*i/8000))) for i in range(1000)))
 audio=wavbytes.getvalue()
 with db.connect(cfg) as conn:
  for source_id,title in [(1,'Audio synthétique A'),(2,'Audio synthétique B')]:
   filename='source'+str(source_id).zfill(6)+'.wav'
   (cfg.audio_dir/filename).write_bytes(audio)
   conn.execute("INSERT INTO sources(id,title,duration,status,file_path,created_at) VALUES(?,?,.125,'available',?,?)",(source_id,title,filename,db.now()))
  manifest=dict(version=1,count=1,sources=[dict(id=1,title='Audio synthétique A',file='audio/source000001.wav')],warnings=[])
  buffer=io.BytesIO()
  with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
   archive.writestr('Anniversaire25/audio/source000001.wav',audio)
   archive.writestr('Anniversaire25/01-selection.m3u8','#EXTM3U\\n#EXTINF:0,Audio synthétique A\\naudio/source000001.wav\\n')
   archive.writestr('Anniversaire25/manifeste.json',json.dumps(manifest,ensure_ascii=False))
  for index in range(1,102):
   export_id=format(index,'032x');directory=cfg.export_dir/export_id;directory.mkdir()
   archive=cfg.export_dir/(export_id+'.zip');archive.write_bytes(buffer.getvalue())
   created=(datetime(2026,10,14,12,tzinfo=timezone.utc)+timedelta(seconds=index)).isoformat()
   conn.execute('INSERT INTO exports(id,directory,archive,created_at) VALUES(?,?,?,?)',(export_id,str(directory),str(archive),created))
 print(json.dumps(dict(latest=format(101,'032x'),oldest=format(1,'032x'),count=101)))
elif sys.argv[1]=='snapshot':
 with sqlite3.connect(cfg.db_path.as_uri()+'?mode=ro',uri=True) as conn:
  conn.row_factory=sqlite3.Row
  print(json.dumps({table:[dict(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in ['sources','exports','jobs','job_events','transfers']},ensure_ascii=False))
else:
 def forbidden(*args,**kwargs):raise AssertionError('No cloud or new export is allowed in the history test')
 cloud_sync.fetch_snapshot=cloud_sync._rpc=cloud_sync.pull=cloud_sync.push=forbidden
 transfers.transfer_addresses=lambda settings:[dict(host='192.168.50.12',label='Wi-Fi fixture — 192.168.50.12')]
 uvicorn.run(create_app('admin',cfg),host='127.0.0.1',port=cfg.admin_port,access_log=False,log_level='warning')
`;
function checked(label) { report.checks.push(label); process.stdout.write(`✓ ${label}\n`); }
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function port() {
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const number = server.address().port; await new Promise(resolve => server.close(resolve)); return number;
}
function snapshot() { return JSON.parse(execFileSync(python, ['-c', fixture, 'snapshot'], { cwd: root, env, encoding: 'utf8' })); }
async function waitHealth() {
  const until = Date.now() + 20000;
  while (Date.now() < until) {
    if (child.exitCode !== null) throw new Error(`Disposable server exited: ${child.logs}`);
    try { if ((await fetch(`${adminUrl}/health`)).ok) return; } catch {}
    await pause(100);
  }
  throw new Error(`Disposable server failed to start: ${child.logs}`);
}
async function context() {
  const result = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  await result.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== adminUrl) { report.externalRequests.push(request.url()); return route.abort('blockedbyclient'); }
    if (url.pathname === '/api/exports' && request.method() === 'POST') report.exportPosts++;
    return route.continue();
  });
  return result;
}
async function login(context, targetId) {
  const page = await context.newPage(); page.on('pageerror', error => report.pageErrors.push(error.message));
  await page.goto(`${adminUrl}/#exports`); await page.locator('#admin-password').fill(password);
  await page.locator('#login-form button[type="submit"]').click();
  await page.locator(`[data-history-export-id="${targetId}"]`).waitFor(); return page;
}
async function refresh(page) {
  const reply = page.waitForResponse(response => new URL(response.url()).pathname === '/api/exports' && response.request().method() === 'GET');
  await page.locator('#refresh-export-history').click(); assert.equal((await reply).status(), 200);
  await page.waitForFunction(() => !document.querySelector('#refresh-export-history').disabled);
}
async function screen(page, name) {
  const target = path.join(artifacts, `${name}.png`); await page.screenshot({ path: target }); report.screenshots.push(target);
}
async function main() {
  const adminPort = await port(), guestPort = await port(); adminUrl = `http://127.0.0.1:${adminPort}`;
  env = { ...process.env, PLAYLIST25_HOME: home, DATA_DIR: path.join(home, 'data'), ADMIN_PORT: String(adminPort),
    GUEST_PORT: String(guestPort), ADMIN_PASSWORD: password, SESSION_SECRET: randomBytes(48).toString('base64url'),
    SUPABASE_URL: '', SUPABASE_SERVICE_ROLE_KEY: '', SUPABASE_ANON_KEY: '' };
  const seeded = JSON.parse(execFileSync(python, ['-c', fixture, 'seed'], { cwd: root, env, encoding: 'utf8' }));
  const initial = snapshot(), expectedZip = fs.readFileSync(path.join(home, 'data', 'exports', `${seeded.latest}.zip`));
  child = spawn(python, ['-c', fixture, 'serve'], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] }); child.logs = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { child.logs = (child.logs + chunk).slice(-5000); });
  await waitHealth();
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined), args: process.platform === 'linux' ? ['--no-sandbox'] : [] });
  const first = await context(); assert.equal((await first.request.get(`${adminUrl}/api/exports`)).status(), 401);
  const page = await login(first, seeded.latest);
  assert.equal(await page.locator('.export-history-row').count(), 100);
  assert.equal(await page.locator('#pack-history-page-label').innerText(), 'Page 1 sur 2');
  assert.match(await page.locator('#export-history .export-history-row').first().innerText(), /1 fichier/);
  const history = await (await first.request.get(`${adminUrl}/api/exports`)).json();
  assert.equal(history.items.length, seeded.count); assert.equal(history.items[0].id, seeded.latest);
  assert.ok(!JSON.stringify(history).includes(home)); checked('101 ZIP préexistants découverts par API privée, plus récent en tête, sans chemins locaux');
  await page.locator('#pack-history-next').click(); await page.locator(`[data-history-export-id="${seeded.oldest}"]`).waitFor();
  assert.equal(await page.locator('.export-history-row').count(), 1);
  assert.equal(await page.locator('#pack-history-page-label').innerText(), 'Page 2 sur 2');
  await page.locator('#pack-history-prev').click(); await page.locator(`[data-history-export-id="${seeded.latest}"]`).waitFor();
  checked('Pagination 100 + 1 packs : dernier pack accessible, retour sans recréation');

  await page.locator('#export-select-none').click();
  await page.locator('#export-selection label').filter({ hasText: 'Audio synthétique B' }).locator('input').check();
  await page.locator('#export-query').fill('synthétique B');
  assert.match(await page.locator('#export-selection-count').innerText(), /^1 fichier/);
  await refresh(page);
  assert.equal(await page.locator('#export-query').inputValue(), 'synthétique B');
  assert.equal(await page.locator('#export-selection input:checked').count(), 1);
  assert.match(await page.locator('#export-selection-count').innerText(), /^1 fichier/);
  checked('Actualiser uniquement les packs conserve recherche et sélection destinée au prochain ZIP');
  const downloaded = page.waitForEvent('download'); await page.locator(`[data-history-export-id="${seeded.latest}"]`).click();
  const download = await downloaded; assert.equal(await download.failure(), null);
  const received = fs.readFileSync(await download.path()); assert.ok(received.equals(expectedZip));
  report.downloadSha256 = createHash('sha256').update(received).digest('hex');
  checked('Téléchargement depuis l’historique identique octet par octet au ZIP créé avant lancement');

  await page.reload(); await page.locator(`[data-history-export-id="${seeded.latest}"]`).waitFor();
  assert.equal(await page.locator('.export-history-row').count(), 100); checked('Rechargement de la page retrouve les packs déjà prêts');
  const fresh = await context(); assert.equal((await fresh.request.get(`${adminUrl}/api/exports`)).status(), 401);
  const newPage = await login(fresh, seeded.latest); checked('Nouvelle session connectée retrouve les mêmes ZIP sans POST /api/exports');
  await newPage.locator(`[data-history-share-id="${seeded.latest}"]`).click(); await newPage.locator('#transfer-dialog').waitFor({ state: 'visible' });
  await newPage.locator('#transfer-host option[value="192.168.50.12"]').waitFor({ state: 'attached' });
  await newPage.locator('#transfer-host').selectOption('192.168.50.12');
  const transferReply = newPage.waitForResponse(response => new URL(response.url()).pathname === '/api/transfers' && response.request().method() === 'POST');
  await newPage.locator('#transfer-create').click(); const reply = await transferReply;
  assert.equal(reply.status(), 200); assert.equal(reply.request().postDataJSON().export_id, seeded.latest);
  const transfer = await reply.json(); assert.equal(transfer.size, expectedZip.length);
  await newPage.locator('#transfer-result').waitFor({ state: 'visible' }); await newPage.locator('#transfer-qr').evaluate(image => image.decode());
  assert.match(await newPage.locator('#transfer-expiry').innerText(), /10 minutes|expire|valable/i);
  const revoked = newPage.waitForResponse(response => new URL(response.url()).pathname === '/api/transfers/revoke');
  await newPage.locator('#transfer-revoke').click(); assert.equal((await revoked).status(), 200);
  await newPage.locator('#transfer-dialog').waitFor({ state: 'hidden' });
  checked('Partage du pack existant : bon identifiant, QR créé par API réelle puis partage révoqué (interface simulée)');
  await newPage.locator('#export-history').scrollIntoViewIfNeeded(); await screen(newPage, 'history-desktop');
  await newPage.setViewportSize({ width: 390, height: 844 });
  assert.ok(await newPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await screen(newPage, 'history-mobile'); checked('Historique et liens lisibles au bureau et à 390 pixels sans débordement');

  fs.unlinkSync(path.join(home, 'data', 'exports', `${seeded.latest}.zip`));
  await refresh(newPage); assert.equal(await newPage.locator(`[data-history-export-id="${seeded.latest}"]`).count(), 0);
  assert.equal(await newPage.locator('.export-history-row').count(), 100);
  checked('ZIP supprimé du disque omis après actualisation, sans toucher les autres packs');
  const after = snapshot(); assert.deepEqual(after.sources, initial.sources); assert.deepEqual(after.exports, initial.exports);
  assert.deepEqual(after.jobs, []); assert.deepEqual(after.job_events, []);
  assert.equal(after.transfers.length, 1); assert.equal(after.transfers[0].export_id, seeded.latest); assert.equal(after.transfers[0].revoked, 1);
  assert.equal(report.exportPosts, 0); assert.deepEqual(report.pageErrors, []); assert.deepEqual(report.externalRequests, []);
  checked('Aucune nouvelle préparation, connexion externe ou erreur JavaScript ; sources et catalogue des packs inchangés');
}
(async () => {
  try { await main(); report.success = true; }
  catch (error) { report.success = false; report.error = error.stack || String(error); process.exitCode = 1; process.stderr.write(`${report.error}\n`); }
  finally {
    if (browser) await browser.close().catch(() => {});
    if (child && child.exitCode === null && child.signalCode === null) {
      const exited = new Promise(resolve => child.once('exit', resolve)); let timer; child.kill('SIGTERM');
      await Promise.race([exited, new Promise(resolve => { timer = setTimeout(resolve, 5000); })]); clearTimeout(timer);
      if (child.exitCode === null && child.signalCode === null) { child.kill('SIGKILL'); await exited; }
    }
    fs.mkdirSync(artifacts, { recursive: true }); fs.writeFileSync(path.join(artifacts, 'report.json'), JSON.stringify(report, null, 2));
    fs.rmSync(home, { recursive: true, force: true }); process.stdout.write(`Rapport navigateur : ${path.join(artifacts, 'report.json')}\n`);
  }
})();
