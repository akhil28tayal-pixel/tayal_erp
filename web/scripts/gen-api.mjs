// Generates src/api/schema.d.ts from the backend's OpenAPI spec.
// X-Company-Id is removed from per-call params because the API client middleware adds it to every request.
import { readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const spec = JSON.parse(readFileSync(new URL("../../backend/openapi.json", import.meta.url)));
for (const ops of Object.values(spec.paths)) {
  for (const op of Object.values(ops)) {
    if (op && Array.isArray(op.parameters)) op.parameters = op.parameters.filter((p) => p.name !== "X-Company-Id");
  }
}
const tmp = new URL("../.openapi.tmp.json", import.meta.url);
writeFileSync(tmp, JSON.stringify(spec));
execFileSync("npx", ["openapi-typescript", tmp.pathname, "-o", "src/api/schema.d.ts"], { stdio: "inherit" });
