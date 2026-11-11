// Test the existing JSON login contract without browser, server or credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, process.argv[2] || '../static/js/local-login.js'), 'utf8');

async function test(status) {
  const button = {disabled: false};
  const result = {textContent: ''};
  const form = {action: 'https://dig.hqwi.re/login',
    querySelector: () => button,
    addEventListener: (_, fn) => { form.submit = fn; }};
  const destinations = [];
  let request;
  vm.runInNewContext(source, {
    document: {getElementById: id => id === 'local-login' ? form : result},
    FormData: class { get(name) { return {email: 'test@example.invalid', password: 'fake-test-only',
      csrf_token: 'fake-csrf'}[name]; } },
    fetch: async (url, options) => {
      request = {url, options};
      if (status === 'network') throw Error('private detail must not be shown');
      return {ok: status === 200, status};
    },
    window: {location: {assign: target => destinations.push(target)}}
  });
  let prevented = false;
  await form.submit({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(request.url, form.action);
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.headers['X-CSRF-Token'], 'fake-csrf');
  assert.deepEqual(JSON.parse(request.options.body), {email: 'test@example.invalid', password: 'fake-test-only'});
  assert.equal(button.disabled, false);
  if (status === 200) assert.deepEqual(destinations, [process.argv[3] || '/submissions']);
  else {
    assert.equal(destinations.length, 0);
    assert.match(result.textContent, status === 400 ? /expirée/ : status === 'network' ? /indisponible/ : /refusée/);
    assert.doesNotMatch(result.textContent, /private detail|fake-test-only/);
  }
}

(async () => {
  for (const status of [200, 401, 403, 400, 'network']) await test(status);
  console.log('OK: local login JSON, CSRF, redirect and failures');
})().catch(error => { console.error(error); process.exitCode = 1; });
