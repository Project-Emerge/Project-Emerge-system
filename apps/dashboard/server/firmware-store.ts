import { mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";

const VERSION_PATTERN = /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/;

export type FirmwareManifest = {
  version: string;
  url: string;
  size: number;
};

export class FirmwareStore {
  private readonly manifestPath: string;

  constructor(private readonly directory: string) {
    this.manifestPath = join(directory, "latest.json");
  }

  async latest(): Promise<FirmwareManifest | null> {
    try {
      const manifest = JSON.parse(await readFile(this.manifestPath, "utf8")) as FirmwareManifest;
      if (!isFirmwareManifest(manifest)) return null;
      const fileStats = await stat(this.filePath(manifest.version));
      return fileStats.isFile() && fileStats.size === manifest.size ? manifest : null;
    } catch {
      return null;
    }
  }

  async save(version: string, image: Buffer): Promise<FirmwareManifest> {
    if (!VERSION_PATTERN.test(version)) {
      throw new Error("Firmware version must contain only letters, numbers, dots, plus signs, and hyphens.");
    }
    if (image.length === 0) throw new Error("Firmware image is empty.");

    await mkdir(this.directory, { recursive: true });
    const manifest: FirmwareManifest = { version, url: `/firmware/dropbot-${version}.bin`, size: image.length };
    const imagePath = this.filePath(version);
    const suffix = `${process.pid}-${crypto.randomUUID()}`;

    // Publish the binary before the manifest, so the current manifest never points at a
    // partial upload if the process stops during the write.
    await writeFile(`${imagePath}.${suffix}`, image);
    await rename(`${imagePath}.${suffix}`, imagePath);
    await writeFile(`${this.manifestPath}.${suffix}`, JSON.stringify(manifest));
    await rename(`${this.manifestPath}.${suffix}`, this.manifestPath);
    return manifest;
  }

  private filePath(version: string): string {
    return join(this.directory, `dropbot-${version}.bin`);
  }
}

function isFirmwareManifest(value: unknown): value is FirmwareManifest {
  return typeof value === "object" && value !== null
    && "version" in value && typeof value.version === "string" && VERSION_PATTERN.test(value.version)
    && "url" in value && value.url === `/firmware/dropbot-${value.version}.bin`
    && "size" in value && typeof value.size === "number" && Number.isSafeInteger(value.size) && value.size > 0;
}
