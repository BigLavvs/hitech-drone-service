/** Generic polling lifecycle. A feature supplies an API-helper callback later. */
export function startPolling(readStatus, onUpdate, { interval = 5000 } = {}) {
  let timerId = null;
  let stopped = false;

  const poll = async () => {
    if (stopped) return;
    try {
      onUpdate(await readStatus());
    } finally {
      if (!stopped) timerId = window.setTimeout(poll, interval);
    }
  };

  poll();
  return () => {
    stopped = true;
    if (timerId) window.clearTimeout(timerId);
  };
}
