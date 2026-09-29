const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const test = require("node:test");

const validator = path.resolve("node_modules/.bin/html-validate");

for (const [fixture, expectedStatus, expectedRule] of [
  ["valid.html", 0, null],
  ["missing-label.html", 1, "input-missing-label"],
  ["missing-alt.html", 1, "wcag/h37"],
]) {
  test(`html-validate ${fixture}`, () => {
    const result = spawnSync(validator, [path.join("tests/a11y", fixture)], {
      encoding: "utf8",
    });

    assert.equal(result.status, expectedStatus, result.stderr || result.stdout);
    if (expectedRule) {
      assert.match(result.stdout, new RegExp(expectedRule.replace("/", "\\/")));
    }
  });
}
