import { expect, test } from "vitest";
import { APP_NAME } from "./index";

test("APP_NAME identifies the web app", () => {
  expect(APP_NAME).toBe("sovra-web");
});
