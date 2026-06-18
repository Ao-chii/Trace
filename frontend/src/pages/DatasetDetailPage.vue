<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from "vue";
import { ArrowLeft, Bug, Database, GitBranch, RefreshCw } from "@lucide/vue";
import { createEvalDataset, getEvalDataset } from "../api/evaluation";
import JsonViewer from "../components/JsonViewer.vue";
import { demoEvalDataset } from "../demo/staticRunFixture";
import { useLatestRequest } from "../composables/useLatestRequest";
import { useI18n } from "../i18n";
import type { EvalDatasetDetailOut, EvalTaskDetailOut, JsonObject } from "../types/api";
import type { DataSource } from "../types/ui";

const props = defineProps<{
  datasetId: string;
  dataSource: DataSource;
}>();

const emit = defineEmits<{
  navigate: [hash: string];
}>();

const { t } = useI18n();

const dataset = shallowRef<EvalDatasetDetailOut | null>(null);
const loading = ref(false);
const errorMessage = ref<string | null>(null);
const selectedTaskId = ref<string | null>(null);
const creating = ref(false);
const createMessage = ref<string | null>(null);
const createForm = ref({
  id: "",
  name: "local eval dataset",
  version: "v1",
  description: "",
  projectSnapshotIds: ""
});

const datasetRequest = useLatestRequest();

const selectedTask = computed<EvalTaskDetailOut | null>(() => {
  const current = dataset.value;
  if (!current) {
    return null;
  }
  return current.tasks.find((task) => task.id === selectedTaskId.value) ?? current.tasks[0] ?? null;
});
const bugCount = computed<number>(() => {
  const current = dataset.value;
  return current ? current.tasks.reduce((count, task) => count + task.seeded_bugs.length, 0) : 0;
});
const variantCount = computed<number>(() => {
  const current = dataset.value;
  return current
    ? current.tasks.reduce((count, task) => count + task.seeded_bugs.reduce((inner, bug) => inner + bug.variants.length, 0), 0)
    : 0;
});

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function capabilityText(task: EvalTaskDetailOut): string {
  return task.expected_capabilities.length > 0 ? task.expected_capabilities.map((item) => String(item)).join(", ") : t("common.none");
}

async function loadDataset() {
  const requestSeq = datasetRequest.next();
  loading.value = true;
  errorMessage.value = null;
  try {
    const next = props.dataSource === "demo" ? demoEvalDataset : await getEvalDataset(props.datasetId);
    if (!datasetRequest.isCurrent(requestSeq)) {
      return;
    }
    dataset.value = next;
    selectedTaskId.value = next.tasks[0]?.id ?? null;
  } catch (error) {
    if (!datasetRequest.isCurrent(requestSeq)) {
      return;
    }
    dataset.value = null;
    selectedTaskId.value = null;
    errorMessage.value = error instanceof Error ? error.message : t("datasets.loadFailed");
  } finally {
    if (datasetRequest.isCurrent(requestSeq)) {
      loading.value = false;
    }
  }
}

async function submitDataset() {
  if (props.dataSource !== "api") {
    return;
  }
  creating.value = true;
  createMessage.value = null;
  errorMessage.value = null;
  try {
    const created = await createEvalDataset({
      id: createForm.value.id.trim() || null,
      name: createForm.value.name.trim(),
      version: createForm.value.version.trim(),
      description: createForm.value.description.trim() || null,
      project_snapshot_ids: createForm.value.projectSnapshotIds
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
    });
    createMessage.value = `${t("datasets.created")}: ${created.id}`;
    emit("navigate", `#/datasets/${created.id}`);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : t("datasets.createFailed");
  } finally {
    creating.value = false;
  }
}

onMounted(() => {
  void loadDataset();
});

watch(
  () => [props.datasetId, props.dataSource],
  () => {
    void loadDataset();
  }
);
</script>

