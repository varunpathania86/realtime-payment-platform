import assert from "node:assert/strict";
import test from "node:test";
import { allowedStatusURL } from "../src/telemetry.js";

test("browser instrumentation permits only bounded same-origin status URLs", () => {
  const origin = "http://localhost:8080";
  assert.equal(allowedStatusURL(`${origin}/api/healthz`, origin), true);
  for (const url of [
    "https://external.test/api/healthz", "/api/payments/private",
    "/api/healthz?token=private", "/api/healthz#private", "/telemetry/v1/traces",
  ]) {
    assert.equal(allowedStatusURL(url, origin), false, url);
  }
});
