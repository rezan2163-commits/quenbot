// PM2 Ecosystem Configuration - QuenBot
// Optimized for compact CPU-only inference on 16 vCPU / 32 GB RAM
// Usage: pm2 start ecosystem.config.js
const quenbotTimeZone = process.env.QUENBOT_TIMEZONE || "Europe/Vienna";

module.exports = {
  apps: [
    {
      name: "quenbot-llama-server",
      cwd: "/root",
      script: "/root/llama.cpp/build/bin/llama-server",
      args: [
        "--host", "127.0.0.1",
        "--port", "8099",
        "--model", "/root/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        "--alias", "qwen2.5-3b",
        "--ctx-size", "4096",
        "--threads", "10",
        "--parallel", "1",
        "--jinja",
        "-ngl", "0",
        "--no-webui",
      ].join(" "),
      interpreter: "none",
      env: {
        TZ: quenbotTimeZone,
      },
      instances: 1,
      autorestart: true,
      watch: false,
      min_uptime: "60s",
      restart_delay: 5000,
      exp_backoff_restart_delay: 500,
      kill_timeout: 10000,
      max_memory_restart: "8G",
      error_file: "./logs/llama-server-error.log",
      out_file: "./logs/llama-server-out.log",
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
    {
      name: "quenbot-api",
      cwd: "./artifacts/api-server",
      script: "npx",
      args: "tsx src/index.ts",
      interpreter: "none",
      env: {
        NODE_ENV: "production",
        TZ: "UTC",
        QUENBOT_TIMEZONE: quenbotTimeZone,
        PORT: 3001,
        DATABASE_URL: process.env.DATABASE_URL || "postgres://user:password@localhost:5432/trade_intel",
        ADMIN_PIN: process.env.ADMIN_PIN || "BABA",
        UV_THREADPOOL_SIZE: "8",
        NODE_OPTIONS: "--max-old-space-size=2048",
        // Hedef kart filtreleri: Python agent tarafıyla aynı eşikler.
        // Gevşetildi — sistem daha çok örnek görsün, sonuçtan öğrensin.
        QUENBOT_TARGET_CARD_MIN_CONF: process.env.QUENBOT_TARGET_CARD_MIN_CONF || "0.52",
        QUENBOT_TARGET_CARD_MIN_QUALITY: process.env.QUENBOT_TARGET_CARD_MIN_QUALITY || "0.55",
        QUENBOT_MAMIS_TARGET_CARD_MIN_CONF: process.env.QUENBOT_MAMIS_TARGET_CARD_MIN_CONF || "0.62",
        QUENBOT_META_LABELER_VETO_PROBA: process.env.QUENBOT_META_LABELER_VETO_PROBA || "0.10",
        QUENBOT_SIGNAL_CARDS_PER_SYMBOL: process.env.QUENBOT_SIGNAL_CARDS_PER_SYMBOL || "1",
      },
      instances: 1,
      autorestart: true,
      watch: false,
      min_uptime: "30s",
      restart_delay: 5000,
      exp_backoff_restart_delay: 200,
      kill_timeout: 15000,
      max_memory_restart: "2G",
      error_file: "./logs/api-error.log",
      out_file: "./logs/api-out.log",
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
    {
      name: "quenbot-agents",
      cwd: "./python_agents",
      script: "python3",
      args: "-O main.py",
      interpreter: "none",
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONOPTIMIZE: "1",
        TZ: quenbotTimeZone,
        QUENBOT_TIMEZONE: quenbotTimeZone,
        DATABASE_URL: process.env.DATABASE_URL || "postgresql://user:password@localhost:5432/trade_intel",
        DB_HOST: "localhost",
        DB_PORT: 5432,
        DB_USER: "user",
        DB_PASSWORD: "password",
        DB_NAME: "trade_intel",
        OLLAMA_NUM_PARALLEL: "1",
        OLLAMA_MAX_LOADED_MODELS: "1",
        // Aktif beyin modeli: Qwen2.5-3B-Instruct Q4_K_M GGUF.
        // Kullanici talebi dogrultusunda buyuk Qwen/Gemma modelleri yerine
        // kompakt bir ana orkestrator kullaniliyor. Ogrenilmis strateji,
        // vector memory, directives ve DB verileri modelden bagimsiz oldugu
        // icin korunur; sadece inference katmani degisir.
        QUENBOT_LLM_MODEL: "qwen2.5-3b-instruct",
        QUENBOT_LLM_NUM_CTX: "8192",
        QUENBOT_LLM_MAX_TOKENS: "512",
        QUENBOT_LLM_NUM_THREAD: "6",
        QUENBOT_CHAT_MODEL: "qwen2.5-3b-instruct",
        QUENBOT_DECISION_MODEL: "qwen2.5-3b-instruct",
        QUENBOT_ACTIVE_MODELS: "qwen2.5-3b-instruct,gemma-2-2b-it",
        // Chat speed tuning — 7B Q4 CPU'da daha hizli, budget daraltilabilir.
        QUENBOT_CHAT_LLM_TIMEOUT: "35",
        QUENBOT_CHAT_FULL_TIMEOUT: "35",
        QUENBOT_CHAT_QUICK_TIMEOUT: "20",
        QUENBOT_CHAT_MAX_TOTAL_LATENCY: "35",
        QUENBOT_CHAT_CONTEXT_CHARS: "1200",
        QUENBOT_CHAT_FULL_MAX_TOKENS: "220",
        QUENBOT_CHAT_QUICK_MAX_TOKENS: "120",
        QUENBOT_GGUF_MODEL_DIR: process.env.QUENBOT_GGUF_MODEL_DIR || "/root/models",
        QUENBOT_GGUF_MODEL_FILE: process.env.QUENBOT_GGUF_MODEL_FILE || "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        // HTTP mode: llama-server standalone binary'yi kullan, Python
        // wrapper'daki Zen4 segfault'u bypass et.
        QUENBOT_LLM_SERVER_URL: process.env.QUENBOT_LLM_SERVER_URL || "http://127.0.0.1:8099",
        QUENBOT_LLM_SERVER_MODEL: process.env.QUENBOT_LLM_SERVER_MODEL || "qwen2.5-3b",
        // Zen4 CPU'da libggml-cpu.so thread race segfault atiyor (hem 0.3.20
        // hem 0.3.2'de). Tek cozum: single-thread inference + OMP kapali.
        // 3B Q4 modelde inference belirgin sekilde daha hafif; eldeki CPU
        // dugumunde daha stabil kalir.
        QUENBOT_GGUF_NUM_THREADS: "1",
        OMP_NUM_THREADS: "1",
        OPENBLAS_NUM_THREADS: "1",
        MKL_NUM_THREADS: "1",
        GGML_CPU_NO_REPACK: "1",
        QUENBOT_GGUF_NUM_CTX: "4096",
        QUENBOT_GGUF_MAX_TOKENS: "512",
        QUENBOT_GGUF_BATCH_SIZE: "256",
        QUENBOT_GGUF_UBATCH_SIZE: "256",
        QUENBOT_ENABLE_REDIS: process.env.QUENBOT_ENABLE_REDIS || "1",
        QUENBOT_REDIS_URL: process.env.QUENBOT_REDIS_URL || "redis://127.0.0.1:6379/0",
        QUENBOT_VECTOR_DB_PATH: process.env.QUENBOT_VECTOR_DB_PATH || "./.chroma",
        QUENBOT_USE_WS_CLIENT_BRIDGE: process.env.QUENBOT_USE_WS_CLIENT_BRIDGE || "0",
        QUENBOT_SCOUT_INGEST_WORKERS: process.env.QUENBOT_SCOUT_INGEST_WORKERS || "8",
        QUENBOT_SCOUT_TRADE_QUEUE_SIZE: process.env.QUENBOT_SCOUT_TRADE_QUEUE_SIZE || "50000",
        QUENBOT_SCOUT_TRADE_BATCH_SIZE: process.env.QUENBOT_SCOUT_TRADE_BATCH_SIZE || "96",
        QUENBOT_BINANCE_WS_MAX_QUEUE: process.env.QUENBOT_BINANCE_WS_MAX_QUEUE || "40000",
        QUENBOT_BYBIT_WS_MAX_QUEUE: process.env.QUENBOT_BYBIT_WS_MAX_QUEUE || "40000",
        QUENBOT_DISABLE_DRAWDOWN_GATE: process.env.QUENBOT_DISABLE_DRAWDOWN_GATE || "1",
        QUENBOT_ENABLE_CHAT_POLLER: "1",
        QUENBOT_CONTROL_TOKEN: process.env.QUENBOT_CONTROL_TOKEN || "",
        // Phase 3+ intel modules — enable Fast Brain, Decision Router,
        // Online Learning ve Prometheus metrics exporter (dashboard
        // IntelPanel "8/8 modul aktif" gostergesi icin).
        QUENBOT_FAST_BRAIN_ENABLED: process.env.QUENBOT_FAST_BRAIN_ENABLED || "1",
        // Canli path'te OFI/MH warming-up olsa bile Fast Brain'in
        // confluence-tabanli shadow prediction uretmesine izin ver.
        QUENBOT_FAST_BRAIN_MIN_FEATURES: process.env.QUENBOT_FAST_BRAIN_MIN_FEATURES || "2",
        QUENBOT_FAST_BRAIN_ALLOW_CONFLUENCE_FALLBACK:
          process.env.QUENBOT_FAST_BRAIN_ALLOW_CONFLUENCE_FALLBACK || "1",
        QUENBOT_DECISION_ROUTER_ENABLED: process.env.QUENBOT_DECISION_ROUTER_ENABLED || "1",
        QUENBOT_ONLINE_LEARNING_ENABLED: process.env.QUENBOT_ONLINE_LEARNING_ENABLED || "1",
        QUENBOT_METRICS_ENABLED: process.env.QUENBOT_METRICS_ENABLED || "1",
        // Phase 5 Finalization — Safety Net observer (accuracy + drift izleyici).
        // Yalnızca gözlem yapar; strateji yolunu etkilemez. Baseline yoksa
        // 24 saatlik pasif kalibrasyon sonrası metrik üretmeye başlar.
        QUENBOT_SAFETY_NET_ENABLED: process.env.QUENBOT_SAFETY_NET_ENABLED || "1",
        // Cross-asset graph — eşikleri 29 sembollü canlı pencerede kenar
        // üretecek şekilde gevşetildi. Yalnızca alert/cooldown etkiler,
        // strateji yolunu değiştirmez.
        QUENBOT_CROSS_ASSET_MIN_SAMPLES: process.env.QUENBOT_CROSS_ASSET_MIN_SAMPLES || "30",
        QUENBOT_CROSS_ASSET_MIN_EDGE: process.env.QUENBOT_CROSS_ASSET_MIN_EDGE || "0.05",
        QUENBOT_CROSS_ASSET_REBUILD_MIN: process.env.QUENBOT_CROSS_ASSET_REBUILD_MIN || "10",
        // Oracle §1–§8 dedektörleri + §10 Factor Graph Fusion.
        // Tümü read-only observer; oracle_signal_bus üzerinden kanal
        // kaydı yapar, strateji/risk yoluna doğrudan dokunmaz. Aşamalı
        // rollout yerine artık kalıcı olarak aktif (misyon gereği).
        // §8 Onchain: API anahtarı yoksa client otomatik disabled kalır.
        QUENBOT_BOCPD_ENABLED: process.env.QUENBOT_BOCPD_ENABLED || "1",
        QUENBOT_HAWKES_ENABLED: process.env.QUENBOT_HAWKES_ENABLED || "1",
        QUENBOT_LOB_THERMO_ENABLED: process.env.QUENBOT_LOB_THERMO_ENABLED || "1",
        QUENBOT_WASSERSTEIN_ENABLED: process.env.QUENBOT_WASSERSTEIN_ENABLED || "1",
        QUENBOT_PATH_SIGNATURE_ENABLED: process.env.QUENBOT_PATH_SIGNATURE_ENABLED || "1",
        QUENBOT_MIRROR_FLOW_ENABLED: process.env.QUENBOT_MIRROR_FLOW_ENABLED || "1",
        QUENBOT_TDA_ENABLED: process.env.QUENBOT_TDA_ENABLED || "1",
        QUENBOT_ONCHAIN_ENABLED: process.env.QUENBOT_ONCHAIN_ENABLED || "1",
        QUENBOT_FACTOR_GRAPH_ENABLED: process.env.QUENBOT_FACTOR_GRAPH_ENABLED || "1",
        // §11 Oracle Brain — Low-Dose Active Mode (Aşama 1).
        // Shadow kapalı; direktifler Directive Gatekeeper + Auto-Rollback
        // güvenlik ağının arkasında uygulanıyor. Hard blocklist kalıcı
        // (OVERRIDE_VETO, FORCE_TRADE, DISABLE_SAFETY_NET).
        QUENBOT_ORACLE_BRAIN_ENABLED: process.env.QUENBOT_ORACLE_BRAIN_ENABLED || "1",
        QUENBOT_ORACLE_BRAIN_SHADOW: process.env.QUENBOT_ORACLE_BRAIN_SHADOW || "0",
        // Aşama 1 low-dose tuning — confidence min 0.80, 3/saat, dar allowlist.
        QUENBOT_DIRECTIVE_GATEKEEPER_ENABLED: process.env.QUENBOT_DIRECTIVE_GATEKEEPER_ENABLED || "1",
        QUENBOT_ORACLE_BRAIN_DIRECTIVE_CONFIDENCE_MIN:
          process.env.QUENBOT_ORACLE_BRAIN_DIRECTIVE_CONFIDENCE_MIN || "0.80",
        QUENBOT_ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR:
          process.env.QUENBOT_ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR || "3",
        QUENBOT_ORACLE_BRAIN_DIRECTIVE_ALLOWLIST:
          process.env.QUENBOT_ORACLE_BRAIN_DIRECTIVE_ALLOWLIST ||
          "ADJUST_CONFIDENCE_THRESHOLD,ADJUST_POSITION_SIZE_MULT,PAUSE_SYMBOL",
        // Auto-rollback explicit: rejection rate / accuracy / cascade / impact
        // regression tetikleyicileri aktif. Trip olursa ORACLE_BRAIN_SHADOW
        // otomatik True'ya döner ve forensic bundle yazılır.
        QUENBOT_AUTO_ROLLBACK_ENABLED: process.env.QUENBOT_AUTO_ROLLBACK_ENABLED || "1",
        QUENBOT_AUTO_ROLLBACK_CASCADE_DETECTION:
          process.env.QUENBOT_AUTO_ROLLBACK_CASCADE_DETECTION || "1",
        // Gatekeeper gevşemesi: yön (long/short) analizlerinde çok katı
        // confidence/quality filtresi sistemi öğrenemez hale getiriyordu.
        // Eşikleri düşürüp sampling artırıyoruz; TEK-COIN-TEK-KART kuralı
        // üç ayrı katmanda (insert_signal horizon-aware lockout,
        // ghost_simulator filtered_duplicate, /api/signals bySymbol map)
        // korunmaya devam ediyor, sadece havuz büyüyor.
        QUENBOT_TARGET_CARD_MIN_CONF: process.env.QUENBOT_TARGET_CARD_MIN_CONF || "0.52",
        QUENBOT_TARGET_CARD_MIN_QUALITY: process.env.QUENBOT_TARGET_CARD_MIN_QUALITY || "0.55",
        QUENBOT_MAMIS_TARGET_CARD_MIN_CONF: process.env.QUENBOT_MAMIS_TARGET_CARD_MIN_CONF || "0.62",
        QUENBOT_MAMIS_TARGET_CARD_MIN_VOLATILITY: process.env.QUENBOT_MAMIS_TARGET_CARD_MIN_VOLATILITY || "0.0030",
        // Meta-labeler veto eşiği: çok düşük olasılıkta bile sadece açıkça
        // reddedilenleri dışla (0.10 → daha geniş örnek).
        QUENBOT_META_LABELER_VETO_PROBA: process.env.QUENBOT_META_LABELER_VETO_PROBA || "0.10",
      },
      instances: 1,
      autorestart: true,
      watch: false,
      min_uptime: "120s",
      restart_delay: 10000,
      exp_backoff_restart_delay: 500,
      kill_timeout: 30000,
      max_memory_restart: "26G",
      error_file: "./logs/agents-error.log",
      out_file: "./logs/agents-out.log",
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
    {
      name: "quenbot-dashboard",
      cwd: "./dashboard",
      script: "./server.js",
      node_args: "--unhandled-rejections=warn --max-old-space-size=768",
      env: {
        NODE_ENV: "production",
        TZ: quenbotTimeZone,
        QUENBOT_TIMEZONE: quenbotTimeZone,
        NEXT_PUBLIC_QUENBOT_TIMEZONE: quenbotTimeZone,
        PORT: 5173,
        HOSTNAME: "0.0.0.0",
        API_TARGET: "http://127.0.0.1:3001",
        NODE_OPTIONS: "--unhandled-rejections=warn",
      },
      instances: 1,
      autorestart: true,
      watch: false,
      min_uptime: "60s",
      restart_delay: 5000,
      exp_backoff_restart_delay: 500,
      kill_timeout: 15000,
      max_memory_restart: "1G",
      error_file: "./logs/dashboard-error.log",
      out_file: "./logs/dashboard-out.log",
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
    },
  ],
};
