"""VisionForge 私有 Artifact 协议名；Core 只保存字符串，不解释内容。"""

REFERENCE_IMAGE = "visionforge:reference_image"
UI_SPEC = "visionforge:ui_spec"
ACTUAL_SCREENSHOT = "visionforge:actual_screenshot"
BROWSER_RUN = "visionforge:browser_run"
VISUAL_REVIEW = "visionforge:visual_review"
QUALITY_GATE = "visionforge:quality_gate"
RUN = "visionforge:run"

VISIONFORGE_ARTIFACT_KINDS = frozenset({
    REFERENCE_IMAGE,
    UI_SPEC,
    ACTUAL_SCREENSHOT,
    BROWSER_RUN,
    VISUAL_REVIEW,
    QUALITY_GATE,
    RUN,
})
