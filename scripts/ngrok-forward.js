// Start an ngrok tunnel for the Minis bridge with a STABLE free dev domain.
//
//   NGROK_AUTHTOKEN=... node ngrok-forward.js [port]
//
// ngrok's free plan assigns one permanent development domain per account, so
// the public URL survives every restart - which is exactly what a
// pre-registered claude.ai connector needs.
const ngrok = require("@ngrok/ngrok");

const port = parseInt(process.argv[2] || process.env.ORIGIN_PORT || "8901", 10);
const domain = process.env.NGROK_DOMAIN || undefined;

(async () => {
  try {
    const listener = await ngrok.forward({
      addr: port,
      authtoken_from_env: true,
      ...(domain ? { domain } : {}),
      // the connector needs the raw JSON-RPC body untouched
      compression: false,
    });
    console.log("NGROK_URL=" + listener.url());
    console.log("forwarding 127.0.0.1:" + port + " -> " + listener.url());
    // Keep the Node event loop alive even when stdin is /dev/null
    // (process.stdin.resume() alone exits on EOF).
    setInterval(() => {}, 60 * 60 * 1000);
    const bye = async () => { try { await ngrok.disconnect(); } catch (e) {} process.exit(0); };
    process.on("uncaughtException", (e) => console.error("uncaught:", e && e.message));
    process.on("SIGINT", bye);
    process.on("SIGTERM", bye);
  } catch (e) {
    console.error("ngrok error:", e && e.message ? e.message : e);
    process.exit(1);
  }
})();
