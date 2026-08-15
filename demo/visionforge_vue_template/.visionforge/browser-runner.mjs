import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'playwright'

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
  for (const required of ['url', 'spec', 'screenshot', 'result']) {
    if (!values[required]) {
      throw new Error(`缺少参数 --${required}`)
    }
  }
  return values
}

function messageText(message) {
  return message.text().slice(0, 2000)
}

async function requireUniqueLocator(page, selector) {
  const locator = page.locator(selector)
  const count = await locator.count()
  if (count !== 1) {
    throw new Error(`目标必须唯一匹配，实际 ${count} 个: ${selector}`)
  }
  return locator
}

async function runInteraction(page, interaction) {
  const started = Date.now()
  const result = {
    interaction_id: interaction.interaction_id,
    action: interaction.action,
    target: interaction.target,
    passed: false,
    evidence: '',
    error: '',
    duration_ms: 0,
  }
  try {
    if (interaction.action === 'expect_url') {
      const actual = page.url()
      if (!actual.includes(interaction.expected)) {
        throw new Error(`URL ${actual} 不包含 ${interaction.expected}`)
      }
      result.evidence = `URL: ${actual}`
    } else {
      const locator = await requireUniqueLocator(page, interaction.target)
      if (interaction.action === 'click') {
        await locator.click({ timeout: 5000 })
        result.evidence = `已点击 ${interaction.target}`
      } else if (interaction.action === 'fill') {
        await locator.fill(interaction.value, { timeout: 5000 })
        result.evidence = `已填写 ${interaction.target}`
      } else if (interaction.action === 'expect_visible') {
        if (!(await locator.isVisible())) {
          throw new Error(`目标不可见: ${interaction.target}`)
        }
        result.evidence = `目标可见: ${interaction.target}`
      } else if (interaction.action === 'expect_text') {
        const actual = (await locator.textContent({ timeout: 5000 })) ?? ''
        if (!actual.includes(interaction.expected)) {
          throw new Error(`文本 ${JSON.stringify(actual)} 不包含 ${JSON.stringify(interaction.expected)}`)
        }
        result.evidence = `文本: ${actual.trim().slice(0, 500)}`
      } else {
        throw new Error(`不支持的交互动作: ${interaction.action}`)
      }
    }
    result.passed = true
  } catch (error) {
    result.error = String(error?.message ?? error).slice(0, 2000)
  }
  result.duration_ms = Date.now() - started
  return result
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const spec = JSON.parse(await fs.readFile(args.spec, 'utf8'))
  const expectedOrigin = new URL(args.url).origin
  const consoleMessages = []
  const pageErrors = []
  const networkErrors = []
  const started = Date.now()
  const launchOptions = { headless: true }
  if (process.env.VISIONFORGE_BROWSER_EXECUTABLE) {
    launchOptions.executablePath = process.env.VISIONFORGE_BROWSER_EXECUTABLE
  }
  const browser = await chromium.launch(launchOptions)
  try {
    const context = await browser.newContext({
      viewport: {
        width: spec.viewport.width,
        height: spec.viewport.height,
      },
      deviceScaleFactor: spec.viewport.device_scale_factor,
      locale: 'zh-CN',
      timezoneId: 'UTC',
      colorScheme: 'light',
      reducedMotion: 'reduce',
    })
    const page = await context.newPage()
    page.on('console', (message) => {
      consoleMessages.push({
        level: message.type(),
        message: messageText(message),
      })
    })
    page.on('pageerror', (error) => {
      pageErrors.push(String(error?.message ?? error).slice(0, 2000))
    })
    await page.route('**/*', async (route) => {
      const requestUrl = new URL(route.request().url())
      if (
        ['data:', 'blob:'].includes(requestUrl.protocol)
        || requestUrl.origin === expectedOrigin
      ) {
        await route.continue()
        return
      }
      networkErrors.push(`阻止外部请求: ${requestUrl.origin}`)
      await route.abort('blockedbyclient')
    })
    await page.goto(args.url, { waitUntil: 'networkidle', timeout: 15000 })
    await page.addStyleTag({
      content: '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}',
    })
    await page.evaluate(() => document.fonts?.ready)
    const assertions = []
    for (const interaction of spec.interactions) {
      assertions.push(await runInteraction(page, interaction))
    }
    await fs.mkdir(path.dirname(args.screenshot), { recursive: true })
    await page.screenshot({ path: args.screenshot, fullPage: true })
    const seriousConsoleErrors = consoleMessages.filter((item) => item.level === 'error')
    const passed = assertions.every((item) => item.passed)
      && seriousConsoleErrors.length === 0
      && pageErrors.length === 0
      && networkErrors.length === 0
    const result = {
      schema_version: '1.0',
      passed,
      url: page.url(),
      viewport: spec.viewport,
      assertions,
      console_messages: consoleMessages,
      page_errors: pageErrors,
      network_errors: [...new Set(networkErrors)],
      screenshot_path: args.screenshot,
      duration_ms: Date.now() - started,
    }
    await fs.mkdir(path.dirname(args.result), { recursive: true })
    await fs.writeFile(args.result, JSON.stringify(result, null, 2), 'utf8')
  } finally {
    await browser.close()
  }
}

main().catch((error) => {
  process.stderr.write(`${String(error?.stack ?? error)}\n`)
  process.exitCode = 1
})
