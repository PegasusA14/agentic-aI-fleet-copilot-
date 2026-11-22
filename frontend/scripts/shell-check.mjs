// A real-browser check of the app shell, which jsdom cannot do: in fixture mode, ask a question, open a proposal, approve it,
// expand Details and scroll the thread to the bottom, then assert the document is exactly one window tall and the composer
// sits on the chat column's bottom edge. Run it with the fixture dev server up:
//   VITE_FLEET_DATA=fixtures npx vite --port 5181 --strictPort
//   node scripts/shell-check.mjs [http://127.0.0.1:5181/]
// It drives headless Chrome over the DevTools protocol (Node 22 global WebSocket, no packages) in its own temporary profile.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const BASE = process.argv[2] || "http://127.0.0.1:5181/";
const PORT = Number(process.env.CDP_PORT || 9341);
const SIZES = [
  [1440, 900],
  [390, 844],
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function run(W, H) {
  const profile = mkdtempSync(join(tmpdir(), "fleet-shell-"));
  const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, "--no-first-run", `--user-data-dir=${profile}`, `--window-size=${W},${H}`, "about:blank"], { stdio: "ignore" });
  try {
    let ws;
    for (let i = 0; i < 40 && !ws; i++) {
      try {
        const page = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page");
        if (page) ws = new WebSocket(page.webSocketDebuggerUrl);
      } catch {
        /* Chrome is still starting */
      }
      if (!ws) await sleep(250);
    }
    await new Promise((r) => ws.addEventListener("open", r));
    let id = 0;
    const pending = new Map();
    ws.addEventListener("message", (m) => {
      const d = JSON.parse(m.data);
      if (d.id && pending.has(d.id)) pending.get(d.id)(d), pending.delete(d.id);
    });
    const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
    const ev = async (expression) => (await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result?.result?.value;
    const click = (text) => ev(`(() => { const b = [...document.querySelectorAll('button, a')].find((e) => (e.getAttribute('aria-label') || e.textContent).includes(${JSON.stringify(text)})); if (!b) return 'missing:' + ${JSON.stringify(text)}; b.click(); return 'ok'; })()`);
    const ask = (q) => ev(`(() => { const t = document.querySelector('textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(t, ${JSON.stringify(q)}); t.dispatchEvent(new Event('input', { bubbles: true })); document.querySelector('button[aria-label="Send question"]').click(); return 'ok'; })()`);
    const bottom = () => ev("(() => { const m = document.querySelector('.stage__main'); m.scrollTop = m.scrollHeight; return 'ok'; })()");
    const measure = async () => JSON.parse(await ev(`(() => {
      const s = document.scrollingElement, st = document.querySelector('.stage'), c = document.querySelector('.stage__composer');
      const bottomOfColumn = st.getBoundingClientRect().bottom - parseFloat(getComputedStyle(st).paddingBottom);
      return JSON.stringify({ scrollHeight: s.scrollHeight, innerHeight: innerHeight, gap: bottomOfColumn - c.getBoundingClientRect().bottom });
    })()`));

    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: W < 700 });
    await send("Page.navigate", { url: BASE });
    await sleep(1500);
    const steps = [];
    const note = async (label) => { await sleep(400); steps.push({ label, ...(await measure()) }); };
    await click("Globex");
    await sleep(800);
    await ask("Which devices are low on disk space?");
    await sleep(3400);
    await click("Order a storage upgrade (1 TiB");
    await sleep(2600);
    await bottom();
    await note("proposal pending");
    await click("Approve");
    await sleep(600);
    await click("Approve and execute");
    await sleep(1800);
    await bottom();
    await note("approved");
    await ev("document.querySelectorAll('.details__toggle').forEach((b) => b.click())");
    await sleep(900);
    await bottom();
    await note("details open");
    ws.close();
    return steps;
  } finally {
    chrome.kill();
    await sleep(800);
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
  }
}

let failed = false;
for (const [W, H] of SIZES) {
  for (const step of await run(W, H)) {
    const ok = step.scrollHeight === step.innerHeight && Math.abs(step.gap) <= 1;
    failed ||= !ok;
    console.log(`${ok ? "ok  " : "FAIL"} ${W}x${H} ${step.label}: scrollHeight ${step.scrollHeight}, innerHeight ${step.innerHeight}, composer ${step.gap.toFixed(1)}px from the column's bottom edge`);
  }
}
process.exit(failed ? 1 : 0);
