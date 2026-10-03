#!/usr/bin/env node
/**
 * Generate frontend TypeScript API types from the backend OpenAPI schema.
 *
 * The backend FastAPI app is the source of truth for API transport types
 * (PROJECT-SPEC §3.1). Generated output is never edited by hand.
 *
 * Usage:
 *   node scripts/generate-api.mjs           # write src/api/generated/schema.ts
 *   node scripts/generate-api.mjs --check    # fail if generated output is stale (CI)
 *
 * The input is frontend/openapi/openapi.json, produced by the backend:
 *   python backend/scripts/export_openapi.py frontend/openapi/openapi.json
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const inputPath = resolve(root, "openapi/openapi.json");
const outputPath = resolve(root, "src/api/generated/schema.ts");
const checkMode = process.argv.includes("--check");

if (!existsSync(inputPath)) {
  console.error(
    `OpenAPI schema not found at ${inputPath}.\n` +
      "Generate it first:\n" +
      "  python backend/scripts/export_openapi.py frontend/openapi/openapi.json",
  );
  process.exit(1);
}

const ast = await openapiTS(new URL(`file://${inputPath.replace(/\\/g, "/")}`));

const header =
  "/**\n" +
  " * AUTO-GENERATED — DO NOT EDIT.\n" +
  " *\n" +
  " * Source: frontend/openapi/openapi.json (exported from the FastAPI app).\n" +
  " * Regenerate with: npm run api:generate\n" +
  " */\n";

const contents = header + astToString(ast);

if (checkMode) {
  const existing = existsSync(outputPath) ? readFileSync(outputPath, "utf8") : "";
  if (existing !== contents) {
    console.error(
      "Generated API types are out of date.\n" +
        "Run `npm run api:generate` and commit the result.",
    );
    process.exit(1);
  }
  console.log("Generated API types are up to date.");
  process.exit(0);
}

mkdirSync(dirname(outputPath), { recursive: true });
writeFileSync(outputPath, contents, "utf8");
console.log(`wrote ${outputPath}`);
