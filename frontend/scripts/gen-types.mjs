// Generates src/types/*.ts from the backend's JSON Schema contract (../contracts).
import { compileFromFile } from "json-schema-to-typescript";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "src", "types");
await mkdir(out, { recursive: true });

for (const name of ["AnswerEnvelope", "TraceEvent"]) {
  const ts = await compileFromFile(join(root, "..", "contracts", `${name}.schema.json`), {
    bannerComment: "/* Generated from contracts/*.schema.json by scripts/gen-types.mjs. Do not edit. */",
    additionalProperties: false,
    strictIndexSignatures: false,
    unreachableDefinitions: false,
  });
  await writeFile(join(out, `${name}.ts`), ts);
  console.log(`wrote src/types/${name}.ts`);
}
