const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("hookscope/templates/_rule_import.html", "utf8")
  .replace("<script>", "").replace("</script>", "");
function setup() {
  let submit, resolve, reject;
  const calls = [], redirects = [];
  const input = {value: '{"rules":[{"name":"<new>","target_url":"http://localhost"}]}',
    disabled: false};
  const button = {disabled: false}, status = {textContent: ""};
  const elements = {"rule-import": {addEventListener: (_, callback) => {submit = callback;}},
    "rule-import-json": input, "rule-import-submit": button, "rule-import-status": status};
  vm.runInNewContext(source, {document: {getElementById: id => elements[id]}, JSON, TextEncoder,
    window: {location: {assign: url => redirects.push(url)}},
    fetch: (url, options) => {
      calls.push({url, ...options});
      return new Promise((done, fail) => {resolve = done; reject = fail;});
    }});
  return {input, button, status, calls, redirects,
    submit: () => submit({preventDefault() {}}),
    respond: code => resolve({ok: code === 201, status: code}), reject: () => reject(Error("offline"))};
}
test("imports exact draft once and navigates only after confirmed success", async () => {
  const s = setup(), draft = s.input.value, pending = s.submit();
  assert.equal(s.input.disabled, true);
  assert.equal(s.button.disabled, true);
  await s.submit();
  assert.equal(s.calls.length, 1);
  assert.equal(s.calls[0].url, "/api/rules/import");
  assert.equal(s.calls[0].method, "POST");
  assert.equal(s.calls[0].credentials, "same-origin");
  assert.equal(s.calls[0].body, draft);
  assert.deepEqual(s.redirects, []);
  s.respond(201); await pending;
  assert.deepEqual(s.redirects, ["/rules"]);
});
for (const code of [401, 409, 422, 500]) {
  test("HTTP " + code + " preserves draft and requires inspection before retry", async () => {
    const s = setup(), draft = s.input.value, pending = s.submit();
    s.respond(code); await pending;
    assert.equal(s.input.value, draft);
    assert.equal(s.input.disabled, false);
    assert.equal(s.button.disabled, false);
    assert.match(s.status.textContent, /Inspect the rule list before retrying/);
    assert.deepEqual(s.redirects, []);
    assert.equal(s.calls.length, 1);
  });
}
test("lost response never retries automatically", async () => {
  const s = setup(), draft = s.input.value, pending = s.submit();
  s.reject(); await pending;
  assert.equal(s.input.value, draft);
  assert.match(s.status.textContent, /not confirmed/);
  assert.equal(s.calls.length, 1);
  assert.deepEqual(s.redirects, []);
});
test("invalid or oversized documents never reach the API", async () => {
  for (const draft of ["<html>", "[]", '{"rules":[]}', " ".repeat(1048577)]) {
    const s = setup(); s.input.value = draft;
    await s.submit();
    assert.equal(s.calls.length, 0);
    assert.match(s.status.textContent, /Invalid document/);
    assert.equal(s.input.value, draft);
  }
});
test("application-managed page without import form is supported", () => {
  vm.runInNewContext(source, {document: {getElementById: () => null}});
});
