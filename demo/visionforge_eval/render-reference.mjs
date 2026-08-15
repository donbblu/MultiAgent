import fs from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const require = createRequire(
  new URL('../visionforge_vue_template/package.json', import.meta.url),
)
const { chromium } = require('playwright')

function parseArgs(argv) {
  const values = {}
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]
    const value = argv[index + 1]
    if (!key?.startsWith('--') || value === undefined) {
      throw new Error('参数必须使用 --name value 格式')
    }
    values[key.slice(2)] = value
  }
  for (const key of ['source', 'output', 'width', 'height', 'scale']) {
    if (!values[key]) throw new Error(`缺少参数 --${key}`)
  }
  const width = Number(values.width)
  const height = Number(values.height)
  const scale = Number(values.scale)
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 320 || height < 320) {
    throw new Error('viewport 无效')
  }
  if (!Number.isFinite(scale) || scale < 0.5 || scale > 3) {
    throw new Error('device scale factor 无效')
  }
  return { ...values, width, height, scale }
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const source = path.resolve(args.source)
  const output = path.resolve(args.output)
  if (path.extname(source) !== '.html') throw new Error('参考源必须是 HTML')
  await fs.access(source)
  const options = { headless: true }
  if (process.env.VISIONFORGE_BROWSER_EXECUTABLE) {
    options.executablePath = process.env.VISIONFORGE_BROWSER_EXECUTABLE
  }
  const browser = await chromium.launch(options)
  try {
    const context = await browser.newContext({
      viewport: { width: args.width, height: args.height },
      deviceScaleFactor: args.scale,
      locale: 'zh-CN',
      timezoneId: 'UTC',
      colorScheme: 'light',
      reducedMotion: 'reduce',
    })
    const page = await context.newPage()
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url())
      if (url.protocol === 'file:' || url.protocol === 'data:') {
        await route.continue()
      } else {
        await route.abort('blockedbyclient')
      }
    })
    await page.goto(pathToFileURL(source).href, { waitUntil: 'load', timeout: 15000 })
    await page.addStyleTag({
      content: '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}',
    })
    await page.evaluate(() => document.fonts?.ready)
    await fs.mkdir(path.dirname(output), { recursive: true })
    await page.screenshot({ path: output, fullPage: true })
  } finally {
    await browser.close()
  }
}

main().catch((error) => {
  process.stderr.write(`${String(error?.stack ?? error)}\n`)
  process.exitCode = 1
})
