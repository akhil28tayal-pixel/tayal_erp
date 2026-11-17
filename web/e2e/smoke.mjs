// Browser smoke test against a running stack (API + `next start`).
//   E2E_BASE=http://localhost:3000 E2E_PHONE=... E2E_PASSWORD=... node e2e/smoke.mjs
// Covers: sign in, session restore after reload, dealer credit + customer rate, godown ERP switch,
// product conversion, adding a vehicle, sign out. Uses whatever data the target database holds.
// PLAYWRIGHT_PATH lets you point at a globally installed Playwright; otherwise `npm i -D playwright`.
const pw = await import(process.env.PLAYWRIGHT_PATH ?? "playwright");
const chromium = pw.chromium ?? pw.default.chromium;
import assert from "node:assert/strict";

const BASE = process.env.E2E_BASE ?? "http://localhost:3000";
const SHOTS = process.env.E2E_SHOTS ?? "";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1360, height: 860 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const shot = async (name) => SHOTS && page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
const step = (s) => console.log("✓", s);


try {
await page.goto(`${BASE}/overview`);
await page.waitForURL("**/login");
await shot("01-login");
await page.getByLabel("Phone number").fill(process.env.E2E_PHONE);
await page.getByLabel("Password").fill(process.env.E2E_PASSWORD);
await page.getByRole("button", { name: "Sign in" }).click();
await page.waitForURL("**/overview");
await page.getByText("Customers from Tally").waitFor();
await shot("02-overview");
step("signed in, overview shows setup status");

await page.reload();
await page.getByText("Customers from Tally").waitFor();
step("session restored from HttpOnly cookie after reload");

await page.getByRole("link", { name: "Dealers" }).click();
await page.getByRole("link", { name: "RBM (Bagdola)" }).click();
await page.getByLabel("Credit limit (₹)").fill("750000");
await page.getByRole("button", { name: "Save credit settings" }).click();
await page.getByText("Credit settings saved").waitFor();
await page.getByRole("button", { name: "Set rate" }).click();
const drawer = page.getByRole("dialog", { name: "Set customer rate" });
await drawer.getByLabel("Product").selectOption({ label: "Ambuja PPC" });
await drawer.getByLabel("Rate (₹)").fill("392");
await drawer.getByLabel("Tolerance %").fill("1");
await drawer.getByRole("button", { name: "Save rate" }).click();
await page.getByText("Rate saved").waitFor();
await page.getByRole("cell", { name: "₹392 / bag" }).waitFor();
await shot("03-dealer");
step("dealer credit limit and customer rate saved");

await page.getByRole("link", { name: "Godowns" }).click();
const sw = page.getByRole("switch", { name: "Use Old Gurgaon Godown in ERP" });
const before = await sw.getAttribute("aria-checked");
await sw.click();
await page.getByText(/Old Gurgaon Godown switched (on|off)/).waitFor();
await page.getByRole("switch", { name: "Use Old Gurgaon Godown in ERP", checked: before !== "true" }).waitFor();
if (before === "false") {  // leave it switched off for the screenshot
  await sw.click();
  await page.getByRole("switch", { name: "Use Old Gurgaon Godown in ERP", checked: false }).waitFor();
}
await page.getByRole("button", { name: "Switched off" }).click();
await page.getByRole("cell", { name: "Old Gurgaon Godown", exact: true }).waitFor();
assert.equal(await page.getByRole("switch").count(), 1, "only switched-off godowns listed");
await page.getByRole("button", { name: "All" }).click();
await shot("04-godowns");
step("godown switched off in ERP and filtered");

await page.getByRole("link", { name: "Products" }).click();
await page.getByLabel("Product", { exact: true }).selectOption({ label: "Shree Jung Rodhak PPC" });
await page.getByLabel("Quantity").fill("25");
await page.getByLabel("From").selectOption("MT");
await page.getByRole("button", { name: "Convert" }).click();
await page.getByText("500 BAG").waitFor();
step("25 MT Shree ROC = 500 bags");

// Products imported from Tally that need setup: finish one and switch it on.
await Promise.all([
  page.waitForResponse((r) => r.url().includes("needs_setup=true") && r.ok()),
  page.getByRole("button", { name: "Needs setup" }).click(),
]);
await page.waitForTimeout(200);  // let the table re-render with the new list
const row = page.getByRole("row").filter({ has: page.getByText("TMT Bar", { exact: true }) });
if (await row.count()) {
  await row.getByRole("button", { name: "Set up" }).click();
  const d = page.getByRole("dialog", { name: "TMT Bar" });
  await d.getByLabel("Type").selectOption("TMT");
  await d.getByLabel("Brand").selectOption({ label: "Kamdhenu Steel" });
  await d.getByLabel("Diameter (mm)").fill("16");
  await d.getByRole("button", { name: "Save product" }).click();
  await page.getByText("TMT Bar is ready to use").waitFor();
  await page.getByRole("button", { name: "In use" }).click();
  await page.getByRole("switch", { name: "Use TMT Bar in ERP", checked: true }).waitFor();
  step("Tally item 'TMT Bar' set up as 16 mm Kamdhenu TMT and switched on");
}
await page.getByRole("button", { name: "In use" }).click();
await shot("05-products");

await page.getByRole("link", { name: "Vehicles & drivers" }).click();
await page.getByRole("button", { name: "Add vehicle" }).click();
const reg = `DL1LT${Math.floor(1000 + Math.random() * 8999)}`;
await page.getByLabel("Registration number").fill(reg);
await page.getByLabel("Capacity (MT)").fill("16");
await page.getByRole("dialog").getByRole("button", { name: "Add vehicle" }).click();
await page.getByRole("cell", { name: reg }).waitFor();
await shot("06-vehicles");
step(`vehicle ${reg} added`);

await page.getByRole("button", { name: "Sign out" }).click();
await page.waitForURL("**/login");
await page.goto(`${BASE}/dealers`);
await page.waitForURL("**/login");
step("signed out; protected pages redirect to sign in");

} catch (e) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/FAILED.png`, fullPage: true });
  console.error(e.message.split("\n")[0]);
  await browser.close();
  process.exit(1);
}

assert.deepEqual(errors, [], "no uncaught page errors");
await browser.close();
console.log("E2E smoke passed");
