import { cp, mkdir, mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";
import { generate } from "orval";
import { createApiConfig, frontendRoot } from "../orval.config.ts";

async function tree(directory: string): Promise<Map<string, string>> {
  const files = new Map<string, string>();
  async function visit(current: string) {
    let entries;
    try {
      entries = await readdir(current, { withFileTypes: true });
    } catch (error) {
      if (error instanceof Error && "code" in error && error.code === "ENOENT") return;
      throw error;
    }
    for (const entry of entries) {
      const path = join(current, entry.name);
      if (entry.isDirectory()) await visit(path);
      else if (entry.isFile()) files.set(relative(directory, path), await readFile(path, "utf8"));
      else throw new Error(`Generated directory contains a non-regular file: ${path}`);
    }
  }
  await visit(directory);
  return files;
}

async function main() {
  const checking = process.argv.includes("--check");
  const staging = await mkdtemp(join(tmpdir(), "tradingagents-contract-"));
  try {
    const stagingApi = resolve(staging, "src/shared/api");
    await mkdir(stagingApi, { recursive: true });
    await cp(
      resolve(frontendRoot, "src/shared/api/transport.ts"),
      join(stagingApi, "transport.ts"),
    );
    for (const project of Object.values(createApiConfig(staging))) {
      await generate(project, frontendRoot, { throwOnError: true, failOnWarnings: true });
    }
    const expectedDirectory = join(stagingApi, "generated");
    const actualDirectory = resolve(frontendRoot, "src/shared/api/generated");
    if (checking) {
      const [expected, actual] = await Promise.all([
        tree(expectedDirectory),
        tree(actualDirectory),
      ]);
      const names = new Set([...expected.keys(), ...actual.keys()]);
      const differences = [...names].filter((name) => expected.get(name) !== actual.get(name));
      if (differences.length || expected.size === 0) {
        throw new Error(
          `API generation drift: ${differences.join(", ") || "no generated files"}. Run npm run api:generate.`,
        );
      }
      process.stdout.write(`API generation is current (${expected.size} files).\n`);
    } else {
      await rm(actualDirectory, { recursive: true, force: true });
      await cp(expectedDirectory, actualDirectory, { recursive: true });
      process.stdout.write("API client generated from contracts/openapi.json.\n");
    }
  } finally {
    await rm(staging, { recursive: true, force: true });
  }
}

await main();
