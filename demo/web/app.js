const $ = (id) => document.getElementById(id)
const stageKinds = ['reference_image', 'ui_spec', 'implementation_plan', 'browser_run', 'visual_review', 'quality_gate']
const kindLabels = {
  reference_image: '参考图', ui_spec: 'UI Spec', implementation_plan: '初始 Patch',
  fix_plan: '修复 Patch', integration_result: 'Patch 应用结果', build_result: '构建结果',
  actual_screenshot: '实际截图', browser_run: 'Browser Run', visual_review: 'Visual Review',
  quality_gate: '质量门禁', visionforge_run: '最终 Run'
}
let selectedFile = null
let uploadedAsset = null
let currentTask = null
let pollTimer = null
let previewUrl = null

function formatBytes(value) {
  if (!Number.isFinite(value)) return '—'
  if (value < 1024) return `${value} B`
  return `${(value / 1024 / 1024).toFixed(2)} MiB`
}

function artifactMap(artifacts) {
  return new Map((artifacts || []).map((artifact) => [artifact.ref, artifact]))
}

function latestByKind(artifacts, kind) {
  return [...(artifacts || [])].reverse().find((artifact) => artifact.kind === kind)
}

function setStatus(status) {
  const pill = $('run-status')
  const text = {queued: 'QUEUED', preparing: 'PREPARING', running: 'RUNNING', completed: 'PASSED', failed: 'FAILED'}[status] || 'IDLE'
  pill.textContent = text
  pill.className = `status-pill ${status || 'idle'}`
}

function updatePipeline(data) {
  const kinds = new Set((data.artifacts || []).map((artifact) => artifact.kind))
  document.querySelectorAll('#pipeline [data-stage]').forEach((node) => {
    const stage = node.dataset.stage
    const index = stageKinds.indexOf(stage)
    const reached = stage === 'reference' || kinds.has(stage)
    const previousReached = index <= 0 || stageKinds.slice(0, index).every((kind) => kinds.has(kind) || kind === 'reference_image')
    node.classList.toggle('done', reached)
    node.classList.toggle('active', !reached && previousReached && ['queued', 'preparing', 'running'].includes(data.status))
  })
}

function renderRounds(data, artifactsByRef) {
  const cycles = data.result?.cycles || []
  $('round-count').textContent = `${cycles.length} 轮`
  const container = $('rounds')
  container.replaceChildren()
  cycles.forEach((cycle) => {
    const review = artifactsByRef.get(cycle.visual_review_artifact_ref)?.content || {}
    const gate = artifactsByRef.get(cycle.quality_gate_artifact_ref)?.content || {}
    const article = document.createElement('article')
    article.className = `round ${cycle.passed ? 'passed' : 'failed'}`
    const head = document.createElement('div')
    const title = document.createElement('strong')
    title.textContent = cycle.round_index === 0 ? '首次生成' : `自动修复 ${cycle.round_index}`
    const badge = document.createElement('span')
    badge.textContent = cycle.passed ? '门禁通过' : '需要修复'
    head.append(title, badge)
    const score = document.createElement('p')
    score.textContent = `视觉评分 ${review.score ?? '—'} · ${(review.issues || []).length} 个视觉问题`
    const failures = document.createElement('small')
    failures.textContent = (gate.failures || []).join('；') || '构建、交互与视觉门禁全部满足'
    article.append(head, score, failures)
    container.append(article)
  })
  if (!cycles.length) {
    const empty = document.createElement('p')
    empty.className = 'inline-empty'
    empty.textContent = '浏览器验证尚未产生轮次。'
    container.append(empty)
  }
}

function renderArtifacts(data) {
  const container = $('artifacts')
  container.replaceChildren()
  ;(data.artifacts || []).forEach((artifact) => {
    const details = document.createElement('details')
    details.className = 'artifact'
    const summary = document.createElement('summary')
    const label = document.createElement('span')
    label.textContent = kindLabels[artifact.kind] || artifact.kind
    const meta = document.createElement('small')
    meta.textContent = `${artifact.validation_state} · ${artifact.ref.slice(-8)}`
    summary.append(label, meta)
    const pre = document.createElement('pre')
    pre.textContent = JSON.stringify(artifact.content, null, 2)
    details.append(summary, pre)
    container.append(details)
  })
}

