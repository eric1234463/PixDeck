<script setup lang="ts">
import { ref } from 'vue'
import type { Transport } from '../types'
const props = defineProps<{ device: string; transport?: Transport }>()
const emit = defineEmits<{
  (e: 'save', ip: string): void
  (e: 'saveTransport', t: { mode: 'http' | 'mqtt'; broker: string; prefix: string; user: string; pass: string; retain: boolean }): void
  (e: 'close'): void
}>()
const ip = ref(props.device)
const mode = ref<'http' | 'mqtt'>(props.transport?.transport || 'http')
const broker = ref(props.transport?.broker || '')
const prefix = ref(props.transport?.prefix || '')
const user = ref('')
const pass = ref('')
const retain = ref(props.transport?.retain || false)
function saveAll() {
  emit('save', ip.value.trim())
  emit('saveTransport', { mode: mode.value, broker: broker.value.trim(), prefix: prefix.value.trim(), user: user.value, pass: pass.value, retain: retain.value })
}
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <div class="modal">
      <div class="head"><h3>设置</h3><button class="x" @click="emit('close')">&times;</button></div>
      <label class="field"><span>设备 IP</span><input v-model="ip" type="text" /></label>

      <label class="field"><span>推送方式</span>
        <select v-model="mode">
          <option value="http">HTTP（直接推设备）</option>
          <option value="mqtt">MQTT（经 broker）</option>
        </select>
      </label>
      <template v-if="mode === 'mqtt'">
        <label class="field"><span>Broker 地址</span><input v-model="broker" type="text" placeholder="192.168.1.5:1883" /></label>
        <label class="field"><span>Topic 前缀</span><input v-model="prefix" type="text" placeholder="ulanzi_1bf6" /></label>
        <label class="field"><span>用户名（可选）</span><input v-model="user" type="text" /></label>
        <label class="field"><span>密码（可选）</span><input v-model="pass" type="password" /></label>
        <label class="field row"><input v-model="retain" type="checkbox" /><span>保留消息（retain）</span></label>
      </template>

      <div class="actions"><button class="ghost" @click="emit('close')">取消</button><button class="primary" @click="saveAll">保存</button></div>
    </div>
  </div>
</template>

<style scoped>
.backdrop{position:fixed;inset:0;background:rgba(4,6,9,.55);display:flex;align-items:center;justify-content:center;z-index:50}
.modal{width:340px;background:var(--bg-1);border:1px solid var(--line-2);border-radius:14px;padding:18px}
.head{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.head h3{font-family:var(--display);font-weight:700;font-size:var(--fs-h2);margin:0}
.x{background:none;border:0;color:var(--ink-3);font-size:22px;cursor:pointer}
.field{display:flex;flex-direction:column;gap:6px;color:var(--ink-2)}
.field input{background:var(--bg-2);color:var(--ink);border:1px solid var(--line-2);border-radius:8px;padding:7px 9px;font-family:var(--mono)}
.actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}
button{cursor:pointer;border-radius:8px;border:1px solid var(--line-2);background:var(--bg-2);color:var(--ink);padding:7px 13px;font-weight:500}
.ghost{background:transparent}
.primary{background:var(--btn-accent);color:var(--on-accent);border-color:var(--btn-accent)}
.primary:hover{background:var(--btn-accent-h)}
.field.row{flex-direction:row;align-items:center;gap:8px}
.field select{background:var(--bg-2);color:var(--ink);border:1px solid var(--line-2);border-radius:8px;padding:7px 9px}
</style>
