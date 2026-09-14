// 音频 IndexedDB 持久化：跨窗口共享录音回放（小窗关闭后主页面仍可回放）
// 极简封装，无第三方依赖
const DB_NAME = 'xinhere-audio'
const STORE = 'clips'

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE)
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function withStore<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb()
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE, mode)
      const req = fn(tx.objectStore(STORE))
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
  } finally {
    db.close()
  }
}

export function saveAudio(id: string, blob: Blob): Promise<IDBValidKey> {
  return withStore('readwrite', (s) => s.put(blob, id))
}

export function loadAudio(id: string): Promise<Blob | null> {
  return withStore('readonly', (s) => s.get(id)).then((r) => (r instanceof Blob ? r : null)).catch(() => null)
}

export function deleteAudio(id: string): Promise<void> {
  return withStore('readwrite', (s) => s.delete(id)).then(() => undefined).catch(() => undefined)
}
