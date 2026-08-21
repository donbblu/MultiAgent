const $ = (id) => document.getElementById(id)
let currentTask = null
let pollTimer = null

const statusLabels = {
  idle: 'IDLE', queued: 'QUEUED', running: 'RUNNING', paused: 'PAUSED',
  completed: 'PASSED', failed: 'FAILED', cancelled: 'CANCELLED', skipped: 'SKIPPED',
  pending: 'PENDING', success: 'PASSED'
}

function safeStatus(status) {
  return String(status || 'idle').toLowerCase().replace(/[^a-z_-]/g, '') || 'idle'
}

function setRunStatus(status) {
  const value = safeStatus(status)
  $('run-status').textContent = statusLabels[value] || value.toUpperCase()
  $('run-status').className = `status-pill ${value}`
}

function formatTime(value) {
  if (!value) return '刚刚'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', second: '2-digit'})
}

function renderWorkflow(data) {
  const nodes = data.nodes || {}
  document.querySelectorAll('[data-node]').forEach((element) => {
    const node = nodes[element.dataset.node] || {status: 'pending'}
    element.className = safeStatus(node.status)
    element.querySelector('small').textContent = node.last_summary || node.summary || '等待调度'
  })
  const gate = document.querySelector('[data-system="gate"]')
  gate.className = `system-node ${data.status === 'completed' ? 'success' : data.status === 'failed' ? 'failed' : ''}`
}

function renderAgents(data) {
  const container = $('agent-grid')
  container.replaceChildren()
  Object.values(data.nodes || {}).forEach((node) => {
    const article = document.createElement('article')
    article.className = `agent-card ${safeStatus(node.status)}`
    const head = document.createElement('div')
    const title = document.createElement('strong')
    title.textContent = node.label || node.id
    const badge = document.createElement('em')
    badge.textContent = statusLabels[safeStatus(node.status)] || String(node.status).toUpperCase()
    head.append(title, badge)
    const summary = document.createElement('p')
    summary.textContent = node.last_summary || node.summary || '等待 Harness 调度'
    const meta = document.createElement('small')
    const duration = Number.isFinite(node.duration_ms) ? ` · ${node.duration_ms} ms` : ''
    meta.textContent = `尝试 ${node.attempt || 0}${duration}`
    const permissions = document.createElement('div')
    permissions.className = 'permission-list'
    ;(node.permissions || []).forEach((permission) => {
      const item = document.createElement('span')
      item.textContent = permission
      permissions.append(item)
    })
    article.append(head, summary, meta, permissions)
    container.append(article)
  })
}

function renderEvents(data) {
  const events = [...(data.events || [])].reverse()
  $('event-count').textContent = `${events.length} 条`
  const container = $('event-list')
  container.replaceChildren()
  if (!events.length) {
    const empty = document.createElement('p')
    empty.className = 'inline-empty'
    empty.textContent = '等待 Runtime 记录第一个可观察事件。'
    container.append(empty)
    return
  }
  events.slice(0, 20).forEach((event) => {
    const article = document.createElement('article')
    const marker = document.createElement('i')
    marker.className = event.type || 'status'
    const body = document.createElement('div')
    const title = document.createElement('strong')
    title.textContent = event.title || event.event || '运行事件'
    const meta = document.createElement('small')
    meta.textContent = `${event.node_id || 'runtime'} · ${formatTime(event.timestamp)}`
    body.append(title, meta)
    article.append(marker, body)
    container.append(article)
  })
}

function renderDeliverables(data) {
  const container = $('deliverables')
  container.replaceChildren()
  if (data.error) {
    const error = document.createElement('div')
    error.className = 'result-message error'
    error.textContent = data.error
    container.append(error)
  }
  if (data.result?.summary) {
    const summary = document.createElement('div')
    summary.className = 'result-message'
    const label = document.createElement('small')
    label.textContent = 'RUNTIME SUMMARY'
    const text = document.createElement('p')
    text.textContent = data.result.summary
    summary.append(label, text)
    container.append(summary)
  }
  const files = data.result?.files || []
  files.forEach((file) => {
    const row = document.createElement('div')
    row.className = 'file-row'
    const icon = document.createElement('i')
    icon.textContent = '↳'
    const path = document.createElement('code')
    path.textContent = file
    row.append(icon, path)
    container.append(row)
  })
  if (!data.error && !data.result && !files.length) {
    const empty = document.createElement('p')
    empty.className = 'inline-empty'
    empty.textContent = '任务结束后，这里会显示 Runtime 摘要和交付文件。'
    container.append(empty)
  }
}

function updateControls(data) {
  const state = safeStatus(data.lifecycle?.state || data.status)
  const active = ['queued', 'running', 'paused'].includes(state)
  $('pause-task').disabled = !['queued', 'running'].includes(state)
  $('resume-task').disabled = state !== 'paused'
  $('cancel-task').disabled = !active
  $('submit-task').disabled = active
  $('submit-task').querySelector('span').textContent = active ? '任务运行中' : '启动 Coding 任务'
}

function renderTask(data) {
  $('empty-state').hidden = true
  $('task-details').hidden = false
  setRunStatus(data.status)
  $('task-caption').textContent = `${data.id} · ${statusLabels[safeStatus(data.status)] || data.status}`
  $('metric-status').textContent = statusLabels[safeStatus(data.status)] || data.status
  $('metric-active').textContent = (data.active_roles || []).length
  $('metric-attempts').textContent = data.result?.attempts ?? '—'
  $('metric-files').textContent = data.result?.files?.length ?? '—'
  renderWorkflow(data)
  renderAgents(data)
  renderEvents(data)
  renderDeliverables(data)
  updateControls(data)
}

async function pollTask() {
  if (!currentTask) return
  try {
    const response = await fetch(`/api/tasks/${currentTask}`)
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || '读取任务失败')
    renderTask(data)
    if (['completed', 'failed', 'cancelled'].includes(safeStatus(data.status))) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  } catch (error) {
    clearInterval(pollTimer)
    pollTimer = null
    setRunStatus('failed')
    $('form-message').textContent = error.message
    $('form-message').className = 'form-message error'
    $('submit-task').disabled = false
  }
}

async function controlTask(action) {
  if (!currentTask) return
  const button = $(`${action}-task`)
  button.disabled = true
  try {
    const response = await fetch(`/api/tasks/${currentTask}/${action}`, {method: 'POST'})
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || data.reason || '操作未被接受')
    await pollTask()
  } catch (error) {
    $('form-message').textContent = error.message
    $('form-message').className = 'form-message error'
  }
}

$('task-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  clearInterval(pollTimer)
  $('submit-task').disabled = true
  $('submit-task').querySelector('span').textContent = '正在创建任务'
  $('form-message').textContent = 'Runtime 正在建立任务状态与生命周期控制器…'
  $('form-message').className = 'form-message'
  try {
    const response = await fetch('/api/tasks', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: $('task-name').value.trim(), requirement: $('requirement').value.trim()})
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || '无法启动任务')
    currentTask = data.id
    setRunStatus('queued')
    await pollTask()
    pollTimer = setInterval(pollTask, 900)
  } catch (error) {
    setRunStatus('failed')
    $('form-message').textContent = error.message
    $('form-message').className = 'form-message error'
    $('submit-task').disabled = false
    $('submit-task').querySelector('span').textContent = '重新尝试'
  }
})

$('pause-task').addEventListener('click', () => controlTask('pause'))
$('resume-task').addEventListener('click', () => controlTask('resume'))
$('cancel-task').addEventListener('click', () => controlTask('cancel'))
