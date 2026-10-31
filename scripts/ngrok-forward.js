// Start an ngrok tunnel for the Minis bridge with a STABLE free dev domain.
//   NGROK_AUTHTOKEN=... node ngrok-forward.js [port]
// ngrok's free plan assigns one permanent development domain per account, so
// the public URL survives every restart - which is exactly what a
// pre-registered claude.ai connector needs.
//
// v2: supervisor-friendly — never exit(1) on transient failures.
// Retries up to CONNECT_RETRIES (default 30, ~5min) with backoff before
// giving up. Stale "already online" (ERR_NGROK_334 from a zombie double)
// resolves itself once the zombie dies, so retry in-process instead of
// forcing the supervisor into a kill/restart loop.
const ngrok = require("@ngrok/ngrok");

const port = parseInt(process.argv[2] || process.env.ORIGIN_PORT || "8901", 10);
const domain = process.env.NGROK_DOMAIN || undefined;
const MAX_RETRIES = parseInt(process.env.CONNECT_RETRIES || "30", 10);
const RETRY_DELAY_MS = parseInt(process.env.CONNECT_RETRY_DELAY_MS || "10000", 10);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  let listener = null;
  for (let attempt = 1; ; attempt++) {
    try {
      listener = await ngrok.forward({
        addr: port,
        authtoken_from_env: true,
        ...(domain ? { domain } : {}),
        // the connector needs the raw JSON-RPC body untouched
        compression: false,
      });
      console.log("NGROK_URL=" + listener.url());
      console.log("forwarding 127.0.0.1:" + port + " -> " + listener.url());
      break; // connected
    } catch (e) {
      const msg = e && e.message ? e.message : String(e);
      console.error(`ngrok connect attempt ${attempt} failed: ${msg}`);
      if (attempt >= MAX_RETRIES) {
        console.error(`ngrok: giving up after ${attempt} attempts`);
        process.exit(1);
      }
      await sleep(RETRY_DELAY_MS);
    }
  }
  // Keep the Node event loop alive even when stdin is /dev/null
  // (process.stdin.resume() alone exits on EOF).
  setInterval(() => {}, 60 * 60 * 1000);
  const bye = async () => { try { await ngrok.disconnect(); } catch (e) {} process.exit(0); };
  process.on("uncaughtException", (e) => console.error("uncaught:", e && e.message));
  process.on("SIGINT", bye);
  process.on("SIGTERM", bye);
})();
