import { readFile, stat } from 'node:fs/promises'
import { resolve } from 'node:path'

const manifest = JSON.parse(await readFile(resolve('dist/.vite/manifest.json'), 'utf8'))
const entry = Object.values(manifest).find((chunk) => chunk.isEntry)
if (!entry) throw new Error('Vite manifest 中没有入口 chunk。')

const files = new Set()
function collect(chunk) {
  if (chunk.file.endsWith('.js')) files.add(chunk.file)
  for (const key of chunk.imports ?? []) {
    const imported = manifest[key]
    if (imported && !files.has(imported.file)) collect(imported)
  }
}
collect(entry)

let size = 0
for (const file of files) size += (await stat(resolve('dist', file))).size
const limit = 500 * 1024
if (size >= limit) throw new Error(`初始 JavaScript 必须小于 500 KiB，当前为 ${(size / 1024).toFixed(1)} KiB。`)
console.log(`Initial JavaScript: ${(size / 1024).toFixed(1)} KiB (${[...files].join(', ')})`)
