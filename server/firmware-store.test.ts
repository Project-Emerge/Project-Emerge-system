import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { FirmwareStore } from "./firmware-store.js";

const directories: string[] = [];

afterEach(async () => {
  await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe("FirmwareStore", () => {
  it("publishes a complete versioned image through the latest manifest", async () => {
    const directory = await mkdtemp(join(tmpdir(), "emerge-firmware-"));
    directories.push(directory);
    const store = new FirmwareStore(directory);

    await expect(store.latest()).resolves.toBeNull();
    await expect(store.save("0.3.1", Buffer.from("firmware"))).resolves.toEqual({
      version: "0.3.1",
      url: "/firmware/dropbot-0.3.1.bin",
      size: 8,
    });
    await expect(store.latest()).resolves.toEqual({
      version: "0.3.1",
      url: "/firmware/dropbot-0.3.1.bin",
      size: 8,
    });
  });

  it("rejects unsafe versions and empty uploads", async () => {
    const directory = await mkdtemp(join(tmpdir(), "emerge-firmware-"));
    directories.push(directory);
    const store = new FirmwareStore(directory);

    await expect(store.save("../../unsafe", Buffer.from("firmware"))).rejects.toThrow("Firmware version");
    await expect(store.save("0.3.1", Buffer.alloc(0))).rejects.toThrow("empty");
  });
});
