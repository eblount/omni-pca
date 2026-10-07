// Property checks for the editor's encoders: decoding a value and
// encoding it again must give the same value back, for every value, so
// opening and saving a program can never change a field the user did not
// touch. Run with `npm test`.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, ".types.bundle.mjs");
await build({
  entryPoints: [join(here, "..", "src", "types.ts")],
  bundle: true, format: "esm", outfile: out, logLevel: "silent",
});
const t = await import(out);

for (let v = 0; v <= 0xFFFF; v++) {
  assert.equal(t.encodeEventId(t.decodeEventId(v)), v, `event 0x${v.toString(16)}`);
  assert.equal(t.encodeCondition(t.decodeCondition(v)), v, `cond 0x${v.toString(16)}`);
}
for (let par = 0; par <= 0xFF; par++) {
  assert.equal(t.encodeDuration(t.decodeDuration(par)), par, `duration ${par}`);
  const f = t.rawTempToF(par);
  assert.equal(t.rawTempToF(t.fToRawTemp(f)), f, `temperature raw ${par}`);
}

// Layouts seen on a real Omni IIe.
assert.deepEqual(t.decodeEventId(0x0605), { category: "zone", zone: 5, zoneState: 1 });
assert.deepEqual(t.decodeEventId(0x0405), { category: "zone", zone: 5, zoneState: 0 });
assert.deepEqual(t.decodeEventId(0x03F0), { category: "allunits", allOn: true, area: 0 });
assert.deepEqual(t.decodeEventId(0xB000), { category: "security", mode: 3, area: 0, code: 0 });
assert.deepEqual(t.decodeCondition(0x0A4D), { family: "unit", index: 77, active: true });
assert.deepEqual(t.decodeCondition(0x0003), { family: "misc", misc: 3 });
// AND record: family byte in cond, instance byte in the high half of cond2.
assert.deepEqual(t.decodeAndCondition({ prog_type: 8, cond: 0x000A, cond2: 0x4D00 }),
  { family: "unit", index: 77, active: true });
assert.deepEqual(t.decodeAndCondition({ prog_type: 8, cond: 0x0000, cond2: 0x0300 }),
  { family: "misc", misc: 3 });
assert.deepEqual(t.encodeAndCondition({ family: "misc", misc: 3 }), { cond: 0, cond2: 0x0300 });
assert.deepEqual(t.decodeDuration(102), { unit: "min", value: 2 });
assert.deepEqual(t.decodeDuration(90), { unit: "sec", value: 90 });
assert.equal(t.rawTempToF(118), 66);

// A real panel's table, when the (gitignored) fixture is present: every
// single-line program must be fully editable, with nothing shown as raw.
const fixture = join(here, "..", "..", "..", "..", "tests", "fixtures", "omni_iie_programs.json");
if (existsSync(fixture)) {
  const programs = JSON.parse(readFileSync(fixture, "utf8")).programs;
  let checked = 0;
  let blockLines = 0;
  for (const [slot, hex] of Object.entries(programs)) {
    const b = hex.split(" ").map((x) => parseInt(x, 16));
    // Every line of a multi-line program must be editable too.
    if (b[0] === 5) {
      assert.notEqual(t.decodeEventId((b[9] << 8) | b[10]).category, "raw", `slot ${slot}: WHEN event`);
      blockLines++;
      continue;
    }
    if (b[0] === 8 || b[0] === 9) {
      const fields = { prog_type: b[0], cond: (b[1] << 8) | b[2], cond2: (b[3] << 8) | b[4] };
      assert.equal(t.isStructuredAnd(fields), false, `slot ${slot}: structured condition`);
      const word = t.andConditionWord(fields);
      assert.notEqual(t.decodeCondition(word).family, "raw", `slot ${slot}: AND condition`);
      assert.deepEqual(t.encodeAndCondition(t.decodeCondition(word)),
        { cond: fields.cond, cond2: fields.cond2 }, `slot ${slot}: AND round trip`);
      blockLines++;
      continue;
    }
    if (b[0] === 10) {
      assert.ok(t.commandOptionFor(b[5]), `slot ${slot}: THEN command ${b[5]} has no form`);
      if ([0, 1, 101].includes(b[5])) {
        assert.notEqual(t.decodeDuration(b[6]).unit, "raw", `slot ${slot}: duration ${b[6]}`);
      }
      blockLines++;
      continue;
    }
    if (b[0] === 6) { blockLines++; continue; }
    if (b[0] < 1 || b[0] > 3) continue;
    const cond = (b[1] << 8) | b[2];
    const cond2 = (b[3] << 8) | b[4];
    assert.ok(t.commandOptionFor(b[5]), `slot ${slot}: command ${b[5]} has no form`);
    assert.notEqual(t.decodeDuration(b[6]).unit === "raw" && [0, 1, 101].includes(b[5]), true,
      `slot ${slot}: duration ${b[6]}`);
    assert.notEqual(t.decodeCondition(cond).family, "raw", `slot ${slot}: cond`);
    assert.notEqual(t.decodeCondition(cond2).family, "raw", `slot ${slot}: cond2`);
    if (b[0] === 2) {
      assert.notEqual(t.decodeEventId((b[9] << 8) | b[10]).category, "raw", `slot ${slot}: event`);
    }
    checked++;
  }
  console.log(`real table: ${checked} single-line programs fully editable`);
  console.log(`real table: ${blockLines} lines of multi-line programs fully editable`);
} else {
  console.log("real table: fixture not present, skipped");
}
console.log("codec checks passed");
