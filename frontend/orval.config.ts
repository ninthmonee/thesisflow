import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { defineConfig } from "orval";

export const frontendRoot = fileURLToPath(new URL(".", import.meta.url));

export function createApiConfig(outputRoot = frontendRoot) {
  const generated = resolve(outputRoot, "src/shared/api/generated");
  return defineConfig({
    api: {
      input: { target: resolve(frontendRoot, "../contracts/openapi.json") },
      output: {
        target: resolve(generated, "client.ts"),
        schemas: resolve(generated, "models"),
        mode: "split",
        client: "react-query",
        httpClient: "fetch",
        packageJson: resolve(frontendRoot, "package.json"),
        tsconfig: resolve(frontendRoot, "tsconfig.app.json"),
        override: {
          mutator: { path: resolve(outputRoot, "src/shared/api/transport.ts"), name: "apiFetch" },
          fetch: { includeHttpResponseReturnType: false },
          query: { version: 5 },
        },
      },
    },
    validation: {
      input: { target: resolve(frontendRoot, "../contracts/openapi.json") },
      output: {
        target: resolve(generated, "validators.ts"),
        mode: "single",
        client: "zod",
        packageJson: resolve(frontendRoot, "package.json"),
        override: { zod: { version: 4 } },
      },
    },
  });
}

export default createApiConfig();
