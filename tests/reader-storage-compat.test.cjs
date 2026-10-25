const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../textfold/web/reader.js'), 'utf8');
test('HTTP saved pages create distinct note IDs without randomUUID or crypto', () => {
  const start = source.indexOf('  const noteId ='), end = source.indexOf('  function saveError', start);
  for (const crypto of [undefined, {getRandomValues: bytes => { require('node:crypto').randomFillSync(bytes); }}]) {
    const scope = { crypto }; vm.runInNewContext(source.slice(start,end) + '; globalThis.make = noteId;', scope);
    const ids = new Set(Array.from({length:1000}, () => scope.make()));
    assert.equal(ids.size,1000); for (const id of ids) assert.match(id,/^n-[a-f0-9]{32}$/);
  }
});
test('local mode starts no cloud status or resume requests', async () => {
  const app = fs.readFileSync(path.join(__dirname, '../textfold/web/app.js'), 'utf8');
  const start = app.indexOf('  async function pollStorage()'), end = app.indexOf("  addEventListener('online'",start);
  const scope = {BOOT:{cloudSync:false}, document:{hidden:false}, api:()=>{throw new Error('Local mode requested cloud state');}, syncPolling:false};
  vm.runInNewContext(app.slice(start,end)+'; globalThis.poll = pollStorage; globalThis.resume = resumeStorage;',scope);
  await scope.poll(); scope.resume('focus');
  assert.match(app, /if \(BOOT.cloudSync\) \{ setInterval\(pollStorage/);
});
test('all twelve interfaces have localized cloud storage and sync controls', () => {
  const scope={window:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../textfold/web/i18n.js'),'utf8'),scope);
  const api=scope.window.DR_I18N;
  assert.equal(api.CODES.length,12);
  for (const code of api.CODES) {
    const t=api.t(code);
    for(const key of ['cloud_title','cloud_kept','sync_save_failed','sync_saved_local','sync_unanswered','storage_existing']) assert.equal(typeof t[key], 'string');
    if(!['en','zh'].includes(code)) assert.notEqual(t.sync_save_failed,api.t('en').sync_save_failed);
  }
});

test('changing a pending local-library action saves even if its path is unchanged', async () => {
  const app=fs.readFileSync(path.join(__dirname,'../textfold/web/app.js'),'utf8');
  const start=app.indexOf('      const saveStorage = async (' ),end=app.indexOf('      const storageSave',start);
  assert.ok(start>=0 && end>start);
  const calls=[],scope={storageBusy:false,storageError:'',cloudError:'',cloudTested:false,
    dlg:{isConnected:true},cloudDraft:{enabled:false},storageDraft:null,storageMode:'existing',render(){},
    data:{storage:{path:'/old',pending:'/existing',mode:'copy'},cloudStorage:{enabled:false}},
    api:async(path,body)=>{calls.push({path,body});return{path:'/old',pending:'/existing',mode:body.mode};}};
  vm.runInNewContext(app.slice(start,end)+';globalThis.save=saveStorage;',scope);
  await scope.save();
  assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/storage');
  assert.equal(calls[0].body.path,'/existing');assert.equal(calls[0].body.mode,'existing');
});

test('unified cloud settings retain upstream folder error codes in every interface language', () => {
  const app=fs.readFileSync(path.join(__dirname,'../textfold/web/app.js'),'utf8');
  const strings={window:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../textfold/web/i18n.js'),'utf8'),strings);
  const i18n=strings.window.DR_I18N;
  const start=app.indexOf('    function storageSection()'),end=app.indexOf('    function syncSection()',start);
  const errorStart=app.indexOf('    function storageErrorMessage('),errorEnd=app.indexOf('    function modelSection()',errorStart);
  for(const lang of i18n.CODES) {
    const scope={T:()=>i18n.t(lang),esc:String,window:{},cloudDraft:{enabled:false},cloudBusy:false,storageBusy:false,
      storageDraft:null,storageMode:null,cloudError:'',cloudTested:false,syncSection:()=>'',
      storageError:{code:'path',message:'Original English diagnostic'},data:{storage:{path:'/library',mode:'copy'}}};
    vm.runInNewContext(app.slice(errorStart,errorEnd)+app.slice(start,end)+';globalThis.html=storageSection();',scope);
    assert.ok(scope.html.includes(i18n.t(lang).storage_err_path),lang);
    assert.ok(!scope.html.includes('Original English diagnostic'),lang);
  }
});
