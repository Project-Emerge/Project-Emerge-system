const wt = require('node:worker_threads');
if (!wt.markAsUncloneable) {
  wt.markAsUncloneable = () => {};
}
