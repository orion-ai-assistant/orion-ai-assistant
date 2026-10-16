// Local lifecycle control for Node applications without console signal support.
const fs = require('node:fs');
const control = process.env.ORION_LIFECYCLE_CONTROL;
// Node fork() inherits this preload. Keep its control path available to the
// application's server children (including the Next.js development server).
if (!control) throw new Error('Lifecycle control path missing');
const timer = setInterval(() => {
  if (!fs.existsSync(control)) return;
  clearInterval(timer);
  // Next.js installs SIGINT handlers to drain its own server/process tree.
  if (!process.emit('SIGINT')) process.exit(0);
}, 100);
timer.unref();