<template>
  <main class="dataset-page app-page">
    <section class="detail-head">
      <button class="text-button" type="button" @click="emit('navigate', '#/experiments')">
        <ArrowLeft :size="16" aria-hidden="true" />
        {{ t("experiments.back") }}
      </button>
      <button class="text-button" type="button" @click="loadDataset">
        <RefreshCw :size="16" aria-hidden="true" />
        {{ t("projects.refresh") }}
      </button>
    </section>

    <p v-if="errorMessage" class="error-banner">{{ errorMessage }}</p>
    <p v-if="createMessage" class="mode-note">{{ createMessage }}</p>
    <p v-if="loading" class="mode-note">{{ t("datasets.loading") }}</p>

    <section v-if="props.dataSource === 'api'" class="subtle-panel create-dataset-panel">
      <div class="panel-head">
        <div>
          <p class="eyebrow">CREATE DATASET</p>
          <h2>{{ t("datasets.createTitle") }}</h2>
        </div>
        <button class="text-button" type="button" :disabled="creating" @click="submitDataset">
          {{ creating ? t("datasets.creating") : t("datasets.create") }}
        </button>
      </div>
      <div class="dataset-form-grid">
        <label>
          <span>{{ t("experiments.optionalId") }}</span>
          <input v-model="createForm.id" type="text" placeholder="dataset-local-v1" />
        </label>
        <label>
          <span>{{ t("experiments.name") }}</span>
          <input v-model="createForm.name" type="text" />
        </label>
        <label>
          <span>{{ t("datasets.version") }}</span>
          <input v-model="createForm.version" type="text" />
        </label>
        <label>
          <span>{{ t("projects.description") }}</span>
          <input v-model="createForm.description" type="text" />
        </label>
        <label class="wide-field">
          <span>{{ t("datasets.snapshots") }}</span>
          <input v-model="createForm.projectSnapshotIds" type="text" placeholder="snapshot id, snapshot id" />
        </label>
      </div>
    </section>

    <template v-if="dataset">
      <section class="hero-band subtle-panel">
        <div>
          <p class="eyebrow">TRACE / DATASET DETAIL</p>
          <h1>{{ dataset.name }}</h1>
          <p>{{ dataset.description ?? t("datasets.noDescription") }}</p>
        </div>
        <div class="source-card">
          <Database :size="18" aria-hidden="true" />
          <span>
            <strong>{{ dataset.id }}</strong>
            <small>{{ t("datasets.version") }} {{ dataset.version }} · {{ formatDate(dataset.created_at) }}</small>
          </span>
        </div>
      </section>

      <section class="metadata-grid">
        <article class="subtle-panel meta-tile">
          <span>{{ t("datasets.tasks") }}</span>
          <strong>{{ dataset.tasks.length }}</strong>
        </article>
        <article class="subtle-panel meta-tile">
          <span>{{ t("datasets.seededBugs") }}</span>
          <strong>{{ bugCount }}</strong>
        </article>
        <article class="subtle-panel meta-tile">
          <span>{{ t("datasets.variants") }}</span>
          <strong>{{ variantCount }}</strong>
        </article>
        <article class="subtle-panel meta-tile">
          <span>{{ t("datasets.snapshots") }}</span>
          <strong>{{ dataset.project_snapshot_ids.length }}</strong>
          <small>{{ dataset.project_snapshot_ids.join(", ") || t("common.none") }}</small>
        </article>
      </section>

      <section class="dataset-grid">
        <aside class="subtle-panel task-list">
          <div class="panel-head">
            <div>
              <p class="eyebrow">{{ t("datasets.tasks") }}</p>
              <h2>{{ dataset.tasks.length }}</h2>
            </div>
            <GitBranch :size="18" aria-hidden="true" />
          </div>
          <button
            v-for="task in dataset.tasks"
            :key="task.id"
            type="button"
            class="task-row"
            :class="{ selected: task.id === selectedTask?.id }"
            @click="selectedTaskId = task.id"
          >
            <span>
              <strong>{{ task.id }}</strong>
              <small>{{ task.project_snapshot_id }}</small>
            </span>
            <small>{{ task.seeded_bugs.length }} {{ t("datasets.seededBugs") }}</small>
          </button>
        </aside>

        <section v-if="selectedTask" class="task-detail">
          <article class="subtle-panel task-overview">
            <div>
              <p class="eyebrow">{{ t("datasets.selectedTask") }}</p>
              <h2>{{ selectedTask.id }}</h2>
              <p>{{ selectedTask.goal }}</p>
            </div>
            <dl>
              <div>
                <dt>{{ t("datasets.snapshot") }}</dt>
                <dd>{{ selectedTask.project_snapshot_id }}</dd>
              </div>
              <div>
                <dt>{{ t("datasets.capabilities") }}</dt>
                <dd>{{ capabilityText(selectedTask) }}</dd>
              </div>
            </dl>
          </article>

          <article class="subtle-panel json-panel">
            <div>
              <p class="eyebrow">{{ t("datasets.targetScope") }}</p>
              <h3>{{ t("datasets.scopeEvidence") }}</h3>
            </div>
            <JsonViewer :value="selectedTask.target_scope" :max-depth="5" :max-array-items="20" />
          </article>

          <section class="bug-stack">
            <article v-for="bug in selectedTask.seeded_bugs" :key="bug.id" class="subtle-panel bug-card">
              <header class="bug-head">
                <div>
                  <p class="eyebrow">{{ bug.bug_type }}</p>
                  <h3>{{ bug.id }}</h3>
                  <p>{{ bug.description }}</p>
                </div>
                <Bug :size="18" aria-hidden="true" />
              </header>
              <dl>
                <div>
                  <dt>{{ t("datasets.expectedDetection") }}</dt>
                  <dd>{{ bug.expected_detection }}</dd>
                </div>
              </dl>
              <div class="table-shell variant-table">
                <table>
                  <thead>
                    <tr>
                      <th>{{ t("datasets.variant") }}</th>
                      <th>{{ t("datasets.canonicalKind") }}</th>
                      <th>{{ t("datasets.snapshot") }}</th>
                      <th>{{ t("datasets.patch") }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="variant in bug.variants" :key="variant.id">
                      <td>
                        <strong>{{ variant.variant_name }}</strong>
                        <small class="block-mono">{{ variant.id }}</small>
                      </td>
                      <td>{{ variant.canonical_kind }}</td>
                      <td class="mono">{{ variant.mutated_snapshot_id ?? t("common.none") }}</td>
                      <td>
                        <JsonViewer :value="variant.ground_truth as JsonObject" :max-depth="4" :max-array-items="10" />
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </article>
          </section>
        </section>
      </section>
    </template>
  </main>
</template>

<style scoped>
.detail-head,
.hero-band,
.metadata-grid,
.dataset-grid,
.create-dataset-panel {
  margin-top: 18px;
}

.detail-head,
.hero-band,
.panel-head,
.bug-head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
}

