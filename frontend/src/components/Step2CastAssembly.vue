<template>
  <div class="cast-assembly-panel">
    <div class="scroll-container">

      <!-- Step 01: Generate Groups -->
      <div class="step-card" :class="{ active: phase === 0, completed: phase > 0 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">01</span>
            <span class="step-title">Generate Stakeholder Groups</span>
          </div>
          <div class="step-status">
            <span v-if="phase > 0" class="badge success">Done</span>
            <span v-else-if="generatingGroups" class="badge processing">Generating...</span>
            <span v-else class="badge pending">Pending</span>
          </div>
        </div>

        <div class="card-content">
          <p class="api-note">POST /api/simulation/&lt;id&gt;/groups/generate</p>
          <p class="description">
            The LLM analyzes your event and proposes relevant stakeholder groups —
            each will be populated with real people via Nyne. Named entities from
            your document are automatically included.
          </p>

          <div v-if="phase === 0" class="action-area">
            <textarea
              v-model="eventDescription"
              class="event-input"
              placeholder="Describe the event or topic you want to simulate (e.g. 'SEC proposes new crypto exchange regulation requiring all exchanges to register')..."
              rows="4"
            />
            <button
              class="btn-primary"
              :disabled="generatingGroups || !eventDescription.trim()"
              @click="handleGenerateGroups"
            >
              {{ generatingGroups ? 'Generating...' : 'Generate Groups' }}
            </button>
          </div>

          <div v-if="error" class="error-msg">{{ error }}</div>
        </div>
      </div>

      <!-- Step 02: Review & Edit Cast -->
      <div class="step-card" :class="{ active: phase === 1, completed: phase > 1, disabled: phase < 1 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">02</span>
            <span class="step-title">Review & Curate Cast</span>
          </div>
          <div class="step-status">
            <span v-if="phase > 1" class="badge success">Approved</span>
            <span v-else-if="phase === 1" class="badge processing">{{ totalMembers }} people</span>
            <span v-else class="badge pending">Pending</span>
          </div>
        </div>

        <div class="card-content" v-if="phase >= 1">
          <p class="description">
            Add/remove groups, adjust counts, upload CSVs, or paste LinkedIn URLs.
            Unfilled slots become synthetic fallbacks (labeled clearly).
          </p>

          <!-- Summary bar -->
          <div class="summary-bar">
            <div class="summary-item">
              <span class="summary-num">{{ groups.length }}</span>
              <span class="summary-label">Groups</span>
            </div>
            <div class="summary-item">
              <span class="summary-num">{{ realMemberCount }}</span>
              <span class="summary-label">Real (Nyne)</span>
            </div>
            <div class="summary-item">
              <span class="summary-num">{{ syntheticMemberCount }}</span>
              <span class="summary-label">Synthetic fallback</span>
            </div>
            <div class="summary-item">
              <span class="summary-num">{{ totalMembers }}</span>
              <span class="summary-label">Total agents</span>
            </div>
          </div>

          <!-- Group cards -->
          <div v-for="group in groups" :key="group.group_id" class="group-card">
            <div class="group-header">
              <div class="group-title-row">
                <span
                  class="source-badge"
                  :class="group.source === 'auto_named_entity' ? 'badge-blue' : 'badge-purple'"
                >
                  {{ group.source === 'auto_named_entity' ? 'From doc' : 'Archetype' }}
                </span>
                <span class="group-name">{{ group.name }}</span>
                <span class="group-count">{{ group.members.length }} / {{ group.target_count }}</span>
              </div>
              <div class="group-actions">
                <button class="btn-sm" @click="openUrlInput(group.group_id)" title="Add LinkedIn URLs">
                  + URL
                </button>
                <label class="btn-sm csv-label" :for="`csv-${group.group_id}`" title="Upload CSV">
                  + CSV
                  <input
                    :id="`csv-${group.group_id}`"
                    type="file"
                    accept=".csv"
                    style="display:none"
                    @change="handleCsvUpload($event, group.group_id)"
                  />
                </label>
                <button
                  class="btn-sm btn-danger"
                  @click="handleDeleteGroup(group.group_id)"
                  title="Remove group"
                >
                  ✕
                </button>
              </div>
            </div>

            <p class="group-criteria">{{ group.criteria }}</p>

            <!-- URL input inline -->
            <div v-if="urlInputGroupId === group.group_id" class="url-input-row">
              <input
                v-model="urlInputValue"
                class="url-input"
                placeholder="Paste LinkedIn URL(s), comma or newline separated"
              />
              <button class="btn-sm btn-confirm" @click="handleAddUrls(group.group_id)">Add</button>
              <button class="btn-sm" @click="urlInputGroupId = null">Cancel</button>
            </div>

            <!-- Members list -->
            <div class="members-list">
              <div
                v-for="member in group.members"
                :key="member.member_id"
                class="member-row"
              >
                <span
                  class="member-source-dot"
                  :class="memberSourceClass(member.source)"
                  :title="member.source"
                />
                <span class="member-name">{{ member.name }}</span>
                <span class="member-role text-muted">{{ member.role }}</span>
                <span v-if="member.linkedin_url" class="member-url text-muted">
                  {{ member.linkedin_url.replace('https://www.linkedin.com/in/', '') }}
                </span>
                <span v-if="member.source === 'synthetic_fallback'" class="badge-grey">Synthetic</span>
              </div>
              <div v-if="group.members.length === 0" class="empty-group">
                No members yet — use + URL or + CSV to add people
              </div>
            </div>
          </div>

          <!-- Add custom group -->
          <div class="add-group-area">
            <div v-if="!showAddGroup">
              <button class="btn-secondary" @click="showAddGroup = true">+ Add Custom Group</button>
            </div>
            <div v-else class="add-group-form">
              <input v-model="newGroupName" class="form-input" placeholder="Group name (e.g. 'Retail crypto investors')" />
              <input v-model="newGroupCriteria" class="form-input" placeholder="Criteria for Nyne search (e.g. 'retail investors who actively post about crypto on LinkedIn')" />
              <input v-model.number="newGroupCount" class="form-input-sm" type="number" min="1" max="20" placeholder="Count" />
              <div class="add-group-btns">
                <button class="btn-primary" @click="handleAddCustomGroup">Add Group</button>
                <button class="btn-secondary" @click="showAddGroup = false">Cancel</button>
              </div>
            </div>
          </div>

          <!-- Approve CTA -->
          <div class="approve-area">
            <button
              class="btn-approve"
              :disabled="approving || totalMembers === 0"
              @click="handleApprove"
            >
              {{ approving ? 'Starting enrichment...' : `Approve Cast & Start Enrichment (${totalMembers} people)` }}
            </button>
            <p class="approve-note">
              Nyne will enrich {{ realMemberCount }} real people.
              {{ syntheticMemberCount > 0 ? `${syntheticMemberCount} slots will use synthetic fallback.` : '' }}
            </p>
          </div>
        </div>
      </div>

      <!-- Step 03: Enrichment Running -->
      <div class="step-card" :class="{ active: phase === 2, completed: phase > 2, disabled: phase < 2 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">03</span>
            <span class="step-title">Nyne Enrichment</span>
          </div>
          <div class="step-status">
            <span v-if="phase > 2" class="badge success">Complete</span>
            <span v-else-if="phase === 2" class="badge processing">
              {{ enrichedCount }} / {{ totalMembers }} enriched
            </span>
            <span v-else class="badge pending">Pending</span>
          </div>
        </div>

        <div class="card-content" v-if="phase >= 2">
          <p class="description">
            Fetching real career history, actual social posts, and psychographic profiles via Nyne.
          </p>
          <div class="enrichment-grid">
            <div
              v-for="p in enrichmentProgress"
              :key="p.member_id"
              class="enrichment-chip"
              :class="enrichmentChipClass(p.status)"
              :title="p.linkedin_url || p.name"
            >
              <span class="chip-status-dot" />
              <span class="chip-name">{{ p.name }}</span>
              <span class="chip-status-label">{{ p.status }}</span>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  generateGroups,
  getGroups,
  populateGroup,
  uploadGroupCSV,
  getGroupsStatus,
  approveGroups,
  updateGroup,
  deleteGroup,
} from '../api/simulation'

