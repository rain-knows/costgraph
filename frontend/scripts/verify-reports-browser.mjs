import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(frontendRoot, '..')
const baseUrl = process.env.REPORTS_BROWSER_BASE_URL ?? 'http://127.0.0.1:5174'
const evidenceDir = path.join(repoRoot, '.pi', 'goals', 'evidence', 'reports-browser')
const artifactListUrl = `${baseUrl}/api/artifacts?state=active&limit=100&offset=0`
const accessToken = (process.env.REPORTS_BROWSER_ACCESS_TOKEN ?? '').trim()
const requestHeaders = accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined
const states = [
  { name: 'desktop-light', width: 1440, height: 1000, theme: 'light', mobile: false },
  { name: 'desktop-dark', width: 1440, height: 1000, theme: 'dark', mobile: false },
  { name: 'mobile-light', width: 390, height: 844, theme: 'light', mobile: true },
  { name: 'mobile-dark', width: 390, height: 844, theme: 'dark', mobile: true },
]

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

async function fetchJson(url) {
  const response = await fetch(url, { headers: requestHeaders })
  assert(response.ok, `HTTP ${response.status} from ${url}`)
  return response.json()
}

async function waitForHttp(url, timeoutMs, existingServer) {
  const deadline = Date.now() + timeoutMs
  let lastError = null
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
      lastError = new Error(`HTTP ${response.status}`)
    } catch (error) {
      lastError = error
    }
    if (existingServer?.exitCode !== null) throw new Error(`Vite exited early: ${existingServer.exitCode}`)
    await delay(400)
  }
  throw new Error(`Timed out waiting for ${url}: ${lastError}`)
}

function findChrome() {
  const candidates = [process.env.CHROME_PATH, 'C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].filter(Boolean)
  const browser = candidates.find((candidate) => existsSync(candidate))
  if (browser) return browser
  throw new Error(`Chrome/Edge not found. Checked: ${candidates.join(', ')}`)
}

function getDevToolsTarget(port) {
  return new Promise((resolve, reject) => {
    const request = fetch(`http://127.0.0.1:${port}/json/list`).then(async (response) => {
      assert(response.ok, `Chrome DevTools endpoint returned HTTP ${response.status}`)
      const targets = await response.json()
      const target = targets.find((entry) => entry.type === 'page')
      assert(target, 'Chrome did not expose a page target')
      resolve(target)
    }).catch(reject)
    void request
  })
}

async function connectCdp(url) {
  const socket = new WebSocket(url)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  let nextId = 0
  const pending = new Map()
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data)
    const waiter = pending.get(message.id)
    if (!waiter) return
    pending.delete(message.id)
    if (message.error) waiter.reject(new Error(message.error.message))
    else waiter.resolve(message.result)
  })
  return {
    socket,
    send(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++nextId
        pending.set(id, { resolve, reject })
        socket.send(JSON.stringify({ id, method, params }))
      })
    },
  }
}

async function evaluate(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text ?? 'Browser evaluation failed')
  if (result.result?.subtype === 'error') throw new Error(result.result.description)
  return result.result?.value
}

async function waitForPageReady(cdp, expectedTheme = null) {
  const deadline = Date.now() + 20_000
  let consecutiveReadyChecks = 0
  let lastState = null
  while (Date.now() < deadline) {
    const result = await evaluate(cdp, `(() => ({ title: document.querySelector('h1')?.innerText ?? '', theme: document.documentElement.dataset.theme ?? '', report: document.querySelector('[aria-label="展示型报表核心指标"]') !== null, chart: [...document.querySelectorAll('canvas')].some((canvas) => canvas.getBoundingClientRect().width > 0) }))()`)
    lastState = result
    if (result.title === '登录以继续') {
      throw new Error('Browser verification requires an authenticated OIDC session or explicit local demo authentication')
    }
    const ready = result.title.includes('展示型报表') && result.report && result.chart && (!expectedTheme || result.theme === expectedTheme)
    consecutiveReadyChecks = ready ? consecutiveReadyChecks + 1 : 0
    if (consecutiveReadyChecks >= 3) return result
    await delay(300)
  }
  throw new Error(`Report page did not stabilize for theme ${expectedTheme ?? '(initial)'} before timeout; last state: ${JSON.stringify(lastState)}`)
}

async function waitForCondition(cdp, expression, label, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs
  let last = null
  while (Date.now() < deadline) {
    last = await evaluate(cdp, expression)
    if (last) return last
    await delay(250)
  }
  throw new Error(`${label} did not become ready; last value: ${JSON.stringify(last)}`)
}