.hero-band {
  padding: 18px;
}

.hero-band h1 {
  margin: 8px 0 10px;
}

.hero-band p {
  max-width: 760px;
  font-family: var(--font-serif);
}

.source-card {
  width: min(380px, 100%);
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: rgba(251, 250, 247, 0.78);
}

.source-card span,
.task-row span {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.source-card small,
.task-row small,
.block-mono,
.meta-tile small {
  overflow-wrap: anywhere;
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
}

.metadata-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.meta-tile,
.create-dataset-panel,
.task-list,
.task-overview,
.json-panel,
.bug-card {
  padding: 18px;
}

.create-dataset-panel {
  display: grid;
  gap: 14px;
}

.dataset-form-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.dataset-form-grid label {
  display: grid;
  gap: 6px;
}

.dataset-form-grid label span {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.dataset-form-grid input {
  width: 100%;
  min-height: 34px;
  padding: 6px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--panel);
  color: var(--ink);
}

.wide-field {
  grid-column: span 2;
}

.meta-tile {
  display: grid;
  gap: 3px;
}

.meta-tile span {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.meta-tile strong {
  overflow-wrap: anywhere;
  font-size: 24px;
  line-height: 1.2;
}

.dataset-grid {
  display: grid;
  grid-template-columns: minmax(260px, 340px) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

.task-list,
.task-detail,
.bug-stack,
.json-panel,
.bug-card {
  display: grid;
  gap: 14px;
}

.task-row {
  width: 100%;
  display: flex;
  gap: 10px;
  justify-content: space-between;
  margin-top: 10px;
  padding: 10px;
  text-align: left;
}

.task-row:hover:not(.selected) {
  border-color: var(--border-strong);
  background: rgba(251, 250, 247, 0.9);
  box-shadow: var(--shadow-hover);
  transform: translateY(-1px);
}

.task-row.selected {
  border-color: var(--tool);
  background: rgba(49, 95, 125, 0.08);
  box-shadow: inset 3px 0 0 var(--tool);
}

.task-overview {
  display: grid;
  gap: 14px;
}

.task-overview dl,
.bug-card dl {
  display: grid;
  gap: 10px;
  margin: 0;
}

.task-overview dt,
.bug-card dt {
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  text-transform: uppercase;
}

.task-overview dd,
.bug-card dd {
  margin: 0;
  color: var(--muted-strong);
  overflow-wrap: anywhere;
}

.bug-head p {
  margin-top: 6px;
}

.variant-table {
  margin-top: 4px;
}

.variant-table :deep(.json-tools),
.variant-table :deep(.json-note) {
  display: none;
}

.variant-table :deep(.json-viewer) {
  max-height: 180px;
}

@media (max-width: 1080px) {
  .hero-band,
  .metadata-grid,
  .dataset-grid,
  .dataset-form-grid {
    grid-template-columns: 1fr;
  }

  .hero-band {
    display: grid;
  }

  .wide-field {
    grid-column: auto;
  }
}
</style>