const props = defineProps({
  simulationId: { type: String, required: true },
  eventDescription: { type: String, default: '' },
  documentText: { type: String, default: '' },
})

const emit = defineEmits(['cast-approved', 'update:phase'])

// State
const phase = ref(0)
const groups = ref([])
const enrichmentProgress = ref([])
const generatingGroups = ref(false)
const approving = ref(false)
const error = ref('')
const eventDescription = ref(props.eventDescription)

// UI state
const urlInputGroupId = ref(null)
const urlInputValue = ref('')
const showAddGroup = ref(false)
const newGroupName = ref('')
const newGroupCriteria = ref('')
const newGroupCount = ref(5)

let pollTimer = null

// Computed
const totalMembers = computed(() => groups.value.reduce((s, g) => s + g.members.length, 0))
const realMemberCount = computed(() =>
  groups.value.reduce((s, g) => s + g.members.filter(m => m.source !== 'synthetic_fallback').length, 0)
)
const syntheticMemberCount = computed(() => totalMembers.value - realMemberCount.value)
const enrichedCount = computed(() => enrichmentProgress.value.filter(p => p.status === 'complete').length)

onMounted(async () => {
  // Try to restore existing groups if already generated
  try {
    const res = await getGroups(props.simulationId)
    if (res.data?.data?.groups?.length > 0) {
      groups.value = res.data.data.groups
      phase.value = 1
    }
  } catch {}

  // Check if enrichment already running/done
  await refreshStatus()
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

async function refreshStatus() {
  try {
    const res = await getGroupsStatus(props.simulationId)
    const data = res.data?.data
    if (!data) return
    if (data.groups?.length > 0) {
      groups.value = data.groups
      if (phase.value < 1) phase.value = 1
    }
    if (data.enrichment_progress?.length > 0) {
      enrichmentProgress.value = data.enrichment_progress
      if (phase.value < 2) phase.value = 2
    }
    // Check if enrichment is done
    const allDone = data.enrichment_progress?.every(
      p => ['complete', 'failed', 'synthetic'].includes(p.status)
    )
    if (allDone && data.enrichment_progress?.length > 0) {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
      emit('cast-approved', { simulationId: props.simulationId, groundingReport: data.grounding_report })
    }
  } catch {}
}

async function handleGenerateGroups() {
  if (!eventDescription.value.trim()) return
  generatingGroups.value = true
  error.value = ''
  try {
    const res = await generateGroups(props.simulationId, eventDescription.value)
    groups.value = res.data?.data?.groups || []
    phase.value = 1
  } catch (e) {
    error.value = e?.response?.data?.error || e.message
  } finally {
    generatingGroups.value = false
  }
}

async function handleDeleteGroup(groupId) {
  try {
    await deleteGroup(props.simulationId, groupId)
    groups.value = groups.value.filter(g => g.group_id !== groupId)
  } catch (e) {
    error.value = e?.response?.data?.error || e.message
  }
}

function openUrlInput(groupId) {
  urlInputGroupId.value = groupId
  urlInputValue.value = ''
}

async function handleAddUrls(groupId) {
  const raw = urlInputValue.value
  const urls = raw.split(/[\n,]+/).map(u => u.trim()).filter(u => u)
  if (!urls.length) return
  try {
    const res = await populateGroup(props.simulationId, groupId, 'urls', { urls })
    const updated = res.data?.data?.group
    if (updated) {
      const idx = groups.value.findIndex(g => g.group_id === groupId)
      if (idx >= 0) groups.value[idx] = updated
    }
  } catch (e) {
    error.value = e?.response?.data?.error || e.message
  }
  urlInputGroupId.value = null
}

async function handleCsvUpload(event, groupId) {
  const file = event.target.files[0]
  if (!file) return
  try {
    const res = await uploadGroupCSV(props.simulationId, groupId, file)
    const updated = res.data?.data?.group
    if (updated) {
      const idx = groups.value.findIndex(g => g.group_id === groupId)
      if (idx >= 0) groups.value[idx] = updated
    }
  } catch (e) {
    error.value = e?.response?.data?.error || e.message
  }
}

async function handleAddCustomGroup() {
  if (!newGroupName.value.trim()) return
  // Add group locally (POST to populate later)
  const group = {
    group_id: `grp_custom_${Date.now()}`,
    name: newGroupName.value,
    criteria: newGroupCriteria.value,
    target_count: newGroupCount.value || 5,
    source: 'user_defined',
    members: [],
    status: 'pending',
    filled_count: 0,
  }
  groups.value.push(group)
  showAddGroup.value = false
  newGroupName.value = ''
  newGroupCriteria.value = ''
  newGroupCount.value = 5
}

async function handleApprove() {
  approving.value = true
  error.value = ''
  try {
    await approveGroups(
      props.simulationId,
      eventDescription.value || props.eventDescription,
      props.documentText,
    )
    phase.value = 2
    // Start polling enrichment progress
    pollTimer = setInterval(refreshStatus, 3000)
  } catch (e) {
    error.value = e?.response?.data?.error || e.message
  } finally {
    approving.value = false
  }
}

function memberSourceClass(source) {
  const map = {
    nyne_search: 'dot-green',
    csv: 'dot-blue',
    named_entity: 'dot-blue',
    user_url: 'dot-blue',
    synthetic_fallback: 'dot-grey',
  }
  return map[source] || 'dot-grey'
}

function enrichmentChipClass(status) {
  const map = {
    pending: 'chip-pending',
    enriching: 'chip-enriching',
    complete: 'chip-complete',
    failed: 'chip-failed',
    synthetic: 'chip-synthetic',
  }
  return map[status] || 'chip-pending'
}
</script>

<style scoped>
.cast-assembly-panel { padding: 16px; }
.scroll-container { display: flex; flex-direction: column; gap: 16px; }

.step-card {
  background: #1a1a2e;
  border: 1px solid #2a2a4a;
  border-radius: 8px;
  overflow: hidden;
  opacity: 0.5;
  transition: opacity 0.2s;
}
.step-card.active, .step-card.completed { opacity: 1; }
.step-card.disabled { pointer-events: none; }

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #2a2a4a;
}
.step-info { display: flex; align-items: center; gap: 10px; }
.step-num { font-size: 11px; color: #888; font-family: monospace; }
.step-title { font-size: 13px; font-weight: 600; color: #e0e0f0; }
.card-content { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.api-note { font-size: 10px; color: #666; font-family: monospace; }
.description { font-size: 12px; color: #aaa; line-height: 1.5; }

.badge { font-size: 10px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }
.badge.success { background: #1a3a2a; color: #4caf50; }
.badge.processing { background: #1a2a3a; color: #64b5f6; }
.badge.pending { background: #2a2a2a; color: #888; }

.event-input {
  width: 100%;
  background: #0f0f1a;
  border: 1px solid #333;
  border-radius: 6px;
  padding: 10px;
  color: #e0e0f0;
  font-size: 12px;
  resize: vertical;
  font-family: inherit;
}
.event-input:focus { outline: none; border-color: #5c6bc0; }

.btn-primary {
  background: #3f51b5;
  color: #fff;
  border: none;
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
}
.btn-primary:hover:not(:disabled) { background: #5c6bc0; }
.btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }

.btn-secondary {
  background: transparent;
  color: #aaa;
  border: 1px solid #444;
  padding: 6px 12px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
}
.btn-secondary:hover { border-color: #888; color: #e0e0f0; }

.btn-sm {
  background: transparent;
  color: #aaa;
  border: 1px solid #333;
  padding: 3px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 10px;
}
.btn-sm:hover { border-color: #666; color: #e0e0f0; }
.btn-danger { border-color: #4a2a2a; color: #e57373; }
.btn-danger:hover { background: #4a2a2a; }
.btn-confirm { border-color: #2a4a2a; color: #81c784; }
.btn-confirm:hover { background: #2a4a2a; }

.csv-label { cursor: pointer; }

.summary-bar {
  display: flex;
  gap: 16px;
  padding: 10px 12px;
  background: #0f0f1a;
  border-radius: 6px;
}
.summary-item { display: flex; flex-direction: column; align-items: center; }
.summary-num { font-size: 18px; font-weight: 700; color: #e0e0f0; }
.summary-label { font-size: 10px; color: #888; }

.group-card {
  border: 1px solid #2a2a4a;
  border-radius: 6px;
  padding: 12px;
  background: #0f0f1a;
}
.group-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 6px;
}
.group-title-row { display: flex; align-items: center; gap: 8px; }
.group-name { font-size: 13px; font-weight: 600; color: #e0e0f0; }
.group-count { font-size: 11px; color: #666; }
.group-actions { display: flex; gap: 6px; }
.group-criteria { font-size: 11px; color: #888; margin-bottom: 8px; font-style: italic; }

.source-badge {
  font-size: 9px;
  padding: 1px 6px;
  border-radius: 8px;
  font-weight: 500;
}
.badge-blue { background: #1a2a3a; color: #64b5f6; }
.badge-purple { background: #2a1a3a; color: #ce93d8; }
.badge-grey { font-size: 9px; padding: 1px 6px; border-radius: 8px; background: #2a2a2a; color: #666; }

.url-input-row { display: flex; gap: 6px; align-items: center; margin: 8px 0; }
.url-input {
  flex: 1;
  background: #1a1a2e;
  border: 1px solid #333;
  border-radius: 4px;
  padding: 5px 8px;
  color: #e0e0f0;
  font-size: 11px;
}
.url-input:focus { outline: none; border-color: #5c6bc0; }

.members-list { display: flex; flex-direction: column; gap: 4px; }
.member-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 11px;
  border-bottom: 1px solid #1a1a2e;
}
.member-row:last-child { border-bottom: none; }
.member-source-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-green { background: #4caf50; }
.dot-blue { background: #64b5f6; }
.dot-grey { background: #666; }
.member-name { font-weight: 500; color: #e0e0f0; min-width: 120px; }
.member-role { color: #888; min-width: 100px; }
.member-url { color: #555; font-family: monospace; font-size: 10px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.text-muted { color: #666; }
.empty-group { font-size: 11px; color: #555; font-style: italic; padding: 8px 0; }

.add-group-area { border-top: 1px solid #2a2a4a; padding-top: 12px; }
.add-group-form { display: flex; flex-direction: column; gap: 8px; }
.form-input {
  background: #0f0f1a;
  border: 1px solid #333;
  border-radius: 6px;
  padding: 8px;
  color: #e0e0f0;
  font-size: 12px;
}
.form-input-sm { width: 80px; }
.form-input:focus { outline: none; border-color: #5c6bc0; }
.add-group-btns { display: flex; gap: 8px; }

.approve-area {
  border-top: 1px solid #2a2a4a;
  padding-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.btn-approve {
  background: linear-gradient(135deg, #1565c0, #283593);
  color: #fff;
  border: none;
  padding: 12px 24px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}
.btn-approve:hover:not(:disabled) { background: linear-gradient(135deg, #1976d2, #3949ab); }
.btn-approve:disabled { opacity: 0.4; cursor: not-allowed; }
.approve-note { font-size: 11px; color: #888; }

.enrichment-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.enrichment-chip {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 10px;
  border: 1px solid transparent;
}
.chip-pending { background: #1a1a2a; border-color: #333; color: #888; }
.chip-enriching { background: #0a1a2a; border-color: #1565c0; color: #64b5f6; }
.chip-complete { background: #0a1a0a; border-color: #2e7d32; color: #81c784; }
.chip-failed { background: #1a0a0a; border-color: #7f0000; color: #e57373; }
.chip-synthetic { background: #1a1a1a; border-color: #333; color: #555; }

.chip-status-dot {
  width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0;
}
.chip-name { font-weight: 500; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chip-status-label { font-size: 9px; color: inherit; opacity: 0.7; }

.error-msg { color: #e57373; font-size: 11px; padding: 6px 10px; background: #1a0a0a; border-radius: 4px; }
.action-area { display: flex; flex-direction: column; gap: 8px; }
</style>
