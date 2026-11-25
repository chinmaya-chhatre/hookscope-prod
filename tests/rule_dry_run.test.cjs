const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("hookscope/templates/_rule_dry_run.html", "utf8")
  .replace("<script>", "").replace("</script>", "");
function setup() {
  const select = {value: '<rule "&>', disabled: false};
  const button = {disabled: false}, note = {textContent: ""};
  const output = {textContent: "old request", hidden: false};
  let submit, resolve;
  const calls = [];
  const form = {elements: {rule: select}, dataset: {eventId: "12"},
    querySelector: key => ({"button": button, ".rule-dry-run-note": note,
      ".rule-dry-run-output": output})[key],
    addEventListener: (name, callback) => {submit = callback;}
  };
  vm.runInNewContext(source, {document: {querySelectorAll: () => [form]}, JSON,
    fetch: (url, options) => { calls.push({url, ...options});
      return new Promise(done => {resolve = done;}); }
  });
  return {select, button, note, output, calls,
    submit: () => submit({preventDefault() {}}),
    respond: (ok, data) => resolve({ok, json: async () => data}),
    badResponse: () => resolve({json: async () => {throw Error("not JSON");}})
  };
}
test("dry run only calls preview and renders request as text", async () => {
  const s = setup(), running = s.submit();
  assert.equal(s.calls.length, 1);
  assert.equal(s.calls[0].url, "/api/events/12/rule-preview");
  assert.equal(s.calls[0].method, "POST");
  assert.equal(JSON.parse(s.calls[0].body).rule, '<rule "&>');
  const request = {body_text: "<img onerror=alert(1)>", body_base64: "AP8="};
  s.respond(true, {matches: true, request}); await running;
  assert.deepEqual(JSON.parse(s.output.textContent), request);
  assert.equal(s.output.hidden, false);
  assert.match(s.note.textContent, /Matches.*Nothing was sent or saved/);
  assert.equal(s.button.disabled, false);
});
test("nonmatching request is explicitly hypothetical", async () => {
  const s = setup(), running = s.submit();
  s.respond(true, {matches: false, request: {method: "POST"}}); await running;
  assert.match(s.note.textContent, /Does not match; request shown for inspection only/);
  assert.equal(s.calls.length, 1);
});
test("errors clear old output and restore controls", async () => {
  const s = setup(), running = s.submit();
  assert.equal(s.output.hidden, true);
  assert.equal(s.output.textContent, "");
  s.respond(false, {error: {message: "authentication required"}}); await running;
  assert.equal(s.note.textContent, "authentication required");
  assert.equal(s.output.hidden, true);
  assert.equal(s.select.disabled, false);
});
test("duplicate submission is blocked while rule selection is frozen", async () => {
  const s = setup(), running = s.submit();
  assert.equal(s.select.disabled, true);
  await s.submit();
  assert.equal(s.calls.length, 1);
  s.respond(true, {matches: true, request: {}}); await running;
  assert.equal(s.select.disabled, false);
});
test("unexpected response fails without sending or displaying stale output", async () => {
  const s = setup(), running = s.submit();
  s.badResponse(); await running;
  assert.match(s.note.textContent, /Could not load the preview/);
  assert.equal(s.output.hidden, true);
  assert.equal(s.calls.length, 1);
  assert.equal(s.button.disabled, false);
});
