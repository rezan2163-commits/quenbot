/**
 * QuenBot Dashboard — çökmeye dayanıklı başlatıcı.
 *
 * Amaç: Next.js rewrites proxy'si API ile konuşurken socket hang up
 * (ECONNRESET / ECONNREFUSED) veya timeout yaşarsa bu hatalar
 * unhandledRejection olarak dashboard sürecini düşürmesin.
 *
 * PM2 bu dosyayı node ile çalıştırır; biz de içeriden next start'ı
 * require ederek süreci ayakta tutarız.
 */

const path = require("path");

process.on("uncaughtException", (err) => {
  // Proxy / fetch hataları genelde buraya düşer. Loglayıp devam et.
  const msg = err && err.stack ? err.stack : String(err);
  console.error(`[dashboard][uncaughtException] ${msg.split("\n")[0]}`);
});

process.on("unhandledRejection", (reason) => {
  const msg = reason && reason.stack ? reason.stack : String(reason);
  console.error(`[dashboard][unhandledRejection] ${msg.split("\n")[0]}`);
});

// Next CLI'yi programatik çalıştır: process.argv'a start + port + hostname koy.
const port = process.env.PORT || "5173";
const hostname = process.env.HOSTNAME || "0.0.0.0";
process.argv = [
  process.argv[0],
  path.join(__dirname, "node_modules", "next", "dist", "bin", "next"),
  "start",
  "--port",
  String(port),
  "--hostname",
  String(hostname),
];

try {
  require(path.join(__dirname, "node_modules", "next", "dist", "bin", "next"));
} catch (err) {
  console.error("[dashboard] next start başlatılamadı:", err);
  process.exit(1);
}