function renderEvidence(data) {
  const artifacts = data.artifacts || []
  const byRef = artifactMap(artifacts)
  $('waiting-state').hidden = true
  $('evidence').hidden = false
  $('metric-status').textContent = data.status === 'completed' ? '通过' : data.status === 'failed' ? '失败' : '运行中'
  $('metric-score').textContent = data.result?.visual_score ?? '—'
  $('metric-fixes').textContent = data.result?.fix_attempts ?? '—'
  $('metric-artifacts').textContent = artifacts.length
  $('final-reference').src = data.reference_image.url
  const finalCycle = data.result?.cycles?.at(-1)
  const screenshot = finalCycle ? byRef.get(finalCycle.actual_screenshot_artifact_ref) : latestByKind(artifacts, 'actual_screenshot')
  if (screenshot?.content?.url) {
    $('final-actual').src = screenshot.content.url
    $('final-actual').hidden = false
    $('actual-frame').querySelector('p').hidden = true
  }
  renderRounds(data, byRef)
  renderArtifacts(data)
}

async function uploadReference() {
  if (uploadedAsset) return uploadedAsset
  if (!selectedFile) throw new Error('请先选择参考图')
  if (!['image/png', 'image/jpeg'].includes(selectedFile.type)) throw new Error('只支持 PNG 或 JPEG')
  if (selectedFile.size <= 0 || selectedFile.size > 10 * 1024 * 1024) throw new Error('图片必须小于 10 MiB')
  $('upload-status').textContent = '正在写入本地内容寻址存储…'
  const response = await fetch('/api/visionforge/assets', {
    method: 'POST', headers: {'Content-Type': selectedFile.type}, body: selectedFile
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || '图片上传失败')
  uploadedAsset = data
  $('upload-status').textContent = `已保存 ${data.width}×${data.height} · ${formatBytes(data.size_bytes)} · ${data.asset_id.slice(0, 10)}…`
  return data
}

async function pollTask() {
  if (!currentTask) return
  try {
    const response = await fetch(`/api/visionforge/tasks/${currentTask}`)
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || '读取任务失败')
    setStatus(data.status)
    updatePipeline(data)
    $('task-caption').textContent = `${data.id} · ${data.status}`
    if ((data.artifacts || []).length) renderEvidence(data)
    if (['completed', 'failed'].includes(data.status)) {
      clearInterval(pollTimer)
      pollTimer = null
      $('submit-task').disabled = false
      $('submit-task').querySelector('span').textContent = '启动新任务'
      renderEvidence(data)
      if (data.error) $('task-caption').textContent = `执行失败：${data.error}`
    }
  } catch (error) {
    clearInterval(pollTimer)
    pollTimer = null
    setStatus('failed')
    $('task-caption').textContent = error.message
    $('submit-task').disabled = false
  }
}

$('reference-file').addEventListener('change', (event) => {
  selectedFile = event.target.files?.[0] || null
  uploadedAsset = null
  if (previewUrl) URL.revokeObjectURL(previewUrl)
  if (!selectedFile) {
    $('reference-preview').hidden = true
    $('upload-placeholder').hidden = false
    $('upload-status').textContent = '尚未选择图片'
    return
  }
  previewUrl = URL.createObjectURL(selectedFile)
  $('reference-preview').src = previewUrl
  $('reference-preview').hidden = false
  $('upload-placeholder').hidden = true
  $('upload-status').textContent = `${selectedFile.name} · ${formatBytes(selectedFile.size)}`
})

$('visionforge-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  clearInterval(pollTimer)
  $('submit-task').disabled = true
  $('submit-task').querySelector('span').textContent = '准备任务中'
  $('waiting-state').hidden = false
  $('evidence').hidden = true
  try {
    const asset = await uploadReference()
    const response = await fetch('/api/visionforge/tasks', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({requirement: $('requirement').value, asset_id: asset.asset_id})
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || '无法启动任务')
    currentTask = data.id
    setStatus('queued')
    $('submit-task').querySelector('span').textContent = '视觉交付进行中'
    await pollTask()
    pollTimer = setInterval(pollTask, 900)
  } catch (error) {
    setStatus('failed')
    $('task-caption').textContent = error.message
    $('submit-task').disabled = false
    $('submit-task').querySelector('span').textContent = '重新尝试'
  }
})
