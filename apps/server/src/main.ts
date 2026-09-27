import { env } from './config/env.js';
import { buildApp } from './app.js';
import { closeDb } from './db/client.js';
import { logger } from './platform/logger.js';
import { hub } from './platform/sse.js';
import { createWorker, startScheduler } from './worker-registry.js';

async function main() {
  const app = await buildApp();
  await hub.start();
  let stopWorker = () => {};
  if (env.EMBEDDED_WORKER) {
    const worker = createWorker();
    worker.start();
    hub.onJob(() => worker.poke());
    const timer = startScheduler();
    stopWorker = () => {
      worker.stop();
      clearInterval(timer);
    };
  }
  await app.listen({ port: env.PORT, host: env.HOST });
  logger.info(
    { port: env.PORT, embeddedWorker: env.EMBEDDED_WORKER, demo: env.DEMO_MODE },
    'command inbox api listening',
  );

  const shutdown = async (signal: string) => {
    logger.info({ signal }, 'shutting down');
    stopWorker();
    await app.close();
    await hub.stop();
    await closeDb();
    process.exit(0);
  };
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
  process.on('SIGINT', () => void shutdown('SIGINT'));
}

main().catch((err) => {
  logger.fatal({ err }, 'failed to start');
  process.exit(1);
});
