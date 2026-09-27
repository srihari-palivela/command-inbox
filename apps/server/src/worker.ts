/** Standalone worker process (production): triage, execution, sends, schedulers. */
import { closeDb } from './db/client.js';
import { logger } from './platform/logger.js';
import { hub } from './platform/sse.js';
import { createWorker, startScheduler } from './worker-registry.js';

async function main() {
  await hub.start();
  const worker = createWorker();
  worker.start();
  hub.onJob(() => worker.poke());
  const timer = startScheduler();
  logger.info('command inbox worker running');
  const shutdown = async () => {
    worker.stop();
    clearInterval(timer);
    await hub.stop();
    await closeDb();
    process.exit(0);
  };
  process.on('SIGTERM', () => void shutdown());
  process.on('SIGINT', () => void shutdown());
}

main().catch((err) => {
  logger.fatal({ err }, 'worker failed to start');
  process.exit(1);
});
