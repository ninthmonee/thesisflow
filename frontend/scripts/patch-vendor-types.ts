import { createHash } from "node:crypto";
import { readFile, writeFile, rename } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

interface TypePatch {
  packageName: string;
  version: string;
  paths: readonly string[];
  originalHash: string;
  patchedHash: string;
  from: string;
  to: string;
}

const patches: readonly TypePatch[] = [
  {
    packageName: "@rc-component/image",
    version: "1.10.0",
    paths: ["es/PreviewGroup.d.ts", "lib/PreviewGroup.d.ts"],
    originalHash: "af6bf86b483eee933366555ec38d289524058c5d879c32c13d6bc018f971bd0b",
    patchedHash: "376cc7545b519f27889c4da8a11edf54b097f9e8a0cf636f333fda2aff9d57f4",
    from: "export interface GroupPreviewConfig extends InternalPreviewConfig {",
    to: "export interface GroupPreviewConfig extends Omit<InternalPreviewConfig, 'imageRender'> {",
  },
  {
    packageName: "@rc-component/picker",
    version: "1.12.2",
    paths: ["es/PickerPanel/index.d.ts", "lib/PickerPanel/index.d.ts"],
    originalHash: "d42e0e90b8efe93fd58a73878eb2d6a3dc6cfceac2e8632b065d38f4ef3c95aa",
    patchedHash: "cfc6a56402998584d2a6b2a5ed5f34c0e0b8aa5693a9cecd9042cd53861f15f2",
    from: ">, SharedTimeProps<DateType>, Pick<React.HTMLAttributes<HTMLDivElement>, 'tabIndex'> {",
    to: ">, Omit<SharedTimeProps<DateType>, 'defaultValue'>, Pick<React.HTMLAttributes<HTMLDivElement>, 'tabIndex'> {",
  },
];

const root = fileURLToPath(new URL("../", import.meta.url));
const hash = (content: string) => createHash("sha256").update(content).digest("hex");

async function main() {
  const checking = process.argv.includes("--check");
  const updates: { path: string; content: string }[] = [];
  let count = 0;
  for (const patch of patches) {
    const packageRoot = resolve(root, "node_modules", patch.packageName);
    const metadata: unknown = JSON.parse(
      await readFile(resolve(packageRoot, "package.json"), "utf8"),
    );
    if (
      metadata === null ||
      typeof metadata !== "object" ||
      !("version" in metadata) ||
      metadata.version !== patch.version
    ) {
      throw new Error(
        `Type patch requires exactly ${patch.packageName}@${patch.version}; review the patch before upgrading.`,
      );
    }
    for (const relativePath of patch.paths) {
      const path = resolve(packageRoot, relativePath);
      const content = await readFile(path, "utf8");
      const currentHash = hash(content);
      if (currentHash === patch.patchedHash) {
        if (!content.includes(patch.to) || content.includes(patch.from))
          throw new Error(`Invalid patched declaration: ${path}`);
      } else if (currentHash === patch.originalHash) {
        if (content.split(patch.from).length !== 2)
          throw new Error(`Patch fragment is not unique: ${path}`);
        const updated = content.replace(patch.from, patch.to);
        if (hash(updated) !== patch.patchedHash)
          throw new Error(`Patch result hash does not match: ${path}`);
        updates.push({ path, content: updated });
      } else {
        throw new Error(
          `Unexpected declaration content: ${path}. Refusing to modify an unknown file.`,
        );
      }
      count += 1;
    }
  }
  if (checking && updates.length)
    throw new Error(
      `${updates.length} type patches missing; run npm run vendor:patch after npm ci.`,
    );
  if (!checking) {
    for (const update of updates) {
      const temporary = `${update.path}.tradingagents-tmp`;
      await writeFile(temporary, update.content, "utf8");
      await rename(temporary, update.path);
    }
  }
  process.stdout.write(
    `Vendor declarations verified (${count} files; ${checking ? "check only" : `${updates.length} applied`}).\n`,
  );
}

await main();
