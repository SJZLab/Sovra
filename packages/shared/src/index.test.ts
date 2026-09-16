import { expect, test } from "vitest";
import { PACKAGE_NAME } from "./index";

test("PACKAGE_NAME identifies the shared package", () => {
  expect(PACKAGE_NAME).toBe("@sovra/shared");
});
