// Actual Chromium interactions with the real API. No response mocks or HAR replay.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {randomUUID} = require('node:crypto');

const base = process.env.CLINIC_BROWSER_URL || 'http://api:8080';
const output = process.env.BROWSER_EVIDENCE || '/evidence';
const offline = process.env.BROWSER_BASELINE_ONLINE !== '1';
const seed = Number(process.env.CF_SEED || 126021);
const sentinel = 'Pessoa Sentinela ZQX';
let browser;
const cases = [];
function check(name, run) { cases.push({name, run}); }
function block(page, method, route) {
  return page.locator('.opblock-' + method).filter({has: page.locator('.opblock-summary-path').filter({hasText: new RegExp('^' + route.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$')})});
}
async function docs(context) {
  const page = await context.newPage();
  await page.goto(base + '/docs');
  await page.locator('.opblock-summary').first().waitFor({timeout: 10000});
  return page;
}
async function activate(page, method, route) {
  const operation = block(page, method, route);
  await operation.locator('.opblock-summary').click();
  await operation.getByRole('button', {name: 'Try it out', exact: true}).click();
  return operation;
}
async function execute(page, operation, method, route) {
  const response = page.waitForResponse(r => r.url() === base + route && r.request().method() === method, {timeout: 12000});
  await operation.getByRole('button', {name: 'Execute', exact: true}).click();
  const result = await response;
  await operation.locator('.responses-wrapper .live-responses-table').waitFor();
  return {status: result.status(), body: await result.json(), text: await operation.locator('.live-responses-table').innerText()};
}
async function post(context, body) {
  const page = await docs(context);
  const operation = await activate(page, 'post', '/appointments');
  if (body !== undefined) await operation.locator('textarea').fill(typeof body === 'string' ? body : JSON.stringify(body));
  const result = await execute(page, operation, 'POST', '/appointments');
  return {page, operation, ...result};
}
async function schema(context) {
  const response = await context.request.get(base + '/openapi.json');
  assert.equal(response.status(), 200);
  return response.json();
}
async function example(context) {
  const page = await docs(context);
  const operation = await activate(page, 'post', '/appointments');
  const value = JSON.parse(await operation.locator('textarea').inputValue());
  await page.close();
  return value;
}
async function valid(context) {
  const s = await schema(context);
  // Use the actual running catalog, never a hard-coded stale version.
  const body = s.paths['/appointments'].post.requestBody.content['application/json'].example;
  assert.ok(body, 'OpenAPI must provide the exact executable fictional request');
  return {...body, request_id: randomUUID()};
}
function noPII(value) { assert.ok(!JSON.stringify(value).includes(sentinel), 'PII reflected'); }

check('offline Swagger renders without third-party requests', async context => {
  const page = await docs(context);
  assert.equal(await page.locator('.opblock-summary').count(), 4);
  await page.screenshot({path: path.join(output, 'swagger-overview.png'), fullPage: true});
});
check('OpenAPI declares exactly the consumed API and offline UI routes stay private', async context => {
  const s = await schema(context);
  assert.deepEqual(Object.keys(s.paths).sort(), ['/appointments', '/appointments/by-request/{request_id}', '/appointments/{appointment_id}', '/health']);
  for (const status of ['200','201','400','408','409','413','415','422','503']) assert.ok(s.paths['/appointments'].post.responses[status]);
});
check('UUID and code constraints are visible in the contract', async context => {
  const s = (await schema(context)).components.schemas;
  assert.equal(s.AppointmentRequest.properties.request_id.format, 'uuid');
  assert.equal(s.AppointmentRequest.properties.exam_codes.items.pattern, '^FICT-[0-9]{3}$');
  assert.equal(s.AppointmentRequest.properties.exam_codes.uniqueItems, true);
  assert.equal(s.AppointmentReceipt.properties.appointment_id.format, 'uuid');
});
check('Try it out default example succeeds without editing', async context => {
  const value = await example(context);
  assert.match(value.request_id, /^[0-9a-f-]{36}$/);
  assert.ok(value.exam_codes.every(x => /^FICT-[0-9]{3}$/.test(x)));
  const result = await post(context);
  assert.ok([200,201].includes(result.status), 'Default Swagger example must execute');
  assert.equal(result.body.status, 'REQUESTED');
  await result.page.screenshot({path: path.join(output, 'swagger-example.png'), fullPage: true});
});
check('UI creates a real receipt then replays without duplicating', async context => {
  const body = await valid(context);
  const first = await post(context, body);
  assert.equal(first.status, 201);
  assert.equal(first.body.request_id, body.request_id);
  const second = await execute(first.page, first.operation, 'POST', '/appointments');
  assert.equal(second.status, 200);
  assert.deepEqual(second.body, first.body);
  await first.page.screenshot({path: path.join(output, 'swagger-idempotency.png'), fullPage: true});
  await first.operation.locator('.live-responses-table').screenshot({path:path.join(output,'swagger-idempotency-detail.png')});
});
check('UI reusing a request with different exams returns 409', async context => {
  const body = await valid(context);
  const first = await post(context, body);
  assert.equal(first.status, 201);
  const second = await post(context, {...body, exam_codes:['FICT-003']});
  assert.equal(second.status, 409);
  assert.equal(second.body.detail, 'IDEMPOTENCY_CONFLICT');
});
check('UI reads the same receipt by request and appointment', async context => {
  const body = await valid(context);
  const receipt = (await post(context, body)).body;
  for (const [template, field, value] of [['/appointments/by-request/{request_id}', 'request_id', body.request_id], ['/appointments/{appointment_id}', 'appointment_id', receipt.appointment_id]]) {
    const page = await docs(context);
    const operation = await activate(page, 'get', template);
    await operation.locator('input').fill(value);
    const result = await execute(page, operation, 'GET', template.replace('{' + field + '}', value));
    assert.equal(result.status, 200);
    assert.deepEqual(result.body, receipt);
  }
});
check('CLI output is persisted and readable through Swagger', async context => {
  const requestId = process.env.CLINIC_CLI_REQUEST_ID;
  if (!requestId) throw new Error('Real CLI request id required, never skip this acceptance case');
  const page = await docs(context);
  const operation = await activate(page, 'get', '/appointments/by-request/{request_id}');
  await operation.locator('input').fill(requestId);
  const result = await execute(page, operation, 'GET', '/appointments/by-request/' + requestId);
  assert.equal(result.status, 200);
  assert.equal(result.body.request_id, requestId);
  assert.deepEqual(result.body.exam_codes, ['FICT-001', 'FICT-002', 'FICT-005']);
  await page.screenshot({path: path.join(output, 'swagger-cli-receipt.png'), fullPage: true});
  await operation.locator('.live-responses-table').screenshot({path:path.join(output,'swagger-cli-receipt-detail.png')});
});
check('422 schema matches sanitized validation, not FastAPI default', async context => {
  const s = await schema(context);
  const response = s.paths['/appointments'].post.responses['422'].content['application/json'].schema;
  const serialized = JSON.stringify(response) + JSON.stringify(s.components.schemas);
  assert.ok(!JSON.stringify(response).includes('HTTPValidationError'));
  assert.ok(serialized.includes('ValidationIssue') && serialized.includes('INVALID_REQUEST'));
  const result = await post(context, {request_id:'invalid', exam_codes:['FICT-001'], catalog_version:'0'.repeat(64)});
  assert.equal(result.status, 422);
  assert.equal(result.body.code, 'INVALID_REQUEST');
  assert.ok(result.body.errors.every(x => Object.keys(x).sort().join(',') === 'field,type'));
});
check('UI rejects extra PII fields and does not reflect their names or values', async context => {
  const body = await valid(context);
  const result = await post(context, {...body, [sentinel]: sentinel});
  assert.equal(result.status, 422);
  noPII(result.body);
  assert.ok(result.body.errors.some(x => x.field === 'body.[extra]'));
  assert.equal((await context.request.get(base+'/appointments/by-request/'+body.request_id)).status(), 404);
});
check('UI rejects duplicated JSON keys before schema parsing', async context => {
  const body = await valid(context);
  const raw = JSON.stringify(body).replace('"request_id":', '"request_id":"'+randomUUID()+'","request_id":');
  const result = await post(context, raw);
  assert.equal(result.status, 400);
  assert.equal(result.body.code, 'INVALID_JSON_ENVELOPE');
});
check('UI rejects oversized body with a bounded safe error', async context => {
  const body = await valid(context);
  const result = await post(context, {...body, unknown:'x'.repeat(5000)});
  assert.equal(result.status, 413);
  assert.equal(result.body.code, 'REQUEST_SIZE_LIMIT');
});
for (const [name, patch] of [['unknown exam', {exam_codes:['FICT-999']}], ['stale catalog', {catalog_version:'0'.repeat(64)}], ['duplicate exams', {exam_codes:['FICT-001','FICT-001']}], ['command instead of exam', {exam_codes:['__import__(os)']}]] ) {
  check('UI rejects '+name+' without creating an appointment', async context => {
    const body = {...await valid(context), ...patch};
    const result = await post(context, body);
    assert.equal(result.status, 422);
    assert.equal((await context.request.get(base+'/appointments/by-request/'+body.request_id)).status(), 404);
  });
}
check('UI missing and malformed lookups distinguish 404 and 422 safely', async context => {
  const page = await docs(context);
  const operation = await activate(page, 'get', '/appointments/by-request/{request_id}');
  const absent=randomUUID();
  await operation.locator('input').fill(absent);
  assert.equal((await execute(page, operation, 'GET', '/appointments/by-request/'+absent)).status, 404);
  const sent=[];
  page.on('request',r=>{if(r.url().includes('/appointments/by-request/invalid-uuid')) sent.push(r.url());});
  await operation.locator('input').fill('invalid-uuid');
  await operation.getByRole('button',{name:'Execute',exact:true}).click();
  await operation.getByText('Please correct the following validation errors and try again.',{exact:false}).first().waitFor();
  assert.deepEqual(sent,[], 'Swagger must reject invalid UUID before submission');
  const direct=await context.request.get(base+'/appointments/by-request/invalid-uuid');
  assert.equal(direct.status(),422);
  assert.equal((await direct.json()).code,'INVALID_REQUEST');
});
check('Mobile Swagger supports real try-execute without horizontal document overflow', async context => {
  const page = await docs(context);
  await page.setViewportSize({width:390,height:844});
  const operation = await activate(page, 'post', '/appointments');
  await operation.locator('textarea').fill(JSON.stringify(await valid(context)));
  const result = await execute(page, operation, 'POST', '/appointments');
  assert.equal(result.status, 201);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({path:path.join(output,'swagger-mobile.png'),fullPage:true});
});

async function main() {
  await fs.mkdir(output,{recursive:true});
  browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  const report = {complete:false, failures:0, skipped:0, seed, browser:'Chromium', version:browser.version(), actual_api:base, no_response_mocks:true, all_contexts_external_blocked:offline, cases:[]};
  let rng = seed >>> 0;
  const ordered = [...cases];
  for (let i=ordered.length-1;i>0;i--) {rng=(Math.imul(rng,1664525)+1013904223)>>>0;const j=rng%(i+1);[ordered[i],ordered[j]]=[ordered[j],ordered[i]];}
  try {
    for (let index=0;index<ordered.length;index++) {
      const test = ordered[index];
      const context = await browser.newContext({viewport:{width:1440,height:1000}, serviceWorkers:'block'});
      const external = [], errors = [];
      context.on('page',page=>page.on('pageerror',error=>errors.push(error.message)));
      await context.route('**/*',route=>{
        const url = new URL(route.request().url());
        if (url.origin !== new URL(base).origin) {
          external.push(url.origin);
          if (offline || test.name.startsWith('offline')) return route.abort('blockedbyclient');
        }
        return route.continue();
      });
      await context.tracing.start({screenshots:true,snapshots:true,sources:false});
      const started = Date.now();
      let record = {name:test.name, passed:false};
      try {
        await test.run(context);
        assert.deepEqual(errors,[], 'Javascript page errors');
        if (offline || test.name.startsWith('offline')) assert.deepEqual(external,[], 'No third-party dependency is allowed in offline Swagger');
        record.passed = true;
      } catch(error) {
        report.failures++;
        record.error = error.message;
        const page = context.pages().at(-1);
        if (page) await page.screenshot({path:path.join(output,'failure-'+index+'.png'),fullPage:true}).catch(()=>{});
      }
      record.duration_ms = Date.now()-started;
      record.external_origins = [...new Set(external)];
      record.trace = 'trace-'+index+'.zip';
      await context.tracing.stop({path:path.join(output,record.trace)});
      await context.close();
      report.cases.push(record);
      console.log((record.passed?'PASS ':'FAIL ')+test.name);
      await fs.writeFile(path.join(output,'browser-checkpoint.json'),JSON.stringify(report,null,2));
    }
    report.tests = cases.length;
    report.complete = report.failures === 0;
    await fs.writeFile(path.join(output,'browser-report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify({tests:report.tests,failures:report.failures,complete:report.complete,proof:output}));
    if (!report.complete) process.exitCode = 1;
  } finally {await browser.close();}
}
main().catch(async error => {console.error(error);process.exitCode=1;if(browser) await browser.close();});
