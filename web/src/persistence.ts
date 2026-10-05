/** Serialize atomic cache updates against the last successfully committed state. */
export function createSnapshotWriter(commit: (entries: [string, unknown][]) => Promise<void>) {
  let committed: Record<string, unknown> | undefined;
  let pending: Promise<void> = Promise.resolve();
  return (snapshot: Record<string, unknown>): Promise<void> => {
    const next = pending.then(async () => {
      const changes = Object.entries(snapshot).filter(([key, value]) => !committed || committed[key] !== value);
      if (changes.length) await commit(changes);
      committed = snapshot;
    });
    pending = next.catch(() => undefined);
    return next;
  };
}
