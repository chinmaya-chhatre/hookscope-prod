const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("hookscope/templates/_rule_editor.html", "utf8")
  .replace("<script>", "").replace("</script>", "");
const good = JSON.stringify({name: "a", target_url: "https://target.test"});
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup(edit = null) {
  const listeners = {};
  const nodes = Object.fromEntries(["rule-editor", "rule-json", "rule-status", "rule-save"]
    .map(id => [id, {value: good, disabled: true, textContent: "", dataset: {},
      addEventListener: (name, fn) => { listeners[id + ":" + name] = fn; }}]));
  nodes["rule-editor"].dataset = {mode: edit === null ? "create" : "edit", editing: edit};
  const calls = [], pending = [], timers = [], redirects = [];
  vm.runInNewContext(source, {
    document: {getElementById: id => nodes[id]}, JSON, Error, encodeURIComponent,
    fetch: (url, options) => { calls.push({url, ...options});
      return new Promise(resolve => pending.push(resolve)); },
    setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout: () => {},
    window: {location: {assign: url => redirects.push(url)}}
  });
  return {nodes, calls, pending, timers, redirects,
    respond: async (index, ok = true, data = {}) => {
      pending[index]({ok, json: async () => data}); await flush();
    },
    type: text => { nodes["rule-json"].value = text; listeners["rule-json:input"](); },
    submit: () => listeners["rule-editor:submit"]({preventDefault() {}})
  };
}
test("validation never saves and a valid create submits exactly the validated text", async () => {
  const s = setup();
  assert.equal(s.calls[0].url, "/api/rule-validation");
  await s.respond(0);
  assert.equal(s.nodes["rule-save"].disabled, false);
  const submitting = s.submit();
  assert.equal(s.calls[1].method, "POST");
  assert.equal(s.calls[1].url, "/api/rules");
  assert.equal(s.calls[1].body, good);
  await s.respond(1); await submitting;
  assert.deepEqual(s.redirects, ["/rules"]);
});
test("editing encodes the name for validation and replacement", async () => {
  const s = setup("a ?&");
  assert.equal(s.calls[0].url, "/api/rule-validation?edit=a%20%3F%26");
  await s.respond(0);
  const submitting = s.submit();
  assert.equal(s.calls[1].method, "PUT");
  assert.equal(s.calls[1].url, "/api/rules/a%20%3F%26");
  await s.respond(1); await submitting;
});
test("a late validation response cannot enable saving an invalid newer draft", async () => {
  const s = setup();
  s.type("{"); await s.timers.at(-1)();
  await s.respond(0);
  assert.equal(s.nodes["rule-save"].disabled, true);
  await s.submit();
  assert.equal(s.calls.length, 1);
  assert.match(s.nodes["rule-status"].textContent, /Invalid JSON/);
});
test("out of order responses preserve the newest validation error", async () => {
  const s = setup();
  s.type(good + " "); s.timers.at(-1)();
  await s.respond(1, false, {error: {message: "conflict"}});
  await s.respond(0);
  assert.equal(s.nodes["rule-save"].disabled, true);
  assert.equal(s.nodes["rule-status"].textContent, "conflict");
});
test("save conflict preserves draft, displays text safely and prevents repeated submission", async () => {
  const s = setup(); await s.respond(0);
  const submitting = s.submit();
  await s.submit();
  assert.equal(s.calls.length, 2);
  await s.respond(1, false, {error: {message: "<img onerror=alert(1)>"}});
  await submitting;
  assert.equal(s.nodes["rule-json"].value, good);
  assert.equal(s.nodes["rule-status"].textContent, "<img onerror=alert(1)>");
  assert.equal(s.nodes["rule-save"].disabled, true);
  assert.deepEqual(s.redirects, []);
});
test("validation failures and expired authentication keep saving disabled", async () => {
  const s = setup();
  await s.respond(0, false, {error: {message: "authentication required"}});
  assert.equal(s.nodes["rule-save"].disabled, true);
  await s.submit();
  assert.equal(s.calls.length, 1);
});
test("non JSON validation response fails closed", async () => {
  const s = setup();
  s.pending[0]({ok: false, json: async () => {throw Error("HTML login");}});
  await flush();
  assert.equal(s.nodes["rule-save"].disabled, true);
  assert.match(s.nodes["rule-status"].textContent, /unavailable/);
});
test("an uncertain save keeps the draft and instructs inspection before retry", async () => {
  const s = setup(); await s.respond(0);
  const submitting = s.submit();
  s.pending[1]({ok: true, json: async () => {throw Error("lost response");}});
  await submitting;
  assert.equal(s.nodes["rule-json"].value, good);
  assert.match(s.nodes["rule-status"].textContent, /Check the rules list before retrying/);
  assert.equal(s.nodes["rule-save"].disabled, true);
});