async function verifyCostWorkspace(cdp, artifact) {
  await cdp.send('Page.navigate', { url: `${baseUrl}/cost-data?period=${encodeURIComponent(artifact.period)}` })
  await waitForCondition(cdp, `document.querySelector('h1')?.innerText === '批次成本数据库' && document.querySelectorAll('tbody tr').length > 0`, 'Cost data page')
  const costState = await evaluate(cdp, `(() => ({
    title: document.querySelector('h1')?.innerText ?? '',
    rows: document.querySelectorAll('tbody tr').length,
    table: document.querySelector('table[aria-label]')?.getAttribute('aria-label') ?? '',
  }))()`)
  assert(costState.rows > 0, 'Cost data page has no batch rows')
  assert(costState.table === '六类制造成本', `Unexpected cost view: ${costState.table}`)

  await evaluate(cdp, "document.querySelector('tbody tr')?.click()")
  await waitForCondition(cdp, `location.pathname.startsWith('/cost-data/')`, 'Cost detail route')
  await waitForCondition(cdp, `document.querySelector('h1')?.innerText?.length > 0 && ['成本汇总', '成本追溯', '来源记录'].every((value) => document.body.innerText.includes(value))`, 'Cost detail content')
  const detailState = await evaluate(cdp, `(() => ({
    title: document.querySelector('h1')?.innerText ?? '',
    summary: document.body.innerText.includes('成本汇总'),
    trace: document.body.innerText.includes('成本追溯'),
    sources: document.body.innerText.includes('来源记录'),
  }))()`)
  assert(detailState.summary && detailState.trace && detailState.sources, 'Cost detail tabs are missing')
  return { cost: costState, detail: detailState }
}

async function verifyKeyboardAccess(cdp) {
  await evaluate(cdp, 'document.body.focus()')
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9, nativeVirtualKeyCode: 9 })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9, nativeVirtualKeyCode: 9 })
  const active = await evaluate(cdp, `(() => {
    const element = document.activeElement
    return element && element !== document.body && element !== document.documentElement
      ? { tag: element.tagName, role: element.getAttribute('role'), label: element.getAttribute('aria-label'), text: element.textContent?.trim().slice(0, 80) }
      : null
  })()`)
  assert(active, 'Tab did not move focus to a keyboard reachable control')
  return active
}

async function captureState(cdp, state, artifact, firstState) {
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: state.width,
    height: state.height,
    deviceScaleFactor: 1,
    mobile: state.mobile,
  })
  const url = `${baseUrl}/reports/${encodeURIComponent(artifact.artifact_id)}?period=${encodeURIComponent(artifact.period)}`
  if (firstState) {
    await cdp.send('Page.navigate', { url })
    await waitForPageReady(cdp)
  }
  await evaluate(cdp, `localStorage.setItem('costgraph-theme', ${JSON.stringify(state.theme)})`)
  await cdp.send('Page.reload', { ignoreCache: true })
  await waitForPageReady(cdp, state.theme)
  await delay(500)

  const measurement = await evaluate(cdp, `(() => {
    const canvas = [...document.querySelectorAll('canvas')].find((element) => {
      const rect = element.getBoundingClientRect()
      return rect.width > 0 && rect.height > 0
    })
    const rect = canvas?.getBoundingClientRect()
    let paintedPixels = 0
    if (canvas?.width && canvas?.height) {
      const context = canvas.getContext('2d', { willReadFrequently: true })
      if (context) {
        const image = context.getImageData(0, 0, canvas.width, canvas.height).data
        for (let i = 3; i < image.length; i += 4) if (image[i] > 0) paintedPixels++
      }
    }
    const text = document.body.innerText
    return {
      title: document.querySelector('h1')?.innerText ?? '',
      theme: document.documentElement.dataset.theme ?? '',
      viewportWidth: innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      reportContent: document.querySelector('[aria-label="展示型报表核心指标"]') !== null && ['批次料工费构成', '分析结论', '可审计元数据'].every((value) => text.includes(value)),
      batchContent: text.includes('产成品批次'),
      currentQuantitiesAreInteger: !/\\b\\d+\\.\\d{4}\\s+(?:pcs|件|kg)\\b/i.test(text),
      canvas: canvas ? { width: canvas.width, height: canvas.height, displayWidth: rect.width, displayHeight: rect.height, paintedPixels } : null,
    }
  })()`)

  assert(measurement.title.includes('展示型报表'), `${state.name}: presentation report heading missing`)
  assert(measurement.theme === state.theme, `${state.name}: expected theme ${state.theme}, got ${measurement.theme}`)
  assert(measurement.reportContent && measurement.batchContent, `${state.name}: report sections are missing`)
  assert(measurement.scrollWidth <= measurement.clientWidth, `${state.name}: horizontal overflow (${measurement.scrollWidth} > ${measurement.clientWidth})`)
  assert(measurement.currentQuantitiesAreInteger, `${state.name}: a four-decimal quantity remains visible`)
  assert(measurement.canvas?.width > 0 && measurement.canvas?.height > 0, `${state.name}: chart canvas has zero intrinsic dimensions`)
  assert(measurement.canvas?.displayWidth > 0 && measurement.canvas?.displayHeight > 0, `${state.name}: chart canvas is not visible`)
  assert(measurement.canvas?.paintedPixels > 30, `${state.name}: chart canvas appears blank (${measurement.canvas?.paintedPixels ?? 0} painted pixels)`)

  const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false })
  await writeFile(path.join(evidenceDir, `${state.name}.png`), Buffer.from(screenshot.data, 'base64'))
  return measurement
}

