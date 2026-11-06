const KEY = "visualstore.lastProfile";

export function getLastProfile(storage: Storage = localStorage): string | null {
  return storage.getItem(KEY);
}

export function setLastProfile(name: string | null, storage: Storage = localStorage): void {
  if (name === null) storage.removeItem(KEY);
  else storage.setItem(KEY, name);
}
