<script setup>
import { ref } from 'vue'

const email = ref('')
const submitted = ref(false)

function submit() {
  submitted.value = Boolean(email.value.trim())
}
</script>

<template>
  <main class="page-shell" data-testid="page-shell">
    <section class="hero" aria-labelledby="page-title">
      <p class="eyebrow">VisionForge MVP</p>
      <h1 id="page-title">从视觉参考到可运行页面</h1>
      <p class="subtitle">
        这是供 Runtime、Playwright 和视觉审查使用的固定 Vue 3 测试页面。
      </p>

      <form class="signup" data-testid="signup-form" @submit.prevent="submit">
        <label for="email">工作邮箱</label>
        <div class="form-row">
          <input
            id="email"
            v-model="email"
            data-testid="email-input"
            type="email"
            placeholder="name@example.com"
          />
          <button data-testid="submit-button" type="submit">开始体验</button>
        </div>
      </form>

      <p v-if="submitted" class="success" data-testid="success-message" role="status">
        已收到，我们会继续完成视觉验收。
      </p>
    </section>
  </main>
</template>