async function stopProcessTree(process) {
  if (!process || process.exitCode !== null) return
  if (process.platform === 'win32') spawnSync('taskkill.exe', ['/pid', String(process.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' })
  else process.kill('SIGTERM')
}

async function main() {
  await mkdir(evidenceDir, { recursive: true })
  let viteProcess = null
  let chromeProcess = null
  let cdp = null
  let profileDir = null
  try {
    try {
      await waitForHttp(`${baseUrl}/`, 1000, null)
    } catch {
      viteProcess = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', new URL(baseUrl).port || '5174', '--strictPort'], {
        cwd: frontendRoot,
        stdio: 'ignore',
        windowsHide: true,
      })
      await waitForHttp(`${baseUrl}/`, 30_000, viteProcess)
    }

    const list = await fetchJson(artifactListUrl)
    let artifact = null
    let comparisonArtifact = null
    for (const candidate of list.items ?? []) {
      const detail = await fetchJson(`${baseUrl}/api/artifacts/${encodeURIComponent(candidate.artifact_id)}`)
      if (detail.report_json?.report_style === 'presentation' && detail.deleted_at === null) {
        artifact = candidate
      }
      if (detail.report_json?.report_style === 'period_comparison' && detail.deleted_at === null) comparisonArtifact = candidate
    }
    assert(artifact, 'No active presentation Artifact is available through the API')
    assert(comparisonArtifact, 'No active period comparison Artifact is available through the API')
    const detail = await fetchJson(`${baseUrl}/api/artifacts/${encodeURIComponent(artifact.artifact_id)}`)
    assert(detail.artifact_id === artifact.artifact_id, 'Artifact detail ID did not match API list item')

    const debugPort = 9300 + Math.floor(Math.random() * 1000)
    profileDir = await mkdtemp(path.join(tmpdir(), 'costgraph-report-chrome-'))
    const browser = findChrome()
    chromeProcess = spawn(browser, [
      '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run', '--no-default-browser-check',
      '--disable-extensions', '--disable-background-networking', '--disable-breakpad',
      `--user-data-dir=${profileDir}`, `--remote-debugging-port=${debugPort}`, '--remote-allow-origins=*', 'about:blank',
    ], { stdio: 'ignore', windowsHide: true })
    const endpoint = `http://127.0.0.1:${debugPort}/json/list`
    const deadline = Date.now() + 20_000
    let target = null
    while (Date.now() < deadline) {
      try { target = await getDevToolsTarget(debugPort); break } catch { await delay(300) }
    }
    assert(target, 'Chromium DevTools endpoint did not become ready')
    cdp = await connectCdp(target.webSocketDebuggerUrl)
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')

    const workspace = await verifyCostWorkspace(cdp, artifact)
    await cdp.send('Page.navigate', { url: `${baseUrl}/reports/${encodeURIComponent(comparisonArtifact.artifact_id)}?period=${encodeURIComponent(comparisonArtifact.period)}` })
    await waitForCondition(cdp, `document.querySelector('h1')?.innerText.includes('周期对比报表')`, 'Period comparison report')
    const comparison = await evaluate(cdp, `(() => ({
      title: document.querySelector('h1')?.innerText ?? '',
      baseline: document.body.innerText.includes('基准期'),
      target: document.body.innerText.includes('目标期'),
      chart: [...document.querySelectorAll('canvas')].some((canvas) => canvas.getBoundingClientRect().width > 0),
    }))()`)
    assert(comparison.baseline && comparison.target && comparison.chart, 'Period comparison content is incomplete')

    const measurements = []
    for (const [index, state] of states.entries()) {
      const measurement = await captureState(cdp, state, artifact, index === 0)
      if (index === 0) measurement.keyboardFocus = await verifyKeyboardAccess(cdp)
      measurements.push({ state: state.name, ...measurement })
    }
    const result = {
      status: 'passed',
      artifact_id: artifact.artifact_id,
      comparison_artifact_id: comparisonArtifact.artifact_id,
      report_style: detail.report_json.report_style,
      route: `/reports/${artifact.artifact_id}?period=${artifact.period}`,
      workspace,
      comparison,
      views: measurements,
    }
    const resultPath = path.join(evidenceDir, 'result.json')
    await writeFile(resultPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8')
    console.log(JSON.stringify({ ...result, evidence_dir: evidenceDir }, null, 2))
  } catch (error) {
    const failure = { status: 'failed', message: error instanceof Error ? error.stack ?? error.message : String(error), evidence_dir: evidenceDir }
    await writeFile(path.join(evidenceDir, 'result.json'), `${JSON.stringify(failure, null, 2)}\n`, 'utf8').catch(() => undefined)
    console.error(JSON.stringify(failure, null, 2))
    process.exitCode = 1
  } finally {
    cdp?.socket.close()
    await stopProcessTree(chromeProcess)
    await stopProcessTree(viteProcess)
    if (profileDir) await rm(profileDir, { recursive: true, force: true }).catch(() => undefined)
  }
}

await main()
